"""Screenplay product extension from Root TaskSpec to durable execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import replace
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from application.agent_profile_registry import AgentProfileRegistration
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_manifest_compiler import compile_screenplay_manifest
from application.screenplay_task_resolver import (
    ResolvedScreenplayTask,
    SqliteScreenplayTaskResolver,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from application.screenplay_checkpoint_planning import (
    ScreenplayCheckpointInput,
    ScreenplayCheckpointOutcome,
    ScreenplayCheckpointStateError,
    SqliteScreenplayCheckpointRepository,
    _plan_mapping,
    parse_persisted_plan,
    plan_digest,
)
from domains.screenplay_agent.adapter import (
    ScreenplayDomainAdapter,
    ScreenplayHostContextProvider,
)
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
    ScreenplayAgentDomainContext,
)
from domains.screenplay_agent.contracts import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayStageCommand,
)
from domains.screenplay_agent.operation import ScreenplayOperationCreateCommand
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.screenplay import (
    build_screenplay_tool_catalog,
)
from purra.contracts import AgentRunRequest, TaskPlan
from purra.contracts import StepStatus
from purra.events import AgentEvent, CoreEventType
from purra.long_tasks import (
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    RecipeLongTaskDispatcher,
)
from purra.task_admission import ExecutionMode, TaskAdmissionDecision
from purra.task_admission import LongTaskExecutionStatus
from purra.json_values import thaw_json_mapping


class ScreenplayAgentProfileExtension:
    def __init__(
        self,
        db,
        *,
        owner_id: str | None = None,
        resolver=None,
        candidate_normalizer=None,
    ) -> None:
        self._owner_id = str(owner_id or "").strip() or (
            f"screenplay-profile-{uuid4().hex}"
        )
        self._db = db
        self._turns = SqliteScreenplayAgentRepository(
            db,
            owner_id=self._owner_id,
        )
        self._operations = SqliteScreenplayOperationRepository(db)
        self._projects = ScreenplayV2ProjectService(db)
        self._resolver = resolver or SqliteScreenplayTaskResolver(db)
        context_query = ScreenplayAgentContextQuery(db)

        async def load_planning_context(project_id: str):
            workspace = await self._projects.get_workspace(project_id)
            return await context_query.planning_context(workspace)

        self._adapter = ScreenplayDomainAdapter(
            tool_catalog=build_screenplay_tool_catalog(
                db=db,
                candidate_normalizer=candidate_normalizer,
            ),
            context_provider=ScreenplayHostContextProvider(
                planning_context_loader=load_planning_context,
            ),
        )

    def profile_registration(self) -> AgentProfileRegistration:
        return AgentProfileRegistration(
            id="screenplay",
            domain_namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            adapter=self._adapter,
        )

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if context.is_child:
            return request
        turn = await self._turns.load_turn(str(context.turn_id))
        if turn is None:
            raise ValueError("screenplay Root Run requires a persisted Turn")
        if (
            turn["projectId"] != context.project_id
            or request.session_id is None
            or int(turn["sessionId"]) != int(request.session_id)
            or request.latest_user_text() != turn["userContent"]
        ):
            raise ValueError("screenplay Root Run scope does not match its Turn")
        workspace = await self._projects.get_workspace(context.project_id)
        source = (workspace.get("project") or {}).get("source") or {}
        stage_command = turn.get("stageCommand")
        hydrated = replace(
            context,
            stage_command=(
                ScreenplayStageCommand.from_mapping(stage_command)
                if stage_command is not None
                else None
            ),
            source_book_id=str(source.get("bookId") or "") or None,
            source_scope=dict(source.get("scope") or {}),
        )
        return replace(request, domain_context=hydrated.to_core_context())

    def context_provider_factory(self):
        return None

    def response_judge_policies(self, request):
        del request
        return ()

    def task_admission(self):
        return self

    async def evaluate(self, request, plan, signal=None):
        del signal
        if not isinstance(plan, TaskPlan) or plan.task_spec is None:
            raise ValueError("screenplay admission requires a planned TaskSpec")
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if not context.is_root:
            return TaskAdmissionDecision()
        intent = ScreenplayIntent.from_task_spec(plan.task_spec, plan.steps)
        if context.stage_command is not None:
            context.stage_command.require_compatible(intent)
        await self._turns.record_admitted_intent(
            str(context.turn_id),
            intent=intent,
        )
        if intent.action is ScreenplayIntentAction.ANSWER:
            return TaskAdmissionDecision(
                mode=ExecutionMode.INLINE,
                reason_code="screenplay_answer_runs_inline",
            )
        workspace = await self._projects.get_workspace(context.project_id)
        resolved: ResolvedScreenplayTask = await self._resolver.resolve(
            workspace=workspace,
            intent=intent,
        )
        compiled = compile_screenplay_manifest(
            intent=intent,
            target_role=resolved.target_role,
            source_revision_refs=resolved.source_revision_refs,
            episode_scene_ids=resolved.episode_scene_ids,
            reviewed_draft_id=resolved.reviewed_draft_id,
            base_revision_id=resolved.base_revision_id,
            document_sections=resolved.document_sections,
            original_request=request.latest_user_text(),
            plan_bindings=intent.plan_bindings,
            plan_steps=plan.steps,
        )
        turn = await self._turns.load_turn(str(context.turn_id))
        if turn is None:
            raise ValueError("screenplay admission Turn disappeared")
        requirements = {
            "intent": intent.to_mapping(),
            "targetRole": compiled.target_role,
            "episodeNumbers": list(resolved.episode_numbers),
            "baseRevisionId": resolved.base_revision_id,
            "sourceRevisionRefs": list(resolved.source_revision_refs),
            "manifestId": compiled.manifest.id,
            "manifest": {
                "artifactKind": compiled.manifest.artifact_kind,
                "assemblyStrategy": compiled.manifest.assembly_strategy,
                "partSemanticKeys": [
                    part.semantic_key for part in compiled.manifest.parts
                ],
            },
            "recipe": compiled.recipe.to_metadata(),
        }
        operation = await self._operations.create(
            ScreenplayOperationCreateCommand(
                turn_id=str(context.turn_id),
                project_id=context.project_id,
                session_id=int(turn["sessionId"]),
                target_role=compiled.target_role,
                requirements_json=requirements,
                manifest_digest=compiled.manifest.digest,
            )
        )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_formal_task_requires_durable_execution",
            estimated_units=len(compiled.recipe.steps),
            estimated_model_calls=sum(
                step.kind.startswith("generate_")
                or step.kind == "compose_final_response"
                for step in compiled.recipe.steps
            ),
            covered_step_ids=tuple(step.id for step in plan.steps),
            execution_recipe=compiled.recipe,
            metadata={
                "operationId": operation.id,
                "commandId": str(turn["commandId"]),
                "projectId": context.project_id,
                "sessionId": int(turn["sessionId"]),
                "turnId": str(context.turn_id),
                "targetRole": compiled.target_role,
                "manifestId": compiled.manifest.id,
                "manifestDigest": compiled.manifest.digest,
                "sourceRevisionRefs": list(resolved.source_revision_refs),
                "baseRevisionId": resolved.base_revision_id,
                "screenplayScope": intent.scope.to_mapping(),
                "screenplayAction": intent.action.value,
                "requestedDeliverable": intent.requested_deliverable,
                "originalPlan": _plan_mapping(plan),
            },
        )

    def create_long_task_dispatcher(
        self,
        *,
        work_item_repository=None,
        long_task_repository=None,
        executor=None,
    ):
        if executor is None:
            return None
        if work_item_repository is None or long_task_repository is None:
            raise ValueError("screenplay durable repositories are required")
        return _ScreenplayRecipeLongTaskDispatcher(
            db=self._db,
            operations=self._operations,
            turns=self._turns,
            work_item_repository=work_item_repository,
            long_task_repository=long_task_repository,
            descriptor_resolver=_ScreenplayTaskDescriptorResolver(),
            executor_registry=DurableExecutorRegistry({"screenplay": executor}),
            worker_id=self._owner_id,
            checkpoint_planner=getattr(executor, "checkpoint_planner", None),
        )

    def clear_active_executions(self) -> None:
        return None


class _ScreenplayTaskDescriptorResolver:
    async def resolve(self, request, plan, decision):
        del request, plan
        metadata = decision.metadata
        return DurableTaskDescriptor(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            owner_id=str(metadata["projectId"]),
            idempotency_key=str(metadata["commandId"]),
            metadata={
                key: metadata[key]
                for key in (
                    "operationId",
                    "projectId",
                    "sessionId",
                    "turnId",
                    "targetRole",
                    "manifestId",
                    "manifestDigest",
                    "sourceRevisionRefs",
                    "baseRevisionId",
                    "screenplayScope",
                    "screenplayAction",
                    "requestedDeliverable",
                    "originalPlan",
                )
            },
        )


class _ScreenplayRecipeLongTaskDispatcher(RecipeLongTaskDispatcher):
    def __init__(
        self,
        *,
        db,
        operations,
        turns,
        checkpoint_planner=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._db = db
        self._operations = operations
        self._turns = turns
        self._checkpoint_planner = checkpoint_planner
        self._checkpoints = SqliteScreenplayCheckpointRepository(db)

    async def dispatch(self, request, plan, decision, **kwargs):
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = await super().dispatch(request, plan, decision, **kwargs)
            operation_id = str(decision.metadata.get("operationId") or "")
            await self._operations.attach_long_task(
                operation_id,
                long_task_id=receipt.task_id,
                command_id=f"operation:dispatch:{operation_id}:{receipt.task_id}",
            )
            await self._turns.attach_operation(
                str(decision.metadata.get("turnId") or ""),
                operation_id=operation_id,
                task_id=receipt.task_id,
                target_role=str(decision.metadata.get("targetRole") or ""),
                root_run_id=str(kwargs.get("parent_run_id") or ""),
            )
        return receipt

    async def execute(self, task_id, *, parent_run_id, observer, signal=None):
        checkpoint_observer = (
            _ScreenplayCheckpointObserver(
                dispatcher=self,
                planner=self._checkpoint_planner,
                downstream=observer,
                task_id=task_id,
                root_run_id=parent_run_id,
                signal=signal,
            )
            if self._checkpoint_planner is not None
            else observer
        )
        try:
            result = await super().execute(
                task_id,
                parent_run_id=parent_run_id,
                observer=checkpoint_observer,
                signal=signal,
            )
            await self._settle_execution(task_id, result)
            return result
        except Exception as error:
            await self._settle_exception(task_id, error)
            raise

    async def _settle_execution(self, task_id, result) -> None:
        await _settle_screenplay_execution(self, task_id, result)

    async def _settle_exception(self, task_id: str, error: Exception) -> None:
        await _settle_screenplay_exception(self, task_id, error)


class _ScreenplayCheckpointObserver:
    def __init__(
        self,
        *,
        dispatcher,
        planner,
        downstream,
        task_id,
        root_run_id,
        signal,
    ) -> None:
        self._dispatcher = dispatcher
        self._planner = planner
        self._downstream = downstream
        self._task_id = task_id
        self._root_run_id = root_run_id
        self._signal = signal

    async def __call__(self, update):
        await self._downstream(update)
        if update.event.type != CoreEventType.LONG_TASK_PROGRESS:
            return
        task = await self._dispatcher._long_tasks.load(self._task_id)
        if task is None or task.status.value not in {"running", "pending"}:
            return
        metadata = thaw_json_mapping(task.metadata)
        operation_id = str(metadata.get("operationId") or "")
        units = tuple(await self._dispatcher._long_tasks.list_units(task.id))
        for checkpoint_key in _ready_checkpoint_keys(units):
            await self._handle_checkpoint(
                task,
                units,
                metadata,
                operation_id,
                checkpoint_key,
                update,
            )
            refreshed = await self._dispatcher._long_tasks.load(task.id)
            if refreshed is None or refreshed.status.value == "paused":
                return

    async def _handle_checkpoint(
        self,
        task,
        units,
        metadata,
        operation_id,
        checkpoint_key,
        progress_update,
    ) -> None:
        existing = await self._dispatcher._checkpoints.load(
            operation_id,
            checkpoint_key,
        )
        if existing is not None and str(existing["status"]) == "applied":
            return
        if existing is not None and str(existing["status"]) == "paused":
            await self._dispatcher._long_tasks.pause(task.id)
            return
        try:
            original = await self._dispatcher._checkpoints.load_root_plan(
                self._root_run_id,
                initial=True,
            )
            current = await self._dispatcher._checkpoints.load_root_plan(
                self._root_run_id,
            )
        except ScreenplayCheckpointStateError:
            await self._pause_without_planning(
                task,
                operation_id,
                checkpoint_key,
            )
            return
        checkpoint_input = _checkpoint_input(
            task,
            units,
            metadata,
            checkpoint_key,
            original,
            current,
        )
        input_digest = _checkpoint_input_digest(checkpoint_input)
        if existing is not None and str(existing["status"]) in {
            "ready", "applying",
        }:
            try:
                root_digest = (
                    await self._dispatcher._checkpoints.root_revision_digest(
                        self._root_run_id,
                        checkpoint_key,
                        expected_digest=str(existing["plan_digest"]),
                    )
                )
            except ScreenplayCheckpointStateError:
                await self._pause_stale_ready(task, existing)
                return
            if root_digest is not None:
                await self._emit_ready(existing, progress_update, task=task)
                return
            if str(existing["input_digest"]) != input_digest:
                await self._pause_stale_ready(task, existing)
                return
            await self._emit_ready(existing, progress_update, task=task)
            return
        reservation_token = uuid4().hex
        receipt = await self._dispatcher._checkpoints.acquire_planning(
            operation_id=operation_id,
            task_id=task.id,
            checkpoint_key=checkpoint_key,
            root_run_id=self._root_run_id,
            input_digest=input_digest,
            reservation_token=reservation_token,
            signal=self._signal,
        )
        if str(receipt["status"]) in {"ready", "applying"}:
            await self._emit_ready(receipt, progress_update, task=task)
            return
        if str(receipt["status"]) != "reserved":
            if str(receipt["status"]) == "paused":
                await self._dispatcher._long_tasks.pause(task.id)
            return
        decision = await self._with_heartbeat(
            receipt,
            "reserved",
            self._planner.revise(checkpoint_input, self._signal),
        )
        if decision.outcome in {
            ScreenplayCheckpointOutcome.PAUSED,
            ScreenplayCheckpointOutcome.REQUIRES_RERESOLUTION,
        }:
            await self._dispatcher._checkpoints.pause(
                operation_id=operation_id,
                checkpoint_key=checkpoint_key,
                outcome=decision.outcome,
                code=decision.code or decision.outcome.value,
                reservation_owner=reservation_token,
                reservation_epoch=int(receipt["reservation_epoch"]),
            )
            await self._dispatcher._long_tasks.pause(task.id)
            return
        revised = decision.plan or current
        receipt = await self._dispatcher._checkpoints.ready(
            operation_id=operation_id,
            checkpoint_key=checkpoint_key,
            plan=revised,
            outcome=decision.outcome,
            reservation_owner=reservation_token,
            reservation_epoch=int(receipt["reservation_epoch"]),
        )
        await self._emit_ready(receipt, progress_update, task=task)

    async def _pause_without_planning(
        self,
        task,
        operation_id: str,
        checkpoint_key: str,
    ) -> None:
        input_digest = "sha256:" + hashlib.sha256(json.dumps(
            {
                "checkpoint": checkpoint_key,
                "rootRunId": self._root_run_id,
                "error": "checkpoint_root_plan_unavailable",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        reservation_token = uuid4().hex
        receipt = await self._dispatcher._checkpoints.acquire_planning(
            operation_id=operation_id,
            task_id=task.id,
            checkpoint_key=checkpoint_key,
            root_run_id=self._root_run_id,
            input_digest=input_digest,
            reservation_token=reservation_token,
            signal=self._signal,
        )
        if str(receipt["status"]) == "reserved" and receipt.get("_acquired"):
            await self._dispatcher._checkpoints.pause(
                operation_id=operation_id,
                checkpoint_key=checkpoint_key,
                outcome=ScreenplayCheckpointOutcome.PAUSED,
                code="checkpoint_root_plan_unavailable",
                reservation_owner=reservation_token,
                reservation_epoch=int(receipt["reservation_epoch"]),
            )
        await self._dispatcher._long_tasks.pause(task.id)

    async def _pause_stale_ready(self, task, receipt) -> None:
        status = str(receipt["status"])
        await self._dispatcher._checkpoints.pause_ready_conflict(
            operation_id=str(receipt["operation_id"]),
            checkpoint_key=str(receipt["checkpoint_key"]),
            code="screenplay_checkpoint_ready_root_plan_conflict",
            expected_plan_digest=str(receipt["plan_digest"]),
            reservation_owner=(
                str(receipt.get("reservation_owner") or "")
                if status == "applying" else None
            ),
            reservation_epoch=(
                int(receipt.get("reservation_epoch") or 0)
                if status == "applying" else None
            ),
        )
        await self._dispatcher._long_tasks.pause(task.id)

    async def _emit_ready(self, receipt, progress_update, *, task=None) -> None:
        checkpoint_key = str(receipt["checkpoint_key"])
        digest = str(receipt["plan_digest"])
        operation_id = str(receipt["operation_id"])
        owner: str | None = None
        epoch: int | None = None
        try:
            root_digest = await self._dispatcher._checkpoints.root_revision_digest(
                self._root_run_id,
                checkpoint_key,
                expected_digest=digest,
            )
            applying = await self._dispatcher._checkpoints.acquire_applying(
                operation_id=operation_id,
                checkpoint_key=checkpoint_key,
                digest=digest,
                reservation_token=uuid4().hex,
                signal=self._signal,
            )
            if str(applying["status"]) in {"applied", "paused"}:
                return
            owner = str(applying["reservation_owner"])
            epoch = int(applying["reservation_epoch"])

            async def apply_revision() -> None:
                # The prior apply owner may have committed the Root event and
                # crashed before acknowledging the receipt. Reconcile before
                # any re-emission.
                root_digest = (
                    await self._dispatcher._checkpoints.root_revision_digest(
                        self._root_run_id,
                        checkpoint_key,
                        expected_digest=digest,
                    )
                )
                if root_digest is None:
                    event = AgentEvent(
                        type=CoreEventType.LONG_TASK_PROGRESS,
                        run_id=self._root_run_id,
                        payload={
                            **thaw_json_mapping(progress_update.event.payload),
                            "checkpoint": {
                                "identity": checkpoint_key,
                                "digest": digest,
                            },
                        },
                    )
                    await self._downstream(type(progress_update)(
                        event=event,
                        plan_revision=parse_persisted_plan(
                            str(receipt["plan_json"])
                        ),
                        plan_revision_metadata={
                            "identity": checkpoint_key,
                            "digest": digest,
                        },
                    ))
                    root_digest = (
                        await self._dispatcher._checkpoints.root_revision_digest(
                            self._root_run_id,
                            checkpoint_key,
                            expected_digest=digest,
                        )
                    )
                if root_digest != digest:
                    raise ScreenplayCheckpointStateError(
                        "checkpoint Root revision ACK is missing"
                    )
                await self._dispatcher._checkpoints.applied(
                    operation_id=operation_id,
                    checkpoint_key=checkpoint_key,
                    digest=digest,
                    reservation_owner=owner,
                    reservation_epoch=epoch,
                )

            await self._with_heartbeat(
                applying,
                "applying",
                apply_revision(),
            )
        except ScreenplayCheckpointStateError:
            await self._dispatcher._checkpoints.pause_ready_conflict(
                operation_id=operation_id,
                checkpoint_key=checkpoint_key,
                code="screenplay_checkpoint_ready_root_plan_conflict",
                expected_plan_digest=digest,
                reservation_owner=owner,
                reservation_epoch=epoch,
            )
            if task is not None:
                await self._dispatcher._long_tasks.pause(task.id)

    async def _with_heartbeat(self, receipt, status: str, awaitable):
        owner = str(receipt["reservation_owner"])
        epoch = int(receipt["reservation_epoch"])
        operation_id = str(receipt["operation_id"])
        checkpoint_key = str(receipt["checkpoint_key"])

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(
                    self._dispatcher._checkpoints.heartbeat_interval_seconds
                )
                await self._dispatcher._checkpoints.renew(
                    operation_id=operation_id,
                    checkpoint_key=checkpoint_key,
                    reservation_owner=owner,
                    reservation_epoch=epoch,
                    status=status,
                )

        worker = asyncio.create_task(awaitable)
        keeper = asyncio.create_task(heartbeat())
        try:
            done, _pending = await asyncio.wait(
                {worker, keeper},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker in done:
                return worker.result()
            return keeper.result()
        finally:
            for child in (worker, keeper):
                if not child.done():
                    child.cancel()
            for child in (worker, keeper):
                with suppress(asyncio.CancelledError, Exception):
                    await child

def _ready_checkpoint_keys(units) -> tuple[str, ...]:
    ready: list[tuple[int, str]] = []
    review_validations = []
    for unit in units:
        metadata = thaw_json_mapping(unit.metadata)
        unit_input = metadata.get("input") or {}
        if (
            str(metadata.get("unitKind") or "") != "validate_manifest_part"
            or not isinstance(unit_input, Mapping)
        ):
            continue
        kind = str(unit_input.get("validationKind") or "")
        if kind == "review_episode":
            review_validations.append(unit)
        if unit.status.value != "completed":
            continue
        if kind == "draft_episode":
            ready.append((unit.position, f"episode:{int(unit_input['episodeNumber'])}"))
        elif kind == "document":
            ready.append((unit.position, "document:sections"))
    if review_validations and all(
        unit.status.value == "completed" for unit in review_validations
    ):
        ready.append((
            max(unit.position for unit in review_validations),
            "review:aggregate",
        ))
    return tuple(key for _position, key in sorted(ready))


def _checkpoint_input(
    task,
    units,
    metadata,
    checkpoint_key,
    original,
    current,
) -> ScreenplayCheckpointInput:
    completed = tuple({
        "stepId": step.id,
        "title": step.title,
        "summary": step.result_summary or "completed",
    } for step in current.steps if step.status is StepStatus.DONE)
    receipts = []
    completed_episodes: set[int] = set()
    completed_sections: set[str] = set()
    for unit in units:
        if unit.status.value != "completed" or not unit.artifact_digest:
            continue
        raw = thaw_json_mapping(unit.metadata)
        unit_input = raw.get("input") or {}
        if not isinstance(unit_input, Mapping):
            continue
        unit_kind = str(raw.get("unitKind") or "")
        part_kind = {
            "collect_evidence": "evidence",
            "generate_draft_scene": "draftScene",
            "compose_episode_metadata": "episodeMetadata",
            "generate_review_dimension": "reviewDimension",
            "generate_document_section": "documentSection",
            "validate_manifest_part": "validation",
        }.get(unit_kind)
        if part_kind is None:
            continue
        receipt: dict[str, Any] = {
            "partKind": part_kind,
            "digest": unit.artifact_digest,
            "status": "completed",
        }
        if unit_kind == "validate_manifest_part":
            artifact_kind = str(unit_input.get("validationKind") or "").strip()
            if artifact_kind in {"draft_episode", "review_episode", "document"}:
                receipt["artifactKind"] = artifact_kind
        episode = int(unit_input.get("episodeNumber") or 0)
        section = str(unit_input.get("sectionKey") or "").strip()
        if episode:
            receipt["episodeNumber"] = episode
        if section:
            receipt["sectionKey"] = section
        receipts.append(receipt)
        if (
            raw.get("unitKind") == "validate_manifest_part"
            and unit_input.get("validationKind") in {"draft_episode", "review_episode"}
            and episode
        ):
            completed_episodes.add(episode)
        if raw.get("unitKind") == "generate_document_section" and section:
            completed_sections.add(section)
    scope = dict(metadata.get("screenplayScope") or {})
    episode_numbers = tuple(
        sorted({
            int((thaw_json_mapping(unit.metadata).get("input") or {}).get("episodeNumber") or 0)
            for unit in units
        } - {0})
    )
    section_keys = tuple(dict.fromkeys(
        str((thaw_json_mapping(unit.metadata).get("input") or {}).get("sectionKey") or "")
        for unit in units
        if str((thaw_json_mapping(unit.metadata).get("input") or {}).get("sectionKey") or "")
    ))
    remaining = {
        "scope": scope,
        "episodeNumbers": [
            number for number in episode_numbers if number not in completed_episodes
        ],
        "sectionKeys": [
            section for section in section_keys if section not in completed_sections
        ],
    }
    failures = tuple({
        "code": str(unit.error_code),
        "category": str((unit.failure or {}).get("category") or "unit"),
    } for unit in units if unit.error_code)
    return ScreenplayCheckpointInput(
        checkpoint_key=checkpoint_key,
        root_run_id=task.created_by_run_id,
        task_id=task.id,
        turn_id=str(metadata["turnId"]),
        project_id=str(metadata["projectId"]),
        session_id=int(metadata["sessionId"]),
        target_role=str(metadata["targetRole"]),
        original_plan=original,
        current_plan=current,
        completed_summaries=completed,
        artifact_receipts=tuple(receipts),
        typed_failures=failures,
        constraint_changes=(),
        remaining_scope=remaining,
        base_revision_id=str(metadata.get("baseRevisionId") or "") or None,
    )


def _checkpoint_input_digest(value: ScreenplayCheckpointInput) -> str:
    payload = {
        "checkpoint": value.checkpoint_key,
        "originalPlan": _plan_mapping(value.original_plan),
        "currentPlan": _plan_mapping(value.current_plan),
        "completed": [dict(item) for item in value.completed_summaries],
        "receipts": [dict(item) for item in value.artifact_receipts],
        "failures": [dict(item) for item in value.typed_failures],
        "constraintChanges": [dict(item) for item in value.constraint_changes],
        "remainingScope": dict(value.remaining_scope),
        "targetRole": value.target_role,
        "baseRevisionId": value.base_revision_id,
    }
    return "sha256:" + hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


async def _settle_screenplay_execution(dispatcher, task_id, result) -> None:
    task = await dispatcher._long_tasks.load(task_id)
    if task is None:
        raise RuntimeError("screenplay LongTask disappeared")
    metadata = thaw_json_mapping(task.metadata)
    operation_id = str(metadata.get("operationId") or "")
    if await dispatcher._operations.load(operation_id) is None:
        raise RuntimeError("screenplay Operation disappeared")
    if result.status not in {
        LongTaskExecutionStatus.COMPLETED,
        LongTaskExecutionStatus.PAUSED,
        LongTaskExecutionStatus.CANCELED,
        LongTaskExecutionStatus.FAILED,
    }:
        raise RuntimeError("screenplay LongTask returned an unknown status")


async def _settle_screenplay_exception(dispatcher, task_id, error) -> None:
    del dispatcher, task_id, error
    # Root terminal commit projection owns Operation and Turn settlement.
    return None


def build_screenplay_profile_extension(
    *,
    db,
    candidate_normalizer=None,
    **_dependencies,
) -> ScreenplayAgentProfileExtension:
    return ScreenplayAgentProfileExtension(
        db,
        candidate_normalizer=candidate_normalizer,
    )


__all__ = [
    "ScreenplayAgentProfileExtension",
    "build_screenplay_profile_extension",
]
