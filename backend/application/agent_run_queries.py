"""Transactionally consistent read models for persisted Agent Runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from purra.contracts import AgentDelegation, DelegationAggregation
from purra.json_values import thaw_json_mapping
from purra.output import OutputVisibility
from purra.output.ports import AgentOutputRepository
from application.sse_mapping import canonical_output_to_sse_chunk


RUN_SNAPSHOT_VERSION = 1


class RunSnapshotQuery(Protocol):
    async def load(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ): ...


class AgentRunQueryService:
    """Build resumable Run snapshots without exposing storage row shapes."""

    def __init__(
        self,
        store: RunSnapshotQuery,
        output_repository: AgentOutputRepository,
        *,
        product_event_query=None,
    ) -> None:
        self._store = store
        self._output = output_repository
        self._product_events = product_event_query

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

        persisted = await self._store.load(
            normalized_run_id,
            after_event_id=0,
            limit=1,
        )
        if persisted is None:
            return None

        output_page = await self._output.list_events(
            normalized_run_id,
            after_sequence=normalized_after,
            limit=normalized_limit + 1,
        )
        has_more = len(output_page) > normalized_limit
        output_events = output_page[:normalized_limit]
        envelopes = []
        for event in output_events:
            if event.visibility is not OutputVisibility.PUBLIC:
                continue
            mapped_chunk = canonical_output_to_sse_chunk(event)
            if mapped_chunk is None:  # pragma: no cover - serializer invariant
                continue
            payload = thaw_json_mapping(event.payload)
            envelope = {
                "version": RUN_SNAPSHOT_VERSION,
                "cursor": event.sequence,
                "type": str(payload.get("eventType") or event.kind.value),
                "runId": normalized_run_id,
                "payload": payload,
                "createdAt": event.emitted_at.isoformat(),
                "chunk": mapped_chunk,
            }
            envelopes.append(envelope)
        aggregation = _aggregate_delegations(persisted.delegations)
        product_events = (
            await self._product_events.list_for_run(normalized_run_id)
            if self._product_events is not None
            else []
        )
        return {
            "version": RUN_SNAPSHOT_VERSION,
            "run": _run_view(thaw_json_mapping(persisted.run)),
            "todos": [thaw_json_mapping(step) for step in persisted.steps],
            "events": envelopes,
            "productEvents": product_events,
            "delegations": {
                "items": [
                    _delegation_view(item)
                    for item in persisted.delegations
                ],
                "aggregate": _aggregation_view(aggregation),
            },
            "nextCursor": (
                output_events[-1].sequence
                if output_events
                else normalized_after
            ),
            "hasMore": has_more,
        }


def _run_view(
    run: dict[str, Any],
) -> dict[str, Any]:
    return {
        "runId": run.get("id"),
        "sessionId": run.get("session_id"),
        "conversationId": run.get("conversation_id"),
        "status": run.get("status"),
        "mode": run.get("mode"),
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
) -> dict[str, Any]:
    return {
        "delegationId": run.id,
        "runId": run.run_id,
        "agentName": run.agent_name,
        "agentTitle": run.agent_title,
        "objective": run.objective,
        "status": run.status.value,
        "required": run.required,
        "priority": run.priority,
        "resultSummary": run.result_summary,
        "error": run.error,
    }


def _aggregate_delegations(
    items: tuple[AgentDelegation, ...],
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
            "agentName": item.agent_name,
            "agentTitle": item.agent_title,
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
