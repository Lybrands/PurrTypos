"""Replacement-only Screenplay durable continuation and restart recovery."""

from __future__ import annotations

from dataclasses import replace

from agents.screenplay.contracts import (
    SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
    SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
)
from agents.screenplay.conversation_projection import (
    ScreenplayReplacementConversationStore,
)
from agents.screenplay.recipe import ScreenplayHostRecipeSpec
from agents.shared.implementation import (
    AgentKind,
    REPLACEMENT_IMPLEMENTATION_ID,
    replacement_implementation,
)
from agents.shared.implementation_registry import AgentLifecycleAction
from agents.shared.saved_model_binding import (
    capture_saved_model_binding,
    resolve_saved_model_runtime,
)
from infrastructure.persistence.run_execution_store import now_ms
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from purra.api import DurableTaskContinuation
from purra.contracts import RunStatus, StepStatus
from purra.json_values import thaw_json_mapping
from purra.long_tasks import LongTaskStatus
from purra.run_recovery import RunRecoverySnapshot
from purra.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    TaskAdmissionDecision,
)


_LEASE_MS = 30_000


class ScreenplayReplacementRecoveryError(RuntimeError):
    code = "screenplay_replacement_recovery_invalid"


class ScreenplayReplacementContinuationLifecycle:
    def __init__(
        self,
        db,
        repository,
        *,
        turn_id: str,
        operation_id: str,
        task_id: str,
        source_run_id: str,
        expected_operation_revision: int,
        owner_id: str,
        continuation_epoch: int,
        continuation_identity_digest: str,
    ) -> None:
        self._db = db
        self._tasks = repository
        self._turn_id = str(turn_id)
        self._operation_id = str(operation_id)
        self._task_id = str(task_id)
        self._source_run_id = str(source_run_id)
        self._expected_revision = int(expected_operation_revision)
        self._owner_id = str(owner_id)
        self._continuation_epoch = int(continuation_epoch)
        self._continuation_identity_digest = str(continuation_identity_digest)
        self._attempt: int | None = None

    async def validate(self) -> None:
        row = await self._state()
        task = await self._tasks.load(self._task_id)
        if (
            row is None
            or row["turn_status"] != "paused"
            or row["operation_status"] != "paused"
            or int(row["operation_revision"]) != self._expected_revision
            or row["root_run_id"] != self._source_run_id
            or row["long_task_id"] != self._task_id
            or row.get("turn_cancel_requested_at_ms") is not None
            or row.get("operation_cancel_requested_at_ms") is not None
            or task is None
            or task.status is not LongTaskStatus.PAUSED
            or task.cancellation_requested_at_ms is not None
        ):
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation state changed"
            )
        self._attempt = int(row.get("turn_attempt") or 0) + 1

    def run_binding_attributes(self) -> dict[str, object]:
        if self._attempt is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation lifecycle is not validated"
            )
        return {
            "turnExecutionOwner": self._owner_id,
            "turnAttempt": self._attempt,
            "continuationOf": self._source_run_id,
            "operationId": self._operation_id,
            "continuationOwner": self._owner_id,
            "continuationEpoch": self._continuation_epoch,
            "continuationIdentityDigest": self._continuation_identity_digest,
        }

    async def before_submit(self) -> None:
        if self._attempt is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation lifecycle is not validated"
            )
        current = now_ms()
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'planning', "
                "execution_owner_id = ?, lease_expires_at_ms = ?, "
                "heartbeat_at_ms = ?, attempt = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'paused' AND planner_run_id = ? "
                "AND attempt = ? AND cancel_requested_at_ms IS NULL",
                [
                    self._owner_id, current + _LEASE_MS, current, self._attempt,
                    self._turn_id, self._source_run_id, self._attempt - 1,
                ],
            )
            if await _changes(self._db) != 1:
                raise ScreenplayReplacementRecoveryError(
                    "Screenplay continuation claim was lost"
                )
        await self._tasks.resume(self._task_id, recovery_source="user")

    async def on_run_started(self, run_id: str) -> None:
        row = await self._state()
        if (
            row is None
            or row["turn_status"] != "running"
            or row["operation_status"] != "running"
            or row["root_run_id"] != run_id
        ):
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation Root binding is missing"
            )

    async def on_run_finished(self, result) -> None:
        row = await self._state()
        allowed = {
            RunStatus.DONE: {"completed"},
            RunStatus.FAILED: {"failed", "paused"},
            RunStatus.CANCELED: {"canceled", "failed", "paused"},
            RunStatus.BLOCKED: {"failed", "paused"},
        }.get(result.status)
        if row is None or allowed is None or row["turn_status"] not in allowed:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation terminal projection is missing"
            )

    async def on_start_failed(self, code: str) -> None:
        state = await self._state()
        owns_pre_run_claim = bool(
            state is not None
            and state["turn_status"] == "planning"
            and state.get("root_run_id") == self._source_run_id
            and state.get("execution_owner_id") == self._owner_id
            and int(state.get("turn_attempt") or 0) == int(self._attempt or -1)
        )
        if owns_pre_run_claim:
            task = await self._tasks.load(self._task_id)
            if task is not None and task.status is LongTaskStatus.RUNNING:
                await self._tasks.pause(
                    self._task_id,
                    reason_code=str(code or "continuation_start_failed"),
                )
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'paused', "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'planning' "
                "AND execution_owner_id = ? AND attempt = ?",
                [self._turn_id, self._owner_id, int(self._attempt or -1)],
            )

    async def _state(self):
        return await self._db.fetch_one(
            "SELECT t.status AS turn_status, t.attempt AS turn_attempt, "
            "t.execution_owner_id, "
            "t.planner_run_id AS root_run_id, t.cancel_requested_at_ms AS "
            "turn_cancel_requested_at_ms, o.status AS operation_status, "
            "o.revision AS operation_revision, o.long_task_id, "
            "o.cancel_requested_at_ms AS operation_cancel_requested_at_ms "
            "FROM screenplay_agent_turns AS t JOIN screenplay_agent_operations AS o "
            "ON o.turn_id = t.id WHERE t.id = ? AND o.id = ?",
            [self._turn_id, self._operation_id],
        )


