"""Storage-neutral contracts for recoverable model-generated artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from purra.artifacts.scope import ArtifactScope
from purra.normalization import (
    non_negative_int,
    optional_non_negative_int,
    optional_text,
    positive_int,
    required_text,
    text_tuple,
    unique_text_tuple,
)
from purra.json_values import (
    canonical_json_digest,
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
    normalized_run = optional_text(run_id)
    normalized_work_item = optional_text(work_item_id)
    normalized_creator = optional_text(created_by_run_id)
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
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"artifact {name}"),
            )
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
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "artifact schema_version"),
        )
        object.__setattr__(
            self,
            "expected_item_count",
            optional_non_negative_int(
                self.expected_item_count,
                "artifact expected_item_count",
            ),
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
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"artifact {name}"),
            )
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
            object.__setattr__(
                self,
                name,
                positive_int(getattr(self, name), f"artifact {name}"),
            )
        object.__setattr__(
            self,
            "committed_item_count",
            non_negative_int(
                self.committed_item_count,
                "committed_item_count",
            ),
        )
        object.__setattr__(
            self,
            "expected_item_count",
            optional_non_negative_int(
                self.expected_item_count,
                "artifact expected_item_count",
            ),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))
        object.__setattr__(
            self,
            "resource_ref",
            optional_text(self.resource_ref),
        )
        object.__setattr__(
            self,
            "coverage_digest",
            optional_text(self.coverage_digest),
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
            object.__setattr__(
                self,
                name,
                required_text(
                    getattr(self, name),
                    f"artifact mutation lease {name}",
                ),
            )
        object.__setattr__(
            self,
            "lease_duration_ms",
            positive_int(
                self.lease_duration_ms,
                "artifact mutation lease duration",
            ),
        )


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
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"artifact batch {name}"),
            )
        for name in ("expected_revision", "sequence"):
            object.__setattr__(
                self,
                name,
                positive_int(getattr(self, name), f"artifact batch {name}"),
            )
        items = tuple(freeze_json_mapping(item) for item in self.items)
        if not items:
            raise ValueError("artifact batch must contain at least one item")
        object.__setattr__(self, "items", items)
        coverage = text_tuple(
            str(value or "").strip() for value in self.coverage_keys
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
        return canonical_json_digest(payload)


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
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"artifact batch {name}"),
            )
        for name in ("sequence", "committed_revision"):
            object.__setattr__(
                self,
                name,
                positive_int(getattr(self, name), f"artifact batch {name}"),
            )
        object.__setattr__(
            self,
            "items",
            tuple(freeze_json_mapping(item) for item in self.items),
        )
        object.__setattr__(self, "coverage_keys", tuple(self.coverage_keys))
        object.__setattr__(
            self,
            "content_digest",
            required_text(
                self.content_digest,
                "artifact batch content_digest",
            ),
        )


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
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"artifact receipt {name}"),
            )
        for name in ("sequence", "committed_revision", "next_sequence"):
            object.__setattr__(
                self,
                name,
                positive_int(getattr(self, name), f"artifact receipt {name}"),
            )
        object.__setattr__(
            self,
            "accepted_count",
            non_negative_int(
                self.accepted_count,
                "artifact accepted_count",
            ),
        )
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
        object.__setattr__(
            self,
            "artifact_id",
            required_text(self.artifact_id, "artifact_id"),
        )
        object.__setattr__(
            self,
            "expected_revision",
            positive_int(self.expected_revision, "expected_revision"),
        )
        object.__setattr__(
            self,
            "expected_item_count",
            optional_non_negative_int(
                self.expected_item_count,
                "artifact expected_item_count",
            ),
        )
        object.__setattr__(
            self,
            "expected_coverage_keys",
            unique_text_tuple(
                str(value or "").strip()
                for value in self.expected_coverage_keys
            ),
        )
        object.__setattr__(
            self,
            "resource_ref",
            optional_text(self.resource_ref),
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
        code = optional_text(self.code)
        if not self.accepted and not code:
            raise ValueError("rejected artifact validation requires a code")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "details", freeze_json_mapping(self.details))


def coverage_digest(keys: tuple[str, ...]) -> str:
    return canonical_json_digest(sorted(set(keys)))


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
