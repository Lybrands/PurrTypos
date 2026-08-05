"""Business-neutral decisions between one Run and durable execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.events import AgentEvent

from agent_core.json_values import freeze_json_mapping


class ExecutionMode(StrEnum):
    INLINE = "inline"
    DURABLE = "durable"
    CLARIFY = "clarify"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class TaskAdmissionDecision:
    mode: ExecutionMode = ExecutionMode.INLINE
    reason_code: str = "inline_default"
    estimated_units: int = 1
    estimated_model_calls: int = 1
    requires_confirmation: bool = False
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", ExecutionMode(self.mode))
        reason = str(self.reason_code or "").strip()
        if not reason:
            raise ValueError("task admission reason_code is required")
        object.__setattr__(self, "reason_code", reason)
        for name in ("estimated_units", "estimated_model_calls"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"task admission {name} must be non-negative")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "requires_confirmation",
            bool(self.requires_confirmation),
        )
        message = str(self.message or "").strip() or None
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))

    def to_event_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "reasonCode": self.reason_code,
            "estimatedUnits": self.estimated_units,
            "estimatedModelCalls": self.estimated_model_calls,
            "requiresConfirmation": self.requires_confirmation,
        }


@dataclass(frozen=True, slots=True)
class LongTaskDispatchReceipt:
    task_id: str
    message: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = str(self.task_id or "").strip()
        if not task_id:
            raise ValueError("long task dispatch task_id is required")
        object.__setattr__(self, "task_id", task_id)
        message = str(self.message or "").strip()
        if not message:
            raise ValueError("long task dispatch message is required")
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


class LongTaskExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class LongTaskExecutionUpdate:
    """One canonical parent-stream event emitted while a durable task runs."""

    event: AgentEvent
    persist: bool = True


@dataclass(frozen=True, slots=True)
class LongTaskExecutionResult:
    task_id: str
    status: LongTaskExecutionStatus
    final_response: str = ""
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = str(self.task_id or "").strip()
        if not task_id:
            raise ValueError("long task execution task_id is required")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "status", LongTaskExecutionStatus(self.status))
        object.__setattr__(self, "final_response", str(self.final_response or ""))
        object.__setattr__(self, "error", str(self.error or "").strip() or None)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


__all__ = [
    "ExecutionMode",
    "LongTaskDispatchReceipt",
    "LongTaskExecutionResult",
    "LongTaskExecutionStatus",
    "LongTaskExecutionUpdate",
    "TaskAdmissionDecision",
]
