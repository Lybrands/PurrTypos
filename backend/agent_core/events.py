"""Stable Core event and inbound command envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.contracts import ApprovalDecision, RunId
from agent_core.json_values import freeze_json_mapping


class CoreEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_TODOS_UPDATED = "run.todos_updated"
    RUN_TODO_UPDATED = "run.todo_updated"
    MODEL_DELTA = "model.delta"
    MODEL_THINKING_DELTA = "model.thinking_delta"
    TOOL_CALLS_STARTED = "tool.calls_started"
    TOOL_CALL_COMPLETED = "tool.call_completed"
    TOOL_RESULTS = "tool.results"
    TOOL_ROUND_COMPLETED = "tool.round_completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    CONTEXT_BUDGETED = "context.budgeted"
    RUN_COMPLETED = "run.completed"
    RUN_BLOCKED = "run.blocked"
    RUN_FAILED = "run.failed"
    RUN_CANCELED = "run.canceled"


class CoreCommandType(StrEnum):
    APPROVAL_RESOLVE = "approval.resolve"
    RUN_CANCEL = "run.cancel"
    CLIENT_DISCONNECTED = "client.disconnected"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """An event envelope; ``type`` remains open for domain-owned effects."""

    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    run_id: RunId | None = None

    def __post_init__(self) -> None:
        event_type = str(self.type or "").strip()
        if not event_type:
            raise ValueError("event type is required")
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class AgentCommand:
    type: CoreCommandType
    payload: Mapping[str, Any] = field(default_factory=dict)
    run_id: RunId | None = None

    def __post_init__(self) -> None:
        command_type = (
            self.type
            if isinstance(self.type, CoreCommandType)
            else CoreCommandType(str(self.type))
        )
        object.__setattr__(self, "type", command_type)
        if not str(self.run_id or "").strip():
            raise ValueError(f"{command_type} command requires a run id")
        payload = dict(freeze_json_mapping(self.payload))
        if command_type is CoreCommandType.APPROVAL_RESOLVE:
            if not str(payload.get("approval_id") or "").strip():
                raise ValueError("approval.resolve requires approval_id")
            try:
                decision = ApprovalDecision(str(payload.get("decision") or ""))
            except ValueError:
                raise ValueError("approval.resolve requires decision")
            payload["decision"] = decision.value
        object.__setattr__(self, "payload", freeze_json_mapping(payload))
