"""Storage-neutral contracts for durable, multi-Run work items."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.json_values import freeze_json_mapping


class WorkItemStatus(StrEnum):
    """Content lifecycle of one durable task.

    Whether a Run is currently executing the task is represented by Run links
    and execution leases, not by adding transient states here.
    """

    OPEN = "open"
    COMPLETED = "completed"
    CANCELED = "canceled"


class WorkItemRunRelation(StrEnum):
    """Why one Run is linked to a Work Item."""

    CREATED = "created"
    CONTINUATION = "continuation"
    REFERENCE = "reference"

    @property
    def writes_work_item(self) -> bool:
        return self in {
            WorkItemRunRelation.CREATED,
            WorkItemRunRelation.CONTINUATION,
        }


@dataclass(frozen=True, slots=True)
class WorkItemCreateCommand:
    namespace: str
    kind: str
    owner_id: str
    created_by_run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("namespace", "kind", "owner_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"work item {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "created_by_run_id",
            str(self.created_by_run_id or "").strip() or None,
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkItemRecord:
    id: str
    namespace: str
    kind: str
    owner_id: str
    created_by_run_id: str | None = None
    status: WorkItemStatus = WorkItemStatus.OPEN
    revision: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "namespace", "kind", "owner_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"work item {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "created_by_run_id",
            str(self.created_by_run_id or "").strip() or None,
        )
        object.__setattr__(self, "status", WorkItemStatus(self.status))
        revision = int(self.revision)
        if revision <= 0:
            raise ValueError("work item revision must be positive")
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkItemTransitionCommand:
    work_item_id: str
    expected_revision: int

    def __post_init__(self) -> None:
        work_item_id = str(self.work_item_id or "").strip()
        if not work_item_id:
            raise ValueError("work_item_id is required")
        object.__setattr__(self, "work_item_id", work_item_id)
        revision = int(self.expected_revision)
        if revision <= 0:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "expected_revision", revision)


@dataclass(frozen=True, slots=True)
class WorkItemRunLinkCommand:
    work_item_id: str
    run_id: str
    relation: WorkItemRunRelation
    expected_revision: int

    def __post_init__(self) -> None:
        for name in ("work_item_id", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"work item Run link {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "relation", WorkItemRunRelation(self.relation))
        revision = int(self.expected_revision)
        if revision <= 0:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "expected_revision", revision)


@dataclass(frozen=True, slots=True)
class WorkItemRunLink:
    work_item_id: str
    run_id: str
    relation: WorkItemRunRelation
    work_item_revision: int

    def __post_init__(self) -> None:
        for name in ("work_item_id", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"work item Run link {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "relation", WorkItemRunRelation(self.relation))
        revision = int(self.work_item_revision)
        if revision <= 0:
            raise ValueError("work_item_revision must be positive")
        object.__setattr__(self, "work_item_revision", revision)


__all__ = [
    "WorkItemCreateCommand",
    "WorkItemRecord",
    "WorkItemRunLink",
    "WorkItemRunLinkCommand",
    "WorkItemRunRelation",
    "WorkItemStatus",
    "WorkItemTransitionCommand",
]
