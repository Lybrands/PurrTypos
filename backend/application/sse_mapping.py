"""Transport-only serialization for canonical PurrA output."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from purra.contracts import AgentRunResult
from purra.json_values import thaw_json_mapping
from purra.output import AgentOutputEvent, OutputEventKind, OutputVisibility


def core_update_to_sse_chunk(
    update: AgentOutputEvent | AgentRunResult,
    *,
    model: str,
) -> dict[str, Any] | None:
    if isinstance(update, AgentOutputEvent):
        return canonical_output_to_sse_chunk(update)
    if not isinstance(update, AgentRunResult):
        raise TypeError("SSE accepts only canonical output or a transport result")
    return {
        "done": True,
        "model": update.model or model,
        "runResult": {
            "runId": update.run_id,
            "status": update.status.value,
            "errorCode": update.error,
        },
    }


def canonical_output_to_sse_chunk(
    event: AgentOutputEvent,
) -> dict[str, Any] | None:
    if event.visibility is not OutputVisibility.PUBLIC:
        return None
    return {
        "eventId": event.event_id,
        "outputStreamId": event.output_stream_id,
        "runId": event.run_id,
        **({"rootRunId": event.root_run_id} if event.root_run_id else {}),
        **({"parentRunId": event.parent_run_id} if event.parent_run_id else {}),
        **({"agentId": event.agent_id} if event.agent_id else {}),
        "turnId": event.turn_id,
        "invocationId": event.invocation_id,
        "sequence": event.sequence,
        "source": event.source.value,
        "kind": event.kind.value,
        "channel": event.channel.value,
        "visibility": event.visibility.value,
        "payload": thaw_json_mapping(event.payload),
        "occurredAt": event.occurred_at.isoformat(),
        "emittedAt": event.emitted_at.isoformat(),
    }


def bridge_chunk_for_effect(effect_type: str, data: Any) -> dict[str, Any] | None:
    """把领域效果 (type, payload) 转成传输桥接块；白名单外的返回 None。"""
    if not isinstance(data, Mapping):
        return None
    if effect_type == "writing.chapter_content_updated":
        if data.get("chapterId") is None or data.get("noop") is True:
            return None
        return {
            "chapterContentUpdated": {
                "bookId": data.get("bookId"),
                "chapterId": data.get("chapterId"),
                "committedRevision": data.get("committedRevision"),
                "firstContent": data.get("firstContent") is True,
            }
        }
    if effect_type == "writing.chapters_created":
        # purra 冻结容器为 FrozenList（Sequence 子类而非 list/tuple），
        # 按结构化协议判断，否则批量建章通知会被整条丢弃。
        chapters = data.get("chapters")
        if (
            isinstance(chapters, (str, bytes))
            or not isinstance(chapters, Sequence)
            or not chapters
        ):
            return None
        return {
            "chaptersCreated": {
                "bookId": data.get("bookId"),
                "chapters": list(chapters),
            }
        }
    return None


__all__ = [
    "bridge_chunk_for_effect",
    "canonical_output_to_sse_chunk",
    "core_update_to_sse_chunk",
]
