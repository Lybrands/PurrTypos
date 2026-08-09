"""Storage-neutral contracts for durable, multi-Run work items."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.contracts.normalization import (
    optional_text,
    positive_int,
    required_text,
)
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
            object.__setattr__(self, name, required_text(getattr(self, name), f"work item {name}"))
        object.__setattr__(self, "created_by_run_id", optional_text(self.created_by_run_id))
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
            object.__setattr__(self, name, required_text(getattr(self, name), f"work item {name}"))
        object.__setattr__(self, "created_by_run_id", optional_text(self.created_by_run_id))
        object.__setattr__(self, "status", WorkItemStatus(self.status))
        object.__setattr__(self, "revision", positive_int(self.revision, "work item revision"))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkItemTransitionCommand:
    work_item_id: str
    expected_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_id", required_text(self.work_item_id, "work_item_id"))
        object.__setattr__(self, "expected_revision", positive_int(self.expected_revision, "expected_revision"))


@dataclass(frozen=True, slots=True)
class WorkItemRunLinkCommand:
    work_item_id: str
    run_id: str
    relation: WorkItemRunRelation
    expected_revision: int

    def __post_init__(self) -> None:
        for name in ("work_item_id", "run_id"):
            object.__setattr__(self, name, required_text(getattr(self, name), f"work item Run link {name}"))
        object.__setattr__(self, "relation", WorkItemRunRelation(self.relation))
        object.__setattr__(self, "expected_revision", positive_int(self.expected_revision, "expected_revision"))


@dataclass(frozen=True, slots=True)
class WorkItemRunLink:
    work_item_id: str
    run_id: str
    relation: WorkItemRunRelation
    work_item_revision: int

    def __post_init__(self) -> None:
        for name in ("work_item_id", "run_id"):
            object.__setattr__(self, name, required_text(getattr(self, name), f"work item Run link {name}"))
        object.__setattr__(self, "relation", WorkItemRunRelation(self.relation))
        object.__setattr__(self, "work_item_revision", positive_int(self.work_item_revision, "work_item_revision"))


__all__ = [
    "WorkItemCreateCommand",
    "WorkItemRecord",
    "WorkItemRunLink",
    "WorkItemRunLinkCommand",
    "WorkItemRunRelation",
    "WorkItemStatus",
    "WorkItemTransitionCommand",
]
