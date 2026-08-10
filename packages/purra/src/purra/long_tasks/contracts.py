"""Storage-neutral contracts for durable execution spanning many Runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any, Mapping

from purra.normalization import (
    non_negative_int,
    optional_text,
    positive_int,
    required_text,
    unique_text_tuple,
)
from purra.json_values import freeze_json_mapping


class LongTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            LongTaskStatus.COMPLETED,
            LongTaskStatus.FAILED,
            LongTaskStatus.CANCELED,
        }


class LongTaskUnitStatus(StrEnum):
    PENDING = "pending"
    WAITING_RETRY = "waiting_retry"
    CLAIMED = "claimed"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            LongTaskUnitStatus.COMPLETED,
            LongTaskUnitStatus.FAILED,
            LongTaskUnitStatus.CANCELED,
        }


@dataclass(frozen=True, slots=True)
class LongTaskUnitSpec:
    id: str
    position: int
    dependencies: tuple[str, ...] = ()
    input_ref: str | None = None
    max_attempts: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            required_text(self.id, "long task unit id"),
        )
        object.__setattr__(
            self,
            "position",
            non_negative_int(self.position, "long task unit position"),
        )
        dependencies = unique_text_tuple(
            str(item or "").strip() for item in self.dependencies
        )
        if self.id in dependencies:
            raise ValueError("long task unit cannot depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "input_ref", optional_text(self.input_ref))
        object.__setattr__(
            self,
            "max_attempts",
            positive_int(self.max_attempts, "long task unit max_attempts"),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class LongTaskCreateCommand:
    namespace: str
    kind: str
    owner_id: str
    work_item_id: str
    created_by_run_id: str
    units: tuple[LongTaskUnitSpec, ...]
    max_parallelism: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "namespace",
            "kind",
            "owner_id",
            "work_item_id",
            "created_by_run_id",
        ):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"long task {name}"),
            )
        units = tuple(self.units)
        if not units:
            raise ValueError("long task requires at least one execution unit")
        ids = tuple(item.id for item in units)
        positions = tuple(item.position for item in units)
        if len(ids) != len(set(ids)):
            raise ValueError("long task unit ids must be unique")
        if len(positions) != len(set(positions)):
            raise ValueError("long task unit positions must be unique")
        known = set(ids)
        missing = {
            dependency
            for unit in units
            for dependency in unit.dependencies
            if dependency not in known
        }
        if missing:
            raise ValueError(
                "long task unit dependencies are unknown: "
                + ", ".join(sorted(missing))
            )
        _require_acyclic(units)
        object.__setattr__(self, "units", units)
        object.__setattr__(
            self,
            "max_parallelism",
            positive_int(self.max_parallelism, "long task max_parallelism"),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class LongTaskRecord:
    id: str
    namespace: str
    kind: str
    owner_id: str
    work_item_id: str
    created_by_run_id: str
    status: LongTaskStatus
    revision: int
    total_units: int
    completed_units: int
    failed_units: int
    max_parallelism: int
    metadata: Mapping[str, Any] = field(default_factory=dict)
    create_time: str | None = None
    update_time: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "id",
            "namespace",
            "kind",
            "owner_id",
            "work_item_id",
            "created_by_run_id",
        ):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"long task record {name}"),
            )
        object.__setattr__(self, "status", LongTaskStatus(self.status))
        for name in ("revision", "total_units", "max_parallelism"):
            object.__setattr__(
                self,
                name,
                positive_int(getattr(self, name), f"long task record {name}"),
            )
        for name in ("completed_units", "failed_units"):
            object.__setattr__(
                self,
                name,
                non_negative_int(
                    getattr(self, name),
                    f"long task record {name}",
                ),
            )
        if self.completed_units + self.failed_units > self.total_units:
            raise ValueError("long task progress exceeds total units")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        for name in ("create_time", "update_time"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name)),
            )


@dataclass(frozen=True, slots=True)
class LongTaskUnitRecord:
    task_id: str
    id: str
    position: int
    status: LongTaskUnitStatus
    dependencies: tuple[str, ...] = ()
    attempt: int = 0
    max_attempts: int = 3
    worker_id: str | None = None
    lease_expires_at_ms: int | None = None
    run_id: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    error_code: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    create_time: str | None = None
    update_time: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "id"):
            object.__setattr__(
                self,
                name,
                required_text(
                    getattr(self, name),
                    f"long task unit record {name}",
                ),
            )
        object.__setattr__(self, "status", LongTaskUnitStatus(self.status))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        for name in ("position", "attempt", "max_attempts"):
            normalizer = positive_int if name == "max_attempts" else non_negative_int
            object.__setattr__(
                self,
                name,
                normalizer(
                    getattr(self, name),
                    f"long task unit record {name}",
                ),
            )
        for name in ("worker_id", "run_id", "input_ref", "output_ref", "error_code"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name)),
            )
        if self.lease_expires_at_ms is not None:
            object.__setattr__(
                self,
                "lease_expires_at_ms",
                int(self.lease_expires_at_ms),
            )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        for name in ("create_time", "update_time"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name)),
            )


@dataclass(frozen=True, slots=True)
class LongTaskUnitResult:
    output_ref: str
    run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output_ref",
            required_text(self.output_ref, "long task unit result output_ref"),
        )
        object.__setattr__(self, "run_id", optional_text(self.run_id))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


def _require_acyclic(units: tuple[LongTaskUnitSpec, ...]) -> None:
    try:
        tuple(TopologicalSorter({
            unit.id: unit.dependencies for unit in units
        }).static_order())
    except CycleError as error:
        raise ValueError(
            "long task unit dependencies contain a cycle"
        ) from error


__all__ = [
    "LongTaskCreateCommand",
    "LongTaskRecord",
    "LongTaskStatus",
    "LongTaskUnitRecord",
    "LongTaskUnitResult",
    "LongTaskUnitSpec",
    "LongTaskUnitStatus",
]
