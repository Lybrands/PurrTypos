"""Storage-neutral contracts for recoverable model-generated artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from agent_core.artifacts.scope import ArtifactScope
from agent_core.json_values import (
    freeze_json_mapping,
    thaw_json_mapping,
)


class ArtifactStatus(StrEnum):
    OPEN = "open"
    FINALIZED = "finalized"
    ABORTED = "aborted"


def _normalize_scope(
    *,
    scope: ArtifactScope | str,
    run_id: str | None,
    work_item_id: str | None,
    created_by_run_id: str | None,
) -> tuple[ArtifactScope, str | None, str | None, str | None]:
    normalized_scope = ArtifactScope(scope)
    normalized_run = str(run_id or "").strip() or None
    normalized_work_item = str(work_item_id or "").strip() or None
    normalized_creator = str(created_by_run_id or "").strip() or None
    if normalized_scope is ArtifactScope.RUN:
        if normalized_work_item is not None:
            raise ValueError("Run-scoped artifact cannot have work_item_id")
        if (
            normalized_run is not None
            and normalized_creator is not None
            and normalized_run != normalized_creator
        ):
            raise ValueError(
                "Run-scoped artifact must be owned by its creating Run"
            )
        owner_run = normalized_run or normalized_creator
        return normalized_scope, owner_run, None, owner_run
    creator_run = normalized_creator or normalized_run
    if normalized_work_item is None:
        raise ValueError("Work Item-scoped artifact requires work_item_id")
    if creator_run is None:
        raise ValueError(
            "Work Item-scoped artifact requires created_by_run_id"
        )
    if normalized_run is not None and normalized_run != creator_run:
        raise ValueError(
            "artifact run_id must match created_by_run_id provenance"
        )
    return normalized_scope, creator_run, normalized_work_item, creator_run


@dataclass(frozen=True, slots=True)
class ArtifactCreateCommand:
    namespace: str
    kind: str
    owner_id: str
    run_id: str | None = None
    schema_version: int = 1
    expected_item_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    scope: ArtifactScope = ArtifactScope.RUN
    work_item_id: str | None = None
    created_by_run_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("namespace", "kind", "owner_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact {name} is required")
            object.__setattr__(self, name, value)
        scope, run_id, work_item_id, created_by_run_id = _normalize_scope(
            scope=self.scope,
            run_id=self.run_id,
            work_item_id=self.work_item_id,
            created_by_run_id=self.created_by_run_id,
        )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "work_item_id", work_item_id)
        object.__setattr__(self, "created_by_run_id", created_by_run_id)
        version = int(self.schema_version)
        if version <= 0:
            raise ValueError("artifact schema_version must be positive")
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(
            self,
            "expected_item_count",
            _optional_non_negative(self.expected_item_count, "expected_item_count"),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    namespace: str
    kind: str
    owner_id: str
    run_id: str | None
    schema_version: int
    status: ArtifactStatus = ArtifactStatus.OPEN
    revision: int = 1
    next_sequence: int = 1
    committed_item_count: int = 0
    expected_item_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    resource_ref: str | None = None
    coverage_digest: str | None = None
    scope: ArtifactScope = ArtifactScope.RUN
    work_item_id: str | None = None
    created_by_run_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "namespace", "kind", "owner_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact {name} is required")
            object.__setattr__(self, name, value)
        scope, run_id, work_item_id, created_by_run_id = _normalize_scope(
            scope=self.scope,
            run_id=self.run_id,
            work_item_id=self.work_item_id,
            created_by_run_id=self.created_by_run_id,
        )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "work_item_id", work_item_id)
        object.__setattr__(self, "created_by_run_id", created_by_run_id)
        object.__setattr__(self, "status", ArtifactStatus(self.status))
        for name in ("schema_version", "revision", "next_sequence"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"artifact {name} must be positive")
            object.__setattr__(self, name, value)
        count = int(self.committed_item_count)
        if count < 0:
            raise ValueError("committed_item_count must be non-negative")
        object.__setattr__(self, "committed_item_count", count)
        object.__setattr__(
            self,
            "expected_item_count",
            _optional_non_negative(self.expected_item_count, "expected_item_count"),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        object.__setattr__(
            self,
            "resource_ref",
            str(self.resource_ref or "").strip() or None,
        )
        object.__setattr__(
            self,
            "coverage_digest",
            str(self.coverage_digest or "").strip() or None,
        )

    @property
    def scope_id(self) -> str | None:
        if self.scope is ArtifactScope.RUN:
            return self.run_id
        return self.work_item_id


@dataclass(frozen=True, slots=True)
class ArtifactMutationLease:
    """Opaque proof that one Run currently owns a Work Item Artifact write.

    The repository, rather than a caller, decides whether the token is live and
    belongs to the Artifact.  ``lease_duration_ms`` is only a bounded renewal
    request applied atomically with a successful mutation.
    """

    run_id: str
    claim_token: str
    lease_duration_ms: int = 300_000

    def __post_init__(self) -> None:
        for name in ("run_id", "claim_token"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact mutation lease {name} is required")
            object.__setattr__(self, name, value)
        duration = int(self.lease_duration_ms)
        if duration <= 0:
            raise ValueError("artifact mutation lease duration must be positive")
        object.__setattr__(self, "lease_duration_ms", duration)


@dataclass(frozen=True, slots=True)
class ArtifactAppendCommand:
    artifact_id: str
    expected_revision: int
    sequence: int
    batch_id: str
    idempotency_key: str
    items: tuple[Mapping[str, Any], ...]
    coverage_keys: tuple[str, ...] = ()
    write_lease: ArtifactMutationLease | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "batch_id", "idempotency_key"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact batch {name} is required")
            object.__setattr__(self, name, value)
        for name in ("expected_revision", "sequence"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"artifact batch {name} must be positive")
            object.__setattr__(self, name, value)
        items = tuple(freeze_json_mapping(item) for item in self.items)
        if not items:
            raise ValueError("artifact batch must contain at least one item")
        object.__setattr__(self, "items", items)
        coverage = tuple(
            str(value or "").strip()
            for value in self.coverage_keys
            if str(value or "").strip()
        )
        object.__setattr__(self, "coverage_keys", coverage)
        if self.write_lease is not None and not isinstance(
            self.write_lease,
            ArtifactMutationLease,
        ):
            raise TypeError("artifact append write_lease is invalid")

    @property
    def content_digest(self) -> str:
        payload = {
            "artifactId": self.artifact_id,
            "sequence": self.sequence,
            "batchId": self.batch_id,
            "items": [thaw_json_mapping(item) for item in self.items],
            "coverageKeys": list(self.coverage_keys),
        }
        return hashlib.sha256(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactBatch:
    artifact_id: str
    batch_id: str
    idempotency_key: str
    sequence: int
    committed_revision: int
    items: tuple[Mapping[str, Any], ...]
    coverage_keys: tuple[str, ...] = ()
    content_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("artifact_id", "batch_id", "idempotency_key"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact batch {name} is required")
            object.__setattr__(self, name, value)
        for name in ("sequence", "committed_revision"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"artifact batch {name} must be positive")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "items",
            tuple(freeze_json_mapping(item) for item in self.items),
        )
        object.__setattr__(self, "coverage_keys", tuple(self.coverage_keys))
        digest = str(self.content_digest or "").strip()
        if not digest:
            raise ValueError("artifact batch content_digest is required")
        object.__setattr__(self, "content_digest", digest)


@dataclass(frozen=True, slots=True)
class ArtifactBatchReceipt:
    artifact_id: str
    batch_id: str
    sequence: int
    committed_revision: int
    next_sequence: int
    accepted_count: int
    replayed: bool = False

    def __post_init__(self) -> None:
        for name in ("artifact_id", "batch_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact receipt {name} is required")
            object.__setattr__(self, name, value)
        for name in ("sequence", "committed_revision", "next_sequence"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"artifact receipt {name} must be positive")
            object.__setattr__(self, name, value)
        count = int(self.accepted_count)
        if count < 0:
            raise ValueError("artifact accepted_count must be non-negative")
        object.__setattr__(self, "accepted_count", count)
        object.__setattr__(self, "replayed", bool(self.replayed))


@dataclass(frozen=True, slots=True)
class ArtifactFinalizeCommand:
    artifact_id: str
    expected_revision: int
    expected_item_count: int | None = None
    expected_coverage_keys: tuple[str, ...] = ()
    resource_ref: str | None = None
    write_lease: ArtifactMutationLease | None = None
    complete_work_item: bool = False

    def __post_init__(self) -> None:
        artifact_id = str(self.artifact_id or "").strip()
        if not artifact_id:
            raise ValueError("artifact_id is required")
        object.__setattr__(self, "artifact_id", artifact_id)
        revision = int(self.expected_revision)
        if revision <= 0:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "expected_revision", revision)
        object.__setattr__(
            self,
            "expected_item_count",
            _optional_non_negative(self.expected_item_count, "expected_item_count"),
        )
        object.__setattr__(self, "expected_coverage_keys", tuple(dict.fromkeys(
            str(value or "").strip()
            for value in self.expected_coverage_keys
            if str(value or "").strip()
        )))
        object.__setattr__(
            self,
            "resource_ref",
            str(self.resource_ref or "").strip() or None,
        )
        if self.write_lease is not None and not isinstance(
            self.write_lease,
            ArtifactMutationLease,
        ):
            raise TypeError("artifact finalize write_lease is invalid")
        object.__setattr__(
            self,
            "complete_work_item",
            bool(self.complete_work_item),
        )


@dataclass(frozen=True, slots=True)
class ArtifactValidationResult:
    accepted: bool
    code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted", bool(self.accepted))
        code = str(self.code or "").strip() or None
        if not self.accepted and not code:
            raise ValueError("rejected artifact validation requires a code")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "details", freeze_json_mapping(self.details))


def coverage_digest(keys: tuple[str, ...]) -> str:
    canonical = json.dumps(
        sorted(set(keys)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _optional_non_negative(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"artifact {name} must be non-negative")
    return normalized


__all__ = [
    "ArtifactAppendCommand",
    "ArtifactBatch",
    "ArtifactBatchReceipt",
    "ArtifactCreateCommand",
    "ArtifactFinalizeCommand",
    "ArtifactMutationLease",
    "ArtifactRecord",
    "ArtifactStatus",
    "ArtifactValidationResult",
    "coverage_digest",
]
