"""Closed lifecycle contracts for one authoritative Agent operation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from purra.contracts import RunId
from purra.json_values import freeze_json_mapping
from purra.normalization import (
    non_negative_int,
    optional_text,
    required_text,
)


class OperationKind(StrEnum):
    MODEL = "model"
    TOOL = "tool"
    VALIDATION = "validation"
    CONTEXT_COMPACTION = "context_compaction"
    DELEGATION = "delegation"


class OperationStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


_TERMINAL_STATUSES = frozenset({
    OperationStatus.SUCCEEDED,
    OperationStatus.FAILED,
    OperationStatus.CANCELED,
})


@dataclass(frozen=True, slots=True)
class OperationStarted:
    operation_id: str
    run_id: RunId
    invocation_id: str | None
    kind: OperationKind
    started_at: datetime
    display: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_id",
            required_text(self.operation_id, "operation id"),
        )
        object.__setattr__(self, "run_id", required_text(self.run_id, "run id"))
        object.__setattr__(
            self, "invocation_id", optional_text(self.invocation_id)
        )
        object.__setattr__(self, "kind", OperationKind(self.kind))
        _require_aware(self.started_at, "started_at")
        object.__setattr__(
            self,
            "display",
            freeze_json_mapping(self.display),
        )


@dataclass(frozen=True, slots=True)
class OperationFinished:
    operation_id: str
    run_id: RunId
    invocation_id: str | None
    status: OperationStatus
    finished_at: datetime
    duration_ms: int
    error_code: str | None = None
    display: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_id",
            required_text(self.operation_id, "operation id"),
        )
        object.__setattr__(self, "run_id", required_text(self.run_id, "run id"))
        object.__setattr__(
            self, "invocation_id", optional_text(self.invocation_id)
        )
        status = OperationStatus(self.status)
        if status not in _TERMINAL_STATUSES:
            raise ValueError("finished operation requires terminal status")
        error_code = optional_text(self.error_code)
        if status is OperationStatus.FAILED and error_code is None:
            raise ValueError("failed operation requires error code")
        if status is OperationStatus.SUCCEEDED and error_code is not None:
            raise ValueError("succeeded operation cannot include error code")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "error_code", error_code)
        _require_aware(self.finished_at, "finished_at")
        object.__setattr__(
            self,
            "duration_ms",
            non_negative_int(self.duration_ms, "duration"),
        )
        object.__setattr__(
            self,
            "display",
            freeze_json_mapping(self.display),
        )


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [name for name in globals() if not name.startswith("_")]
