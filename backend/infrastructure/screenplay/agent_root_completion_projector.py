"""Atomically project a completed screenplay Root Run into product state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence

from purra.contracts import RunStatus
from purra.errors import RunCommitProjectionError
from purra.ports import RunCommit

from application.screenplay_candidate_assembler import ScreenplayCandidateAssembler
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from domains.screenplay_agent.agent_context import SCREENPLAY_AGENT_DOMAIN_NAMESPACE
from domains.screenplay_agent import OperationUsage
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)


_ROOT_BINDING_NAMESPACE = "screenplay.conversation_turn"
_PROFILE_ID = "screenplay"


class ScreenplayAgentRootCompletionError(RunCommitProjectionError):
    """A completed screenplay Root could not be committed to product state."""

    default_code = "screenplay_root_commit_failed"


class ScreenplayAgentRootCompletionProjector:
    """Finalize one screenplay Turn in the Root terminal transaction."""

    def __init__(self, db) -> None:
        self._db = db
        self._parts = ScreenplayPartArtifactQuery(db)
        self._finalizer = SqliteScreenplayOperationFinalizer(
            db,
            candidate_assembler=ScreenplayCandidateAssembler(db),
        )
        self._operations = SqliteScreenplayOperationRepository(db)
        self._long_tasks = SqliteLongTaskRepository(db)
        self._turns = SqliteScreenplayAgentRepository(
            db,
            owner_id="screenplay-root-projector",
        )

    async def project(self, run_id: str, commit: RunCommit) -> None:
        if commit.terminal_status is None:
            return None
        try:
            row = await self._db.fetch_one(
                "SELECT session_id, binding_namespace, binding_aggregate_id, "
                "binding_command_id, binding_attributes_json "
                "FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if row is None or str(row.get("binding_namespace") or "") != (
                _ROOT_BINDING_NAMESPACE
            ):
                return None
            attributes = _json_mapping(row.get("binding_attributes_json"))
            if (
                str(attributes.get("agentProfile") or "") != _PROFILE_ID
                or str(attributes.get("domainNamespace") or "")
                != SCREENPLAY_AGENT_DOMAIN_NAMESPACE
            ):
                raise ScreenplayAgentRootCompletionError(
                    "screenplay Root binding profile is invalid"
                )
            identity_rows = await self._db.fetch_all(
                "SELECT turn_id FROM ai_agent_run_events "
                "WHERE run_id = ? AND source_event_key = ? "
                "AND kind = 'run.lifecycle' AND event_id IS NOT NULL",
                [run_id, f"run:{run_id}:running"],
            )
            if len(identity_rows) != 1:
                raise ValueError(
                    "screenplay Root canonical Turn identity is missing"
                )
            canonical_turn_id = str(
                identity_rows[0].get("turn_id") or ""
            ).strip()
            if not canonical_turn_id:
                raise ValueError(
                    "screenplay Root canonical Turn identity is empty"
                )
            turns = await self._db.fetch_all(
                "SELECT * FROM screenplay_agent_turns WHERE planner_run_id = ?",
                [run_id],
            )
            if len(turns) != 1:
                raise LookupError("screenplay Root Turn does not exist")
            turn = turns[0]
            expected_command_id = str(turn.get("command_id") or "").strip()
            continuation_of = str(attributes.get("continuationOf") or "").strip()
            if continuation_of:
                operation_id = str(attributes.get("operationId") or "").strip()
                resume = await self._db.fetch_one(
                    "SELECT command_id FROM screenplay_agent_operation_commands "
                    "WHERE operation_id = ? AND command_type = 'resume' "
                    "AND command_id = ? AND continuation_status = 'bound' "
                    "AND continuation_root_run_id = ? "
                    "AND continuation_source_root_run_id = ? "
                    "AND continuation_turn_id = ? "
                    "AND continuation_session_id = ? "
                    "AND continuation_project_id = ?",
                    [
                        operation_id,
                        str(row.get("binding_command_id") or ""),
                        run_id,
                        continuation_of,
                        canonical_turn_id,
                        int(row.get("session_id") or 0),
                        str(row.get("binding_aggregate_id") or ""),
                    ],
                )
                if resume is None:
                    raise ValueError(
                        "screenplay continuation command is not persisted"
                    )
                expected_command_id = str(resume["command_id"])
            _require_root_identity(
                run=row,
                turn=turn,
                canonical_turn_id=canonical_turn_id,
                expected_command_id=expected_command_id,
            )
            operation_id = str(turn.get("operation_id") or "").strip()
            final_response = str(commit.final_response or "")
            if operation_id:
                await self._project_operation_usage(run_id, operation_id)
                if commit.terminal_status is RunStatus.DONE:
                    await self._finalize_operation(
                        turn=turn,
                        operation_id=operation_id,
                        final_response=final_response,
                    )
                else:
                    await self._settle_operation_terminal(
                        root_run_id=run_id,
                        turn=turn,
                        operation_id=operation_id,
                        status=RunStatus(commit.terminal_status),
                        error=commit.error,
                    )
            else:
                if commit.terminal_status is RunStatus.DONE:
                    await self._complete_answer(turn, final_response)
                else:
                    await self._settle_answer_terminal(
                        turn=turn,
                        status=RunStatus(commit.terminal_status),
                        error=commit.error,
                    )
        except ScreenplayAgentRootCompletionError:
            raise
        except Exception as error:
            raise ScreenplayAgentRootCompletionError(
                "screenplay Root product state could not be committed",
                retryable=_is_transient_projection_error(error),
            ) from error

    async def _settle_operation_terminal(
        self,
        *,
        root_run_id: str,
        turn: Mapping[str, object],
        operation_id: str,
        status: RunStatus,
        error: str | None,
    ) -> None:
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise LookupError("screenplay Root Operation does not exist")
        task = (
            await self._long_tasks.load(operation.long_task_id)
            if operation.long_task_id else None
        )
        explicit_cancel = (
            turn.get("cancel_requested_at_ms") is not None
            or operation.cancel_requested_at_ms is not None
        )
        if status is RunStatus.CANCELED and explicit_cancel:
            if task is not None and not task.status.terminal:
                await self._long_tasks.cancel(task.id)
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if not receipt_id:
                raise ValueError(
                    "screenplay explicit cancel has no durable receipt"
                )
            await self._operations.settle_cancel(
                str(turn["id"]),
                receipt_id=receipt_id,
            )
            return
        if (
            status is RunStatus.CANCELED
            and task is not None
            and task.status.value == "paused"
        ):
            paused = await self._db.fetch_one(
                "SELECT error_code FROM screenplay_checkpoint_plans "
                "WHERE operation_id = ? AND status = 'paused' "
                "ORDER BY update_time DESC, checkpoint_key DESC LIMIT 1",
                [operation_id],
            )
            code = str((paused or {}).get("error_code") or "screenplay_task_paused")
            message = _terminal_message(code, paused=True)
            operation = await self._operations.pause(
                operation_id,
                code=code,
                message=message,
                command_id=(
                    f"operation:root-pause:{operation_id}:"
                    f"{root_run_id}:{code}"
                ),
            )
            await self._turns.pause_task(
                str(turn["id"]),
                code=code,
                message=message,
            )
            return
        if status is RunStatus.CANCELED:
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if not receipt_id:
                receipt = await self._operations.request_cancel(
                    str(turn["id"]),
                    idempotency_key=f"root-terminal-cancel:{turn['id']}",
                )
                receipt_id = receipt.id
            await self._operations.settle_cancel(
                str(turn["id"]),
                receipt_id=receipt_id,
            )
            return
        code = str(error or status.value or "screenplay_root_failed")[:240]
        message = _terminal_message(code)
        operation = await self._operations.load(operation_id)
        assert operation is not None
        await self._operations.fail(
            operation_id,
            code=code,
            message=message,
            command_id=(
                f"operation:root-fail:{operation_id}:"
                f"{root_run_id}:{code}"
            ),
        )
        await self._turns.fail_task(
            str(turn["id"]),
            code=code,
            message=message,
        )

    async def _settle_answer_terminal(
        self,
        *,
        turn: Mapping[str, object],
        status: RunStatus,
        error: str | None,
    ) -> None:
        turn_id = str(turn["id"])
        if status is RunStatus.CANCELED:
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if not receipt_id:
                receipt = await self._operations.request_cancel(
                    turn_id,
                    idempotency_key=f"root-terminal-cancel:{turn_id}",
                )
                receipt_id = receipt.id
            await self._operations.settle_cancel(
                turn_id,
                receipt_id=receipt_id,
            )
            return
        code = str(error or status.value or "screenplay_root_failed")[:240]
        await self._turns.fail_turn(
            turn_id,
            code=code,
            message=_terminal_message(code),
        )

    async def _project_operation_usage(
        self,
        root_run_id: str,
        operation_id: str,
    ) -> None:
        root_ids = {str(root_run_id)}
        cursor = str(root_run_id)
        while cursor:
            row = await self._db.fetch_one(
                "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = ?",
                [cursor],
            )
            previous = str(
                _json_mapping((row or {}).get("binding_attributes_json")).get(
                    "continuationOf"
                ) or ""
            ).strip()
            if not previous or previous in root_ids:
                break
            root_ids.add(previous)
            cursor = previous
        placeholders = ",".join("?" for _ in root_ids)
        rows = await self._db.fetch_all(
            "SELECT id FROM ai_agent_runs WHERE id IN (" + placeholders + ") "
            "OR root_run_id IN (" + placeholders + ") ORDER BY id",
            [*sorted(root_ids), *sorted(root_ids)],
        )
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise LookupError("screenplay Root Operation does not exist")
        for run in rows:
            usage_rows = await self._db.fetch_all(
                "SELECT payload_json FROM ai_agent_run_events WHERE run_id = ? "
                "AND kind = 'provider.usage' AND visibility = 'private' "
                "AND event_id IS NOT NULL ORDER BY sequence, id",
                [str(run["id"])],
            )
            if not usage_rows:
                continue
            payloads = [_json_mapping(row.get("payload_json")) for row in usage_rows]
            operation = await self._operations.record_usage(
                operation_id,
                run_id=str(run["id"]),
                usage=OperationUsage(
                    invocation_count=len(payloads),
                    input_tokens=sum(int(item.get("inputTokens") or 0) for item in payloads),
                    output_tokens=sum(int(item.get("outputTokens") or 0) for item in payloads),
                    reasoning_tokens=sum(
                        int(item.get("reasoningOutputTokens") or 0)
                        for item in payloads
                    ),
                ),
                expected_revision=operation.revision,
            )

    async def _complete_answer(
        self,
        turn: Mapping[str, object],
        final_response: str,
    ) -> None:
        status = str(turn.get("status") or "")
        existing = str(turn.get("assistant_content") or "")
        if status == "completed":
            if existing != final_response:
                raise ValueError("screenplay answer replay conflicts")
            return
        if status not in {"queued", "planning"}:
            raise ValueError("screenplay answer Turn is not ready to complete")
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = 'completed', "
            "assistant_content = ?, execution_owner_id = NULL, "
            "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [final_response, str(turn["id"])],
        )

    async def _finalize_operation(
        self,
        *,
        turn: Mapping[str, object],
        operation_id: str,
        final_response: str,
    ) -> None:
        operation = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE id = ?",
            [operation_id],
        )
        if operation is None:
            raise LookupError("screenplay Root Operation does not exist")
        task_id = str(operation.get("long_task_id") or "").strip()
        if not task_id:
            raise ValueError("screenplay Root Operation has no LongTask")
        task = await self._db.fetch_one(
            "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        if task is None or str(task.get("status") or "") != "completed":
            raise ValueError("screenplay Root LongTask is not completed")
        requirements = _json_mapping(operation.get("requirements_json"))
        candidate_ids, response_id = _finalization_unit_ids(requirements)
        candidate_refs = []
        for unit_id in candidate_ids:
            ref = await self._parts.validated_unit_ref(task_id, unit_id)
            if ref is None:
                raise ValueError(
                    f"screenplay validation Part is missing: {unit_id}"
                )
            candidate_refs.append(ref)
        response_ref = await self._parts.validated_unit_ref(task_id, response_id)
        if response_ref is None:
            raise ValueError("screenplay final response Part is missing")
        response = await self._parts.require(response_ref)
        authoritative_response = str(response.get("finalResponse") or "").strip()
        if not authoritative_response or authoritative_response != final_response:
            raise ValueError("screenplay Root response conflicts with final Part")
        await self._finalizer.finalize(
            ScreenplayOperationFinalizationCommand(
                operation_id=operation_id,
                expected_manifest_digest=str(operation.get("manifest_digest") or ""),
                candidate_part_refs=tuple(candidate_refs),
                final_response_ref=response_ref,
            )
        )
        projected = await self._db.fetch_one(
            "SELECT status, assistant_content FROM screenplay_agent_turns "
            "WHERE id = ?",
            [str(turn["id"])],
        )
        if (
            projected is None
            or str(projected.get("status") or "") != "completed"
            or str(projected.get("assistant_content") or "") != final_response
        ):
            raise ValueError("screenplay Root Turn projection conflicts")


def _finalization_unit_ids(
    requirements: Mapping[str, object],
) -> tuple[tuple[str, ...], str]:
    recipe = requirements.get("recipe")
    steps = recipe.get("steps") if isinstance(recipe, Mapping) else None
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("screenplay Operation recipe is missing")
    candidates = tuple(
        str(step.get("id") or "")
        for step in steps
        if isinstance(step, Mapping)
        and str(step.get("kind") or "") == "validate_manifest_part"
    )
    responses = tuple(
        str(step.get("id") or "")
        for step in steps
        if isinstance(step, Mapping)
        and str(step.get("kind") or "") == "compose_final_response"
    )
    if not candidates or any(not value for value in candidates) or len(responses) != 1:
        raise ValueError("screenplay Operation finalization Parts are invalid")
    return candidates, responses[0]


def _json_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _terminal_message(code: str, *, paused: bool = False) -> str:
    if paused:
        return "剧本任务已安全暂停，可在确认后继续。"
    return str(code or "剧本 Agent Root Run 未完成")


def _require_root_identity(
    *,
    run: Mapping[str, object],
    turn: Mapping[str, object],
    canonical_turn_id: str,
    expected_command_id: str | None = None,
) -> None:
    expected = {
        "conversation Turn": (
            canonical_turn_id,
            str(turn.get("id") or "").strip(),
        ),
        "session": (
            str(run.get("session_id") or "").strip(),
            str(turn.get("session_id") or "").strip(),
        ),
        "project": (
            str(run.get("binding_aggregate_id") or "").strip(),
            str(turn.get("project_id") or "").strip(),
        ),
        "command": (
            str(run.get("binding_command_id") or "").strip(),
            str(expected_command_id or turn.get("command_id") or "").strip(),
        ),
    }
    for label, (actual, persisted) in expected.items():
        if not actual or not persisted or actual != persisted:
            raise ValueError(f"screenplay Root {label} identity conflicts")


def _is_transient_projection_error(error: Exception) -> bool:
    if isinstance(error, RunCommitProjectionError):
        return bool(error.retryable)
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower()
        for marker in ("database is locked", "database is busy", "interrupted")
    )


__all__ = [
    "ScreenplayAgentRootCompletionError",
    "ScreenplayAgentRootCompletionProjector",
]
