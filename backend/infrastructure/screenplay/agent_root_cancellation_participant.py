"""Project a trusted screenplay Root cancellation request into business state."""

from __future__ import annotations

from collections.abc import Mapping

from domains.screenplay_agent.agent_context import SCREENPLAY_AGENT_DOMAIN_NAMESPACE
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from purra.errors import ContractViolationError


class ScreenplayRootCancellationParticipant:
    """Fence the Turn, Operation, and LongTask in the Root fence transaction."""

    def __init__(self, db) -> None:
        self._db = db
        self._operations = SqliteScreenplayOperationRepository(db)

    async def project(
        self,
        root_run_id: str,
        receipt: Mapping[str, object],
    ) -> None:
        run = await self._db.fetch_one(
            "SELECT session_id, binding_namespace, binding_aggregate_id, "
            "binding_command_id, binding_attributes_json FROM ai_agent_runs "
            "WHERE id = ?",
            [root_run_id],
        )
        if run is None or str(run.get("binding_namespace") or "") != (
            "screenplay.conversation_turn"
        ):
            return None
        attributes = _json_mapping(run.get("binding_attributes_json"))
        if (
            str(attributes.get("agentProfile") or "") != "screenplay"
            or str(attributes.get("domainNamespace") or "")
            != SCREENPLAY_AGENT_DOMAIN_NAMESPACE
        ):
            raise ContractViolationError(
                "screenplay Root cancellation profile conflicts"
            )
        identities = await self._db.fetch_all(
            "SELECT turn_id FROM ai_agent_run_events WHERE run_id = ? "
            "AND source_event_key = ? AND kind = 'run.lifecycle' "
            "AND event_id IS NOT NULL",
            [root_run_id, f"run:{root_run_id}:running"],
        )
        if len(identities) != 1:
            raise ContractViolationError(
                "screenplay Root cancellation Turn identity is invalid"
            )
        turn_id = str(identities[0].get("turn_id") or "").strip()
        turn = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ? "
            "AND planner_run_id = ?",
            [turn_id, root_run_id],
        )
        if (
            turn is None
            or not turn_id
            or int(turn.get("session_id") or 0)
            != int(run.get("session_id") or 0)
            or str(turn.get("project_id") or "")
            != str(run.get("binding_aggregate_id") or "")
        ):
            raise ContractViolationError(
                "screenplay Root cancellation identity conflicts"
            )
        await self._validate_command(
            root_run_id=root_run_id,
            run=run,
            turn=turn,
            attributes=attributes,
        )
        epoch = int(receipt.get("cancellation_epoch") or 0)
        if epoch <= 0:
            raise ContractViolationError(
                "screenplay Root cancellation epoch is invalid"
            )
        await self._operations.request_cancel(
            turn_id,
            idempotency_key=f"root-cancel:{root_run_id}",
        )
        return None

    async def _validate_command(
        self,
        *,
        root_run_id: str,
        run: Mapping[str, object],
        turn: Mapping[str, object],
        attributes: Mapping[str, object],
    ) -> None:
        command_id = str(run.get("binding_command_id") or "").strip()
        source_root = str(attributes.get("continuationOf") or "").strip()
        if not source_root:
            if command_id != str(turn.get("command_id") or ""):
                raise ContractViolationError(
                    "screenplay Root cancellation command conflicts"
                )
            return
        operation_id = str(attributes.get("operationId") or "").strip()
        if not operation_id:
            raise ContractViolationError(
                "screenplay Root cancellation continuation is incomplete"
            )
        command = await self._db.fetch_one(
            "SELECT command_id FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND operation_id = ? "
            "AND command_type = 'resume' "
            "AND continuation_status = 'bound' "
            "AND continuation_root_run_id = ? "
            "AND continuation_source_root_run_id = ? "
            "AND continuation_turn_id = ? AND continuation_session_id = ? "
            "AND continuation_project_id = ?",
            [
                command_id,
                operation_id,
                root_run_id,
                source_root,
                str(turn["id"]),
                int(turn["session_id"]),
                str(turn["project_id"]),
            ],
        )
        if command is None:
            raise ContractViolationError(
                "screenplay Root cancellation continuation conflicts"
            )


def _json_mapping(value: object) -> dict[str, object]:
    import json

    if not value:
        return {}
    decoded = json.loads(str(value))
    return dict(decoded) if isinstance(decoded, dict) else {}


__all__ = ["ScreenplayRootCancellationParticipant"]
