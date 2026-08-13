"""Atomically project a completed screenplay Root Run into product state."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from purra.contracts import RunStatus
from purra.errors import RunCommitProjectionError
from purra.ports import RunCommit

from application.screenplay_candidate_assembler import ScreenplayCandidateAssembler
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from domains.screenplay_agent.agent_context import SCREENPLAY_AGENT_DOMAIN_NAMESPACE
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
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

    async def project(self, run_id: str, commit: RunCommit) -> None:
        if commit.terminal_status is not RunStatus.DONE:
            return None
        row = await self._db.fetch_one(
            "SELECT binding_namespace, binding_attributes_json "
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

        try:
            turn = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_turns WHERE planner_run_id = ?",
                [run_id],
            )
            if turn is None:
                raise LookupError("screenplay Root Turn does not exist")
            operation_id = str(turn.get("operation_id") or "").strip()
            final_response = str(commit.final_response or "")
            if operation_id:
                await self._finalize_operation(
                    turn=turn,
                    operation_id=operation_id,
                    final_response=final_response,
                )
            else:
                await self._complete_answer(turn, final_response)
        except ScreenplayAgentRootCompletionError:
            raise
        except Exception as error:
            raise ScreenplayAgentRootCompletionError(
                "screenplay Root product state could not be committed"
            ) from error

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


__all__ = [
    "ScreenplayAgentRootCompletionError",
    "ScreenplayAgentRootCompletionProjector",
]
