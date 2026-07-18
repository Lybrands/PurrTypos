"""Transactionally consistent read models for persisted Agent Runs."""

from __future__ import annotations

from typing import Any

from agent_core.contracts import AgentDelegation, DelegationAggregation
from agent_core.ports import CheckpointStore
from agent_core.json_values import thaw_json_mapping
from domains.agent_roles import AgentRoleRegistry


RUN_SNAPSHOT_VERSION = 1


class AgentRunQueryService:
    """Build resumable Run snapshots without exposing storage row shapes."""

    def __init__(
        self,
        store: CheckpointStore,
        *,
        role_registry: AgentRoleRegistry | None = None,
    ) -> None:
        self._store = store
        self._role_registry = role_registry

    async def get_snapshot(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run id is required")
        normalized_after = int(after_event_id)
        normalized_limit = int(limit)
        if normalized_after < 0:
            raise ValueError("event cursor must be non-negative")
        if normalized_limit < 1 or normalized_limit > 500:
            raise ValueError("event page limit must be between 1 and 500")

        checkpoint = await self._store.load(
            normalized_run_id,
            after_event_id=normalized_after,
            limit=normalized_limit,
        )
        if checkpoint is None:
            return None

        envelopes = [
            {
                "version": RUN_SNAPSHOT_VERSION,
                "cursor": int(event.get("id") or 0),
                "type": str(event.get("eventType") or ""),
                "runId": normalized_run_id,
                "payload": thaw_json_mapping(event.get("payload") or {}),
                "createdAt": event.get("createTime"),
            }
            for event in checkpoint.events
        ]
        aggregation = _aggregate_delegations(
            checkpoint.delegations,
            self._role_registry,
        )
        return {
            "version": RUN_SNAPSHOT_VERSION,
            "run": _run_view(
                thaw_json_mapping(checkpoint.run),
                self._role_registry,
            ),
            "todos": [thaw_json_mapping(step) for step in checkpoint.steps],
            "events": envelopes,
            "delegations": {
                "items": [
                    _delegation_view(item, self._role_registry)
                    for item in checkpoint.delegations
                ],
                "aggregate": _aggregation_view(aggregation),
            },
            "nextCursor": checkpoint.next_cursor,
            "hasMore": checkpoint.has_more,
        }


def _run_view(
    run: dict[str, Any],
    role_registry: AgentRoleRegistry | None = None,
) -> dict[str, Any]:
    agent_role = str(run.get("agent_role") or "").strip()
    definition = role_registry.get(agent_role) if role_registry else None
    return {
        "runId": run.get("id"),
        "sessionId": run.get("session_id"),
        "conversationId": run.get("conversation_id"),
        "status": run.get("status"),
        "mode": run.get("mode"),
        "lineage": {
            "parentRunId": run.get("parent_run_id"),
            "rootRunId": run.get("root_run_id") or run.get("id"),
            "delegationId": run.get("delegation_id"),
            "agentRole": agent_role or None,
            "agentTitle": definition.title if definition else (agent_role or None),
            "depth": int(run.get("run_depth") or 0),
        },
        "finalResponse": run.get("final_response") or "",
        "createdAt": run.get("create_time"),
        "updatedAt": run.get("update_time"),
        "execution": {
            "attempt": int(run.get("execution_attempt") or 0),
            "leaseExpiresAtMs": run.get("lease_expires_at_ms"),
            "heartbeatAtMs": run.get("heartbeat_at_ms"),
            "cancellationRequested": (
                run.get("cancel_requested_at_ms") is not None
            ),
        },
        "provenance": {
            "modelProvider": run.get("model_provider"),
            "modelName": run.get("model_name"),
            "contextWindow": run.get("context_window"),
            "endpointDigest": run.get("endpoint_digest"),
            "requestProfileDigest": run.get("request_profile_digest"),
        },
    }


def _delegation_view(
    run: AgentDelegation,
    role_registry: AgentRoleRegistry | None = None,
) -> dict[str, Any]:
    return {
        "delegationId": run.id,
        "parentRunId": run.parent_run_id,
        "rootRunId": run.root_run_id,
        "childRunId": run.child_run_id,
        "agentRole": run.agent_role,
        "agentTitle": _role_title(run.agent_role, role_registry),
        "objective": run.objective,
        "status": run.status.value,
        "required": run.required,
        "priority": run.priority,
        "resultSummary": run.result_summary,
        "error": run.error,
    }


def _aggregate_delegations(
    items: tuple[AgentDelegation, ...],
    role_registry: AgentRoleRegistry | None = None,
) -> DelegationAggregation:
    counts = {
        status: sum(item.status.value == status for item in items)
        for status in ("queued", "claimed", "running", "done", "failed", "canceled")
    }
    failures = tuple(
        item.id
        for item in items
        if item.required and item.status.value in {"failed", "canceled"}
    )
    pending = counts["queued"] + counts["claimed"] + counts["running"]
    state = "pending" if pending else ("blocked" if failures else "ready")
    return DelegationAggregation(
        state=state,  # type: ignore[arg-type]
        counts=counts,
        required_failures=failures,
        results=tuple({
            "delegationId": item.id,
            "agentRole": item.agent_role,
            "agentTitle": _role_title(item.agent_role, role_registry),
            "childRunId": item.child_run_id,
            "summary": item.result_summary or "",
        } for item in items if item.status.value == "done"),
    )


def _aggregation_view(value: DelegationAggregation) -> dict[str, Any]:
    return {
        "state": value.state,
        "counts": dict(value.counts),
        "requiredFailures": list(value.required_failures),
        "results": [dict(item) for item in value.results],
    }


def _role_title(
    role_id: str,
    role_registry: AgentRoleRegistry | None,
) -> str:
    definition = role_registry.get(role_id) if role_registry else None
    return definition.title if definition else role_id
