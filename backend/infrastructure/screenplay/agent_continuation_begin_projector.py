"""Atomically bind a trusted screenplay continuation when its Root begins."""

from __future__ import annotations

from collections.abc import Mapping

from application.screenplay_checkpoint_planning import (
    SqliteScreenplayCheckpointRepository,
)
from domains.screenplay_agent.agent_context import SCREENPLAY_AGENT_DOMAIN_NAMESPACE
from domains.screenplay_agent import ContinuationStartLost, ScreenplayRootStartLost
from purra.contracts import RunCreateParams
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping
from infrastructure.persistence.run_execution_store import now_ms


class ScreenplayContinuationBeginProjector:
    def __init__(self, db) -> None:
        self._db = db

    async def project(self, run_id: str, params: RunCreateParams) -> None:
        binding = params.binding
        if binding is None or binding.namespace != "screenplay.conversation_turn":
            return None
        attributes = thaw_json_mapping(binding.attributes)
        source_root = str(attributes.get("continuationOf") or "").strip()
        if (
            str(attributes.get("agentProfile") or "") != "screenplay"
            or str(attributes.get("domainNamespace") or "")
            != SCREENPLAY_AGENT_DOMAIN_NAMESPACE
        ):
            raise ContractViolationError("screenplay continuation profile conflicts")
        if not source_root:
            return await self._project_initial_root(run_id, params, attributes)
        command_id = str(binding.command_id or "").strip()
        operation_id = str(attributes.get("operationId") or "").strip()
        owner_id = str(attributes.get("continuationOwner") or "").strip()
        identity_digest = str(
            attributes.get("continuationIdentityDigest") or ""
        ).strip()
        epoch = attributes.get("continuationEpoch")
        if not all((command_id, operation_id, owner_id, identity_digest)) or not (
            isinstance(epoch, int) and not isinstance(epoch, bool) and epoch > 0
        ):
            raise ContractViolationError("screenplay continuation binding is incomplete")
        command = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND command_type = 'resume'",
            [command_id],
        )
        turn = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ?",
            [params.turn_id],
        )
        operation = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE id = ?",
            [operation_id],
        )
        source = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [source_root],
        )
        expected = {
            "operation": operation_id,
            "turn": str(params.turn_id or ""),
            "source": source_root,
            "session": int(params.session_id or 0),
            "project": str(binding.aggregate_id or ""),
        }
        if command is None or (
            str(command.get("continuation_status") or "") != "starting"
            or str(command.get("continuation_owner_id") or "") != owner_id
            or int(command.get("continuation_epoch") or 0) != epoch
        ):
            raise ContinuationStartLost(
                "screenplay continuation reservation was lost"
            )
        if (
            str(command.get("operation_id") or "") != expected["operation"]
            or str(command.get("continuation_identity_digest") or "")
            != identity_digest
            or str(command.get("continuation_source_root_run_id") or "")
            != expected["source"]
            or str(command.get("continuation_turn_id") or "") != expected["turn"]
            or int(command.get("continuation_session_id") or 0)
            != expected["session"]
            or str(command.get("continuation_project_id") or "")
            != expected["project"]
            or turn is None
            or str(turn.get("root_run_id") or "") != expected["source"]
            or str(turn.get("status") or "") != "running"
            or turn.get("cancel_requested_at_ms") is not None
            or operation is None
            or str(operation.get("turn_id") or "") != expected["turn"]
            or str(operation.get("project_id") or "") != expected["project"]
            or int(operation.get("session_id") or 0) != expected["session"]
            or str(operation.get("status") or "") != "running"
            or operation.get("cancel_requested_at_ms") is not None
            or source != {"status": "canceled"}
        ):
            raise ContractViolationError("screenplay continuation identity conflicts")
        await SqliteScreenplayCheckpointRepository(
            self._db
        ).rebind_for_continuation(
            operation_id=operation_id,
            source_root_run_id=source_root,
            continuation_root_run_id=run_id,
        )
        await self._db.execute(
            "UPDATE screenplay_agent_operation_commands SET "
            "continuation_status = 'bound', continuation_root_run_id = ?, "
            "continuation_lease_expires_at_ms = NULL WHERE command_id = ? "
            "AND continuation_status = 'starting' "
            "AND continuation_owner_id = ? AND continuation_epoch = ? "
            "AND continuation_identity_digest = ?",
            [run_id, command_id, owner_id, epoch, identity_digest],
        )
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ContinuationStartLost(
                "screenplay continuation reservation was lost"
            )
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET planner_run_id = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND planner_run_id = ? AND status = 'running'",
            [run_id, expected["turn"], expected["source"]],
        )
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ContinuationStartLost(
                "screenplay continuation Turn rotation was lost"
            )
        return None

    async def _project_initial_root(
        self,
        run_id: str,
        params: RunCreateParams,
        attributes: Mapping[str, object],
    ) -> None:
        binding = params.binding
        assert binding is not None
        owner = str(attributes.get("turnExecutionOwner") or "").strip()
        attempt = attributes.get("turnAttempt")
        if not owner or not (
            isinstance(attempt, int)
            and not isinstance(attempt, bool)
            and attempt > 0
        ):
            raise ContractViolationError(
                "screenplay initial Root binding is incomplete"
            )
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET planner_run_id = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND project_id = ? "
            "AND session_id = ? AND command_id = ? AND status = 'planning' "
            "AND planner_run_id IS NULL AND execution_owner_id = ? "
            "AND attempt = ? AND lease_expires_at_ms > ? "
            "AND cancel_requested_at_ms IS NULL",
            [
                run_id,
                str(params.turn_id or ""),
                str(binding.aggregate_id or ""),
                int(params.session_id or 0),
                str(binding.command_id or ""),
                owner,
                attempt,
                now_ms(),
            ],
        )
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ScreenplayRootStartLost(
                "screenplay initial Root Turn claim was lost"
            )
        return None


__all__ = ["ScreenplayContinuationBeginProjector"]
