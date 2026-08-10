"""Product-owned authority for one actionable screenplay Agent request."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from purra.json_values import freeze_json_mapping
from purra.normalization import non_negative_int, optional_text, required_text


class ScreenplayOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            ScreenplayOperationStatus.SUCCEEDED,
            ScreenplayOperationStatus.FAILED,
            ScreenplayOperationStatus.CANCELED,
        }


@dataclass(frozen=True, slots=True)
class OperationUsage:
    invocation_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = 0

    def __post_init__(self) -> None:
        for name in ("invocation_count", "input_tokens", "output_tokens"):
            object.__setattr__(
                self,
                name,
                non_negative_int(getattr(self, name), f"Operation usage {name}"),
            )
        if self.reasoning_tokens is not None:
            object.__setattr__(
                self,
                "reasoning_tokens",
                non_negative_int(
                    self.reasoning_tokens,
                    "Operation usage reasoning_tokens",
                ),
            )

    def to_mapping(self) -> dict[str, int | None]:
        return {
            "invocationCount": self.invocation_count,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
        }


@dataclass(frozen=True, slots=True)
class ScreenplayOperationRecord:
    id: str
    turn_id: str
    project_id: str
    session_id: int
    status: ScreenplayOperationStatus
    revision: int
    long_task_id: str | None
    target_role: str
    requirements_json: Mapping[str, Any]
    manifest_digest: str
    result_revision_id: str | None = None
    finalization_receipt_id: str | None = None
    cancel_receipt_id: str | None = None
    cancel_requested_at_ms: int | None = None
    usage: OperationUsage = field(default_factory=OperationUsage)
    error: Mapping[str, Any] | None = None
    create_time: str | None = None
    update_time: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "turn_id", "project_id", "target_role"):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"screenplay operation {name}"),
            )
        object.__setattr__(self, "session_id", int(self.session_id))
        object.__setattr__(self, "status", ScreenplayOperationStatus(self.status))
        revision = int(self.revision)
        if revision <= 0:
            raise ValueError("screenplay operation revision must be positive")
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "long_task_id", optional_text(self.long_task_id))
        object.__setattr__(
            self,
            "manifest_digest",
            required_text(
                self.manifest_digest,
                "screenplay operation manifest_digest",
            ),
        )
        object.__setattr__(
            self,
            "requirements_json",
            freeze_json_mapping(self.requirements_json),
        )
        for name in (
            "result_revision_id",
            "finalization_receipt_id",
            "cancel_receipt_id",
            "create_time",
            "update_time",
        ):
            object.__setattr__(self, name, optional_text(getattr(self, name)))
        if self.cancel_requested_at_ms is not None:
            requested_at = int(self.cancel_requested_at_ms)
            if requested_at < 0:
                raise ValueError("screenplay operation cancel timestamp is invalid")
            object.__setattr__(self, "cancel_requested_at_ms", requested_at)
        if self.error is not None:
            object.__setattr__(self, "error", freeze_json_mapping(self.error))
        if not isinstance(self.usage, OperationUsage):
            raise TypeError("screenplay operation usage must be OperationUsage")


@dataclass(frozen=True, slots=True)
class ScreenplayOperationCreateCommand:
    turn_id: str
    project_id: str
    session_id: int
    target_role: str
    requirements_json: Mapping[str, Any] = field(default_factory=dict)
    manifest_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("turn_id", "project_id", "target_role", "manifest_digest"):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"screenplay operation {name}"),
            )
        object.__setattr__(self, "session_id", int(self.session_id))
        object.__setattr__(
            self,
            "requirements_json",
            freeze_json_mapping(self.requirements_json),
        )


@dataclass(frozen=True, slots=True)
class CancelOperationReceipt:
    id: str
    operation_id: str | None
    turn_id: str
    requested_at: str
    terminal_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", required_text(self.id, "cancel receipt id"))
        object.__setattr__(
            self,
            "operation_id",
            optional_text(self.operation_id),
        )
        object.__setattr__(
            self,
            "turn_id",
            required_text(self.turn_id, "cancel receipt turn id"),
        )
        object.__setattr__(
            self,
            "requested_at",
            required_text(self.requested_at, "cancel receipt requested at"),
        )
        status = required_text(
            self.terminal_status,
            "cancel receipt terminal status",
        )
        if status not in {
            "cancel_requested",
            "succeeded",
            "failed",
            "canceled",
        }:
            raise ValueError("cancel receipt terminal status is invalid")
        object.__setattr__(self, "terminal_status", status)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operationId": self.operation_id,
            "turnId": self.turn_id,
            "requestedAt": self.requested_at,
            "terminalStatus": self.terminal_status,
            "cancelReceiptId": self.id,
        }


__all__ = [
    "CancelOperationReceipt",
    "OperationUsage",
    "ScreenplayOperationCreateCommand",
    "ScreenplayOperationRecord",
    "ScreenplayOperationStatus",
]