class ScreenplayReplacementRecoveryService:
    def __init__(self, db, composition, *, entry_service=None) -> None:
        self._db = db
        self._composition = composition
        self._entry = entry_service
        self._tasks = composition.long_task_repository
        self._runs = SqliteRunRepository(db)
        self._turns = ScreenplayReplacementConversationStore(
            db, owner_id=composition.execution_owner_id
        )

    async def resolve_automatic_runtime(self, operation_id: str):
        operation = await self._require_operation(operation_id)
        requirements = _mapping(operation.get("requirements_json"))
        binding = requirements.get("runtimeBinding")
        if not isinstance(binding, dict):
            return None
        return await resolve_saved_model_runtime(self._db, binding)

    async def resume(
        self,
        *,
        operation_id: str,
        run_command_id: str,
        expected_operation_revision: int,
        runtime,
        signal,
        prompt: str = "继续已暂停的剧本任务。",
        reserve_only: bool = False,
        execute_reserved: bool = False,
    ):
        operation = await self._require_operation(operation_id)
        turn = await self._require_turn(str(operation["turn_id"]))
        task_id = str(operation.get("long_task_id") or "").strip()
        prior_command = await self._turns.load_resume_reservation(
            str(run_command_id)
        )
        source_run_id = str(
            (prior_command or {}).get("continuation_source_root_run_id")
            or turn.get("root_run_id")
            or ""
        ).strip()
        if not task_id or not source_run_id:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay paused Operation has no durable source"
            )
        await self._require_replacement_owner(source_run_id)
        source = await self._runs.get(source_run_id)
        if source.status is not RunStatus.CANCELED or source.execution_plan is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation requires a canceled source Root plan"
            )
        requirements = _mapping(operation.get("requirements_json"))
        spec = ScreenplayHostRecipeSpec.from_mapping(requirements.get("hostRecipe"))
        task = await self._tasks.load(task_id)
        if task is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay replacement Task does not exist"
            )
        metadata = thaw_json_mapping(task.metadata)
        plan = source.execution_plan
        recipe = spec.compile(
            project_id=str(operation["project_id"]),
            plan_step_ids=tuple(plan.work_step_ids or ()),
        )
        if (
            task.namespace != SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE
            or task.kind != recipe.kind
            or metadata.get("recipeDigest") != recipe.metadata["recipeDigest"]
            or int(metadata.get("recipeVersion") or 0)
            != SCREENPLAY_REPLACEMENT_RECIPE_VERSION
            or metadata.get("commandId") != turn.get("command_id")
            or metadata.get("projectId") != operation.get("project_id")
        ):
            raise ScreenplayReplacementRecoveryError(
                "Screenplay persisted recipe no longer matches its product scope"
            )
        requested_binding = await capture_saved_model_binding(self._db, runtime)
        persisted_binding = requirements.get("runtimeBinding")
        if requested_binding is None or requested_binding != persisted_binding:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation model binding changed"
            )
        units = await self._tasks.list_units(task.id)
        completed = {unit.id for unit in units if unit.status.value == "completed"}
        continuation_plan = replace(
            plan,
            steps=tuple(
                replace(
                    step,
                    status=(
                        StepStatus.DONE
                        if all(
                            unit.id in completed
                            for unit in recipe.steps
                            if unit.plan_step_id == step.id
                        )
                        else StepStatus.PENDING
                    ),
                    result_summary=None,
                    error=None,
                )
                for step in plan.steps
            ),
        )
        admission = TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_replacement_continuation",
            estimated_units=len(recipe.steps),
            estimated_model_calls=sum(
                1 for item in recipe.steps
                if str(item.metadata.get("completion") or "") != "host_only"
            ),
            covered_step_ids=tuple(continuation_plan.work_step_ids or ()),
            execution_recipe=recipe,
            metadata=metadata,
        )
        continuation = DurableTaskContinuation(
            source=RunRecoverySnapshot(
                run_id=source.run_id,
                status=source.status,
                execution_plan=continuation_plan,
                agent_preset_snapshot=source.agent_preset_snapshot,
            ),
            continuation_command=str(run_command_id),
            receipt=LongTaskDispatchReceipt(
                task_id=task.id,
                message="恢复 replacement 剧本任务。",
                admission=admission,
            ),
        )
        reservation = await self._turns.reserve_resume(
            operation_id=str(operation["id"]),
            command_id=str(run_command_id),
            expected_operation_revision=expected_operation_revision,
            runtime_binding=persisted_binding,
            turn_id=str(turn["id"]),
            source_run_id=source_run_id,
            project_id=str(turn["project_id"]),
            session_id=int(turn["session_id"]),
        )
        if not reservation["dispatchRequired"]:
            command = await self._turns.load_resume_reservation(str(run_command_id))
            can_execute_reserved = bool(
                execute_reserved
                and command is not None
                and command.get("continuation_status") == "starting"
                and command.get("continuation_owner_id")
                == self._composition.execution_owner_id
            )
            if not can_execute_reserved:
                yield reservation
                return
        if reserve_only:
            yield reservation
            return
        try:
            command = await self._turns.load_resume_reservation(
                str(run_command_id)
            )
            if command is None:
                raise ScreenplayReplacementRecoveryError(
                    "Screenplay continuation reservation is missing"
                )
            lifecycle = ScreenplayReplacementContinuationLifecycle(
                self._db,
                self._tasks,
                turn_id=str(turn["id"]),
                operation_id=str(operation["id"]),
                task_id=task.id,
                source_run_id=source_run_id,
                expected_operation_revision=expected_operation_revision,
                owner_id=self._composition.execution_owner_id,
                continuation_epoch=int(command.get("continuation_epoch") or 0),
                continuation_identity_digest=str(
                    command.get("continuation_identity_digest") or ""
                ),
            )
            await lifecycle.validate()
            if self._entry is None:
                raise ScreenplayReplacementRecoveryError(
                    "Screenplay continuation executor is unavailable"
                )
            async for update in self._entry.continue_task(
                durable_continuation=continuation,
                task_command_id=str(turn["command_id"]),
                run_command_id=str(run_command_id),
                project_id=str(turn["project_id"]),
                session_id=int(turn["session_id"]),
                turn_id=str(turn["id"]),
                prompt=prompt,
                stage_command=None,
                runtime=runtime,
                host_recipe=spec,
                expected_runtime_binding=persisted_binding,
                failed_resume_attempts=0,
                signal=signal,
                run_binding_lifecycle=lifecycle,
            ):
                yield update
        except BaseException:
            await self._turns.release_resume_reservation(str(run_command_id))
            raise

    async def resume_reserved(
        self,
        *,
        operation_id: str,
        run_command_id: str,
        runtime,
        signal,
    ):
        command = await self._turns.load_resume_reservation(run_command_id)
        if command is None or command.get("continuation_status") != "starting":
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation reservation is not executable"
            )
        response = _mapping(command.get("response_json"))
        expected_revision = response.get("revision")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay continuation revision is missing"
            )
        async for update in self.resume(
            operation_id=operation_id,
            run_command_id=run_command_id,
            expected_operation_revision=expected_revision,
            runtime=runtime,
            signal=signal,
            execute_reserved=True,
        ):
            yield update

    async def recover_stale_admissions(self, *, timestamp_ms: int | None = None):
        """Recover only pre-Run claims; PurrA owns every persisted Root orphan."""

        cutoff = now_ms() if timestamp_ms is None else int(timestamp_ms)
        recovered: list[str] = []
        rows = await self._db.fetch_all(
            "SELECT t.id, t.operation_id, "
            "t.planner_run_id AS root_run_id, "
            "o.status AS operation_status, o.long_task_id "
            "FROM screenplay_agent_turns AS t "
            "JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "LEFT JOIN ai_agent_runs AS r ON r.id = t.planner_run_id "
            "WHERE t.status = 'planning' "
            "AND t.implementation_id = ? "
            "AND (t.planner_run_id IS NULL OR r.status <> 'running') "
            "AND t.lease_expires_at_ms IS NOT NULL "
            "AND t.lease_expires_at_ms <= ?",
            [REPLACEMENT_IMPLEMENTATION_ID, cutoff],
        )
        for row in rows:
            if row.get("long_task_id"):
                task = await self._tasks.load(str(row["long_task_id"]))
                if task is not None and task.status is LongTaskStatus.RUNNING:
                    # Pause the Task first. A crash after this point leaves the
                    # still-planning Turn discoverable by the next recovery.
                    await self._tasks.pause(
                        task.id, reason_code="continuation_start_interrupted"
                    )
            async with self._db.transaction(cancellation_linearizable=True):
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET status = CASE "
                    "WHEN cancel_requested_at_ms IS NOT NULL THEN 'canceled' "
                    "WHEN ? = 'paused' THEN 'paused' "
                    "ELSE 'queued' "
                    "END, execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                    "AND status = 'planning' "
                    "AND lease_expires_at_ms <= ?",
                    [row["operation_status"], row["id"], cutoff],
                )
                if await _changes(self._db) == 1:
                    recovered.append(str(row["id"]))
        return tuple(recovered)

    async def _require_replacement_owner(self, run_id: str) -> None:
        route = await self._composition.agent_implementation_router.for_run(
            run_id,
            action=AgentLifecycleAction.RESUME,
            expected_agent_kind=AgentKind.SCREENPLAY,
        )
        if route.identity != replacement_implementation(
            AgentKind.SCREENPLAY,
            recipe_version=SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
        ):
            raise ScreenplayReplacementRecoveryError(
                "Screenplay task belongs to a different implementation"
            )

    async def _require_operation(self, operation_id: str):
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE id = ?",
            [str(operation_id or "").strip()],
        )
        if row is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay replacement Operation does not exist"
            )
        return row

    async def _require_turn(self, turn_id: str):
        row = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ?",
            [turn_id],
        )
        if row is None:
            raise ScreenplayReplacementRecoveryError(
                "Screenplay replacement Turn does not exist"
            )
        return row


def _mapping(value):
    import json

    try:
        result = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(result) if isinstance(result, dict) else {}


async def _changes(db) -> int:
    row = await db.fetch_one("SELECT changes() AS count")
    return int((row or {}).get("count") or 0)


__all__ = [
    "ScreenplayReplacementContinuationLifecycle",
    "ScreenplayReplacementRecoveryError",
    "ScreenplayReplacementRecoveryService",
]
