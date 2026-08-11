"""Closed contracts for canonical Agent output.

Public text is deliberately representable only as a Provider-authored event.
Runtime, tool, and domain producers use separate structured contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from purra.contracts import DomainEffect, RunId, RunStatus
from purra.json_values import FrozenDict, freeze_json_mapping
from purra.normalization import optional_text, positive_int, required_text


class AgentOutputIntent(StrEnum):
    EXECUTION_PUBLIC = "execution_public"
    FINAL_PUBLIC = "final_public"
    STRUCTURED_PRIVATE = "structured_private"
    REASONING_PRIVATE = "reasoning_private"


class OutputCommitMode(StrEnum):
    LIVE = "live"
    GATED = "gated"
    PRIVATE = "private"


class OutputSource(StrEnum):
    PROVIDER = "provider"
    RUNTIME = "runtime"
    TOOL = "tool"
    DOMAIN = "domain"


class OutputChannel(StrEnum):
    COMMENTARY = "commentary"
    FINAL = "final"
    OPERATION = "operation"
    LIFECYCLE = "lifecycle"
    ERROR = "error"
    DIAGNOSTIC = "diagnostic"


class OutputVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    DIAGNOSTIC = "diagnostic"


class OutputEventKind(StrEnum):
    STREAM_OPENED = "stream.opened"
    PROVIDER_CONTENT_DELTA = "provider.content_delta"
    PROVIDER_REASONING_DELTA = "provider.reasoning_delta"
    STREAM_COMMITTED = "stream.committed"
    STREAM_ABORTED = "stream.aborted"
    OPERATION_STARTED = "operation.started"
    OPERATION_FINISHED = "operation.finished"
    RUN_LIFECYCLE = "run.lifecycle"
    TOOL = "tool.event"
    DOMAIN_EFFECT = "domain.effect"


_PUBLIC_INTENTS = frozenset({
    AgentOutputIntent.EXECUTION_PUBLIC,
    AgentOutputIntent.FINAL_PUBLIC,
})
_TEXT_CHANNELS = frozenset({
    OutputChannel.COMMENTARY,
    OutputChannel.FINAL,
})


@dataclass(frozen=True, slots=True)
class OutputStreamSpec:
    output_stream_id: str
    run_id: RunId
    turn_id: str | None
    invocation_id: str
    intent: AgentOutputIntent
    commit_mode: OutputCommitMode

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output_stream_id",
            required_text(self.output_stream_id, "output stream id"),
        )
        object.__setattr__(self, "run_id", required_text(self.run_id, "run id"))
        object.__setattr__(self, "turn_id", optional_text(self.turn_id))
        object.__setattr__(
            self,
            "invocation_id",
            required_text(self.invocation_id, "invocation id"),
        )
        intent = AgentOutputIntent(self.intent)
        commit_mode = OutputCommitMode(self.commit_mode)
        if intent in _PUBLIC_INTENTS and commit_mode is not OutputCommitMode.LIVE:
            raise ValueError("public output intent must be live")
        if (
            intent
            in {
                AgentOutputIntent.STRUCTURED_PRIVATE,
                AgentOutputIntent.REASONING_PRIVATE,
            }
            and commit_mode is OutputCommitMode.LIVE
        ):
            raise ValueError("private output intent cannot be live")
        if (
            intent is AgentOutputIntent.REASONING_PRIVATE
            and commit_mode is not OutputCommitMode.PRIVATE
        ):
            raise ValueError(
                "reasoning-private output requires private commit mode"
            )
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "commit_mode", commit_mode)


@dataclass(frozen=True, slots=True)
class AgentOutputEventDraft:
    run_id: RunId
    turn_id: str | None
    output_stream_id: str | None
    invocation_id: str | None
    source_event_key: str
    source: OutputSource
    kind: OutputEventKind
    channel: OutputChannel
    visibility: OutputVisibility
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", required_text(self.run_id, "run id"))
        object.__setattr__(self, "turn_id", optional_text(self.turn_id))
        object.__setattr__(
            self,
            "output_stream_id",
            optional_text(self.output_stream_id),
        )
        object.__setattr__(
            self,
            "invocation_id",
            optional_text(self.invocation_id),
        )
        object.__setattr__(
            self,
            "source_event_key",
            required_text(self.source_event_key, "source event key"),
        )
        source = OutputSource(self.source)
        kind = OutputEventKind(self.kind)
        channel = OutputChannel(self.channel)
        visibility = OutputVisibility(self.visibility)
        _require_aware(self.occurred_at, "occurred_at")
        payload = freeze_json_mapping(self.payload)
        _validate_public_text(
            source=source,
            kind=kind,
            channel=channel,
            visibility=visibility,
            payload=payload,
            output_stream_id=self.output_stream_id,
            invocation_id=self.invocation_id,
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "payload", payload)

    @classmethod
    def public_text(
        cls,
        *,
        run_id: RunId,
        turn_id: str | None,
        output_stream_id: str,
        invocation_id: str,
        source_event_key: str,
        source: OutputSource,
        channel: OutputChannel,
        delta: str,
        occurred_at: datetime,
    ) -> AgentOutputEventDraft:
        return cls(
            run_id=run_id,
            turn_id=turn_id,
            output_stream_id=output_stream_id,
            invocation_id=invocation_id,
            source_event_key=source_event_key,
            source=source,
            kind=OutputEventKind.PROVIDER_CONTENT_DELTA,
            channel=channel,
            visibility=OutputVisibility.PUBLIC,
            payload={"delta": str(delta)},
            occurred_at=occurred_at,
        )


@dataclass(frozen=True, slots=True)
class AgentOutputEvent:
    event_id: str
    output_stream_id: str | None
    run_id: RunId
    turn_id: str | None
    invocation_id: str | None
    sequence: int
    source: OutputSource
    kind: OutputEventKind
    channel: OutputChannel
    visibility: OutputVisibility
    payload: Mapping[str, Any]
    occurred_at: datetime
    emitted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_id", required_text(self.event_id, "event id")
        )
        object.__setattr__(
            self,
            "output_stream_id",
            optional_text(self.output_stream_id),
        )
        object.__setattr__(self, "run_id", required_text(self.run_id, "run id"))
        object.__setattr__(self, "turn_id", optional_text(self.turn_id))
        object.__setattr__(
            self, "invocation_id", optional_text(self.invocation_id)
        )
        object.__setattr__(
            self, "sequence", positive_int(self.sequence, "sequence")
        )
        source = OutputSource(self.source)
        kind = OutputEventKind(self.kind)
        channel = OutputChannel(self.channel)
        visibility = OutputVisibility(self.visibility)
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.emitted_at, "emitted_at")
        payload = freeze_json_mapping(self.payload)
        _validate_public_text(
            source=source,
            kind=kind,
            channel=channel,
            visibility=visibility,
            payload=payload,
            output_stream_id=self.output_stream_id,
            invocation_id=self.invocation_id,
        )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "visibility", visibility)
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True, slots=True)
class RunLifecycleOutputDraft:
    source_event_key: str
    status: RunStatus
    payload: Mapping[str, Any]
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_event_key",
            required_text(self.source_event_key, "source event key"),
        )
        object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class ToolOutputEvent:
    operation_id: str
    tool_call_id: str
    tool_name: str
    status: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for attribute, label in (
            ("operation_id", "operation id"),
            ("tool_call_id", "tool call id"),
            ("tool_name", "tool name"),
            ("status", "tool status"),
        ):
            object.__setattr__(
                self,
                attribute,
                required_text(getattr(self, attribute), label),
            )
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class DomainEffectOutput:
    effect: DomainEffect
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.effect, DomainEffect):
            raise TypeError("domain effect output requires a DomainEffect")
        _require_aware(self.occurred_at, "occurred_at")


def _validate_public_text(
    *,
    source: OutputSource,
    kind: OutputEventKind,
    channel: OutputChannel,
    visibility: OutputVisibility,
    payload: FrozenDict,
    output_stream_id: str | None,
    invocation_id: str | None,
) -> None:
    if (
        visibility is not OutputVisibility.PUBLIC
        or channel not in _TEXT_CHANNELS
        or (
            kind is not OutputEventKind.PROVIDER_CONTENT_DELTA
            and "delta" not in payload
        )
    ):
        return
    if source is not OutputSource.PROVIDER:
        raise ValueError("public text requires provider source")
    if kind is not OutputEventKind.PROVIDER_CONTENT_DELTA:
        raise ValueError("public text requires provider content delta")
    if not output_stream_id or not invocation_id:
        raise ValueError("public text requires stream and invocation ids")
    if not isinstance(payload.get("delta"), str):
        raise ValueError("public text requires a string delta")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [name for name in globals() if not name.startswith("_")]
