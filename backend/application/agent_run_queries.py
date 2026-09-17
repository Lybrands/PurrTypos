"""Transactionally consistent read models for persisted Agent Runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from purra.json_values import thaw_json_mapping
from purra.output import AgentOutputRepository, OutputVisibility
from application.sse_mapping import canonical_output_to_sse_chunk
from application.sub_agent_runs import delegation_projection


RUN_SNAPSHOT_VERSION = 2


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
        related_runs_provider=None,
    ) -> None:
        self._store = store
        self._output = output_repository
        self._product_events = product_event_query
        self._related_runs = related_runs_provider

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
        product_events = (
            await self._product_events.list_for_run(normalized_run_id)
            if self._product_events is not None
            else []
        )
        # 根 Run 附带子 Agent（子 Run）投影：快照消费方据此还原委派视图。
        # 提供方负责按 agent kind 决定数据来源；子 Run 快照保持轻量。
        related_runs: list[dict[str, Any]] = []
        if self._related_runs is not None and not persisted.run.get("parent_run_id"):
            related_runs = await self._related_runs(normalized_run_id)
        run_view = _run_view(thaw_json_mapping(persisted.run))
        if related_runs:
            run_view["relatedRuns"] = related_runs
        return {
            "version": RUN_SNAPSHOT_VERSION,
            "run": run_view,
            "todos": [thaw_json_mapping(step) for step in persisted.steps],
            "events": envelopes,
            "productEvents": product_events,
            "delegations": (
                delegation_projection(related_runs)
                if self._related_runs is not None
                else _empty_delegation_projection()
            ),
            "nextCursor": (
                output_events[-1].sequence
                if output_events
                else normalized_after
            ),
            "hasMore": has_more,
        }


def _empty_delegation_projection() -> dict[str, Any]:
    return {
        "items": [],
        "aggregate": {
            "state": "ready",
            "counts": {
                status: 0
                for status in (
                    "queued",
                    "claimed",
                    "running",
                    "done",
                    "failed",
                    "canceled",
                )
            },
            "requiredFailures": [],
            "results": [],
        },
    }


def _run_view(
    run: dict[str, Any],
) -> dict[str, Any]:
    return {
        "runId": run.get("id"),
        **({"rootRunId": run["root_run_id"]} if run.get("root_run_id") else {}),
        **({"parentRunId": run["parent_run_id"]} if run.get("parent_run_id") else {}),
        **({"agentId": run["agent_id"]} if run.get("agent_id") else {}),
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
        "activity": {
            "modelAttemptCount": int(run.get("model_attempt_count") or 0),
            "usage": {
                "inputTokens": int(run.get("input_tokens") or 0),
                "generationTokens": int(run.get("output_tokens") or 0),
                "reasoningTokens": (
                    None
                    if int(run.get("unreported_reasoning_attempts") or 0) > 0
                    else int(run.get("reasoning_tokens") or 0)
                ),
                "totalTokens": (
                    int(run.get("input_tokens") or 0)
                    + int(run.get("output_tokens") or 0)
                ),
                "unreportedAttempts": int(
                    run.get("unreported_usage_attempts") or 0
                ),
                "unreportedReasoningAttempts": int(
                    run.get("unreported_reasoning_attempts") or 0
                ),
            },
            "providerOutputEvents": int(run.get("provider_output_events") or 0),
            "providerOutputBytes": int(run.get("provider_output_bytes") or 0),
        },
        "provenance": {
            "modelProvider": run.get("model_provider"),
            "modelName": run.get("model_name"),
            "contextWindow": run.get("context_window"),
            "endpointDigest": run.get("endpoint_digest"),
            "requestProfileDigest": run.get("request_profile_digest"),
        },
    }
