"""Storage-neutral contracts for durable execution spanning many Runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.json_values import freeze_json_mapping


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
    CLAIMED = "claimed"
    RUNNING = "running"
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
        unit_id = str(self.id or "").strip()
        if not unit_id:
            raise ValueError("long task unit id is required")
        object.__setattr__(self, "id", unit_id)
        position = int(self.position)
        if position < 0:
            raise ValueError("long task unit position must be non-negative")
        object.__setattr__(self, "position", position)
        dependencies = tuple(dict.fromkeys(
            str(item or "").strip()
            for item in self.dependencies
            if str(item or "").strip()
        ))
        if unit_id in dependencies:
            raise ValueError("long task unit cannot depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(
            self,
            "input_ref",
            str(self.input_ref or "").strip() or None,
        )
        attempts = int(self.max_attempts)
        if attempts <= 0:
            raise ValueError("long task unit max_attempts must be positive")
        object.__setattr__(self, "max_attempts", attempts)
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
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"long task {name} is required")
            object.__setattr__(self, name, value)
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
        parallelism = int(self.max_parallelism)
        if parallelism <= 0:
            raise ValueError("long task max_parallelism must be positive")
        object.__setattr__(self, "max_parallelism", parallelism)
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
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"long task record {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "status", LongTaskStatus(self.status))
        for name in (
            "revision",
            "total_units",
            "completed_units",
            "failed_units",
            "max_parallelism",
        ):
            value = int(getattr(self, name))
            if value < 0 or (name in {"revision", "total_units", "max_parallelism"} and value == 0):
                raise ValueError(f"long task record {name} is invalid")
            object.__setattr__(self, name, value)
        if self.completed_units + self.failed_units > self.total_units:
            raise ValueError("long task progress exceeds total units")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        for name in ("create_time", "update_time"):
            object.__setattr__(
                self,
                name,
                str(getattr(self, name) or "").strip() or None,
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
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"long task unit record {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "status", LongTaskUnitStatus(self.status))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        for name in ("position", "attempt", "max_attempts"):
            value = int(getattr(self, name))
            if value < 0 or (name == "max_attempts" and value == 0):
                raise ValueError(f"long task unit record {name} is invalid")
            object.__setattr__(self, name, value)
        for name in ("worker_id", "run_id", "input_ref", "output_ref", "error_code"):
            object.__setattr__(
                self,
                name,
                str(getattr(self, name) or "").strip() or None,
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
                str(getattr(self, name) or "").strip() or None,
            )


@dataclass(frozen=True, slots=True)
class LongTaskUnitResult:
    output_ref: str
    run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        output_ref = str(self.output_ref or "").strip()
        if not output_ref:
            raise ValueError("long task unit result output_ref is required")
        object.__setattr__(self, "output_ref", output_ref)
        object.__setattr__(self, "run_id", str(self.run_id or "").strip() or None)
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


def _require_acyclic(units: tuple[LongTaskUnitSpec, ...]) -> None:
    dependencies = {unit.id: set(unit.dependencies) for unit in units}
    pending = set(dependencies)
    while pending:
        ready = {item for item in pending if not dependencies[item].intersection(pending)}
        if not ready:
            raise ValueError("long task unit dependencies contain a cycle")
        pending.difference_update(ready)


__all__ = [
    "LongTaskCreateCommand",
    "LongTaskRecord",
    "LongTaskStatus",
    "LongTaskUnitRecord",
    "LongTaskUnitResult",
    "LongTaskUnitSpec",
    "LongTaskUnitStatus",
]
