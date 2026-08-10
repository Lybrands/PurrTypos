"""Product-owned authority for one actionable screenplay Agent request."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from purra.json_values import freeze_json_mapping
from purra.normalization import optional_text, required_text


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
class ScreenplayOperationRecord:
    id: str
    turn_id: str
    project_id: str
    session_id: int
    status: ScreenplayOperationStatus
    long_task_id: str | None
    target_role: str
    requirements_json: Mapping[str, Any]
    manifest_digest: str
    result_revision_id: str | None = None
    finalization_receipt_id: str | None = None
    cancel_receipt_id: str | None = None
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
        if self.error is not None:
            object.__setattr__(self, "error", freeze_json_mapping(self.error))


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


__all__ = [
    "ScreenplayOperationCreateCommand",
    "ScreenplayOperationRecord",
    "ScreenplayOperationStatus",
]
