"""Versioned story-state contracts sourced from chapter evidence.

Story memory models authoritative project state and the chapter-scoped changes
that produced it. General semantic recall is owned by the PurrA memory component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class StoryMemoryKind(StrEnum):
    CHARACTER_STATE = "character_state"
    RELATIONSHIP_STATE = "relationship_state"
    WORLD_FACT = "world_fact"
    TIMELINE_EVENT = "timeline_event"
    PLOT_THREAD = "plot_thread"


class StoryMemoryStatus(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    DISPUTED = "disputed"
    DEPRECATED = "deprecated"


class StoryMemoryLifecycle(StrEnum):
    ACTIVE = "active"
    REVERTED = "reverted"


class StoryMemoryProvenanceStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"


class StoryMemoryOperation(StrEnum):
    UPSERT = "upsert"
    REMOVE = "remove"


class StoryMemoryDeltaStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    NEEDS_REVIEW = "needs_review"
    REVERTED = "reverted"
    INVALIDATED = "invalidated"


class StoryMemorySourceStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SourceReference:
    chapter_id: str
    excerpt: str
    source_revision: str = ""
    status: StoryMemorySourceStatus = StoryMemorySourceStatus.VALID
    locator: Mapping[str, Any] = field(default_factory=dict)
    narrative_order: int | None = None
    story_time: str | None = None
    story_time_precision: str | None = None


@dataclass(frozen=True, slots=True)
class StoryMemoryChange:
    target_key: str
    kind: StoryMemoryKind
    operation: StoryMemoryOperation
    source: SourceReference
    subject_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    status: StoryMemoryStatus = StoryMemoryStatus.CONFIRMED
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class StoryMemoryDeltaDraft:
    book_id: str
    chapter_id: str
    changes: tuple[StoryMemoryChange, ...]
    source_revision: str = ""
    note: str = ""
    source_type: str = "manual"


@dataclass(frozen=True, slots=True)
class StoryMemoryDelta:
    id: str
    book_id: str
    chapter_id: str
    changes: tuple[StoryMemoryChange, ...]
    status: StoryMemoryDeltaStatus
    source_revision: str = ""
    note: str = ""
    source_type: str = "manual"
    create_time: str | None = None
    applied_at: str | None = None
    reverted_at: str | None = None
    invalidated_at: str | None = None


@dataclass(frozen=True, slots=True)
class StoryMemoryRecord:
    id: str
    book_id: str
    memory_key: str
    kind: StoryMemoryKind
    subject_id: str | None
    payload: Mapping[str, Any]
    status: StoryMemoryStatus
    lifecycle: StoryMemoryLifecycle
    provenance_status: StoryMemoryProvenanceStatus
    version: int
    last_delta_id: str | None
    last_source_id: str | None
    create_time: str | None = None
    update_time: str | None = None


@dataclass(frozen=True, slots=True)
class StoryMemoryVersion:
    record_id: str
    book_id: str
    memory_key: str
    version: int
    delta_id: str
    action: str
    payload: Mapping[str, Any]
    status: StoryMemoryStatus
    lifecycle: StoryMemoryLifecycle
    provenance_status: StoryMemoryProvenanceStatus
    source_id: str | None
    create_time: str | None = None


@dataclass(frozen=True, slots=True)
class StoryMemoryApplyReceipt:
    delta_id: str
    status: StoryMemoryDeltaStatus
    changed_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChapterMemoryInvalidationReceipt:
    chapter_id: str
    stale_sources: int
    invalidated_pending_deltas: int
    review_required_deltas: int
    stale_records: int


class StoryMemoryError(RuntimeError):
    """Base class for invalid story-memory transitions."""


class StoryMemoryConflictError(StoryMemoryError):
    """The current state no longer matches the delta being transitioned."""


class StoryMemoryNotFoundError(StoryMemoryError):
    """A requested story-memory record or delta does not exist."""


def validate_delta_draft(draft: StoryMemoryDeltaDraft) -> None:
    book_id = str(draft.book_id or "").strip()
    chapter_id = str(draft.chapter_id or "").strip()
    if not book_id:
        raise ValueError("book_id is required")
    if not chapter_id:
        raise ValueError("chapter_id is required")
    if not draft.changes:
        raise ValueError("a story-memory delta requires at least one change")

    seen: set[str] = set()
    for change in draft.changes:
        target_key = str(change.target_key or "").strip()
        if not target_key:
            raise ValueError("change target_key is required")
        if target_key in seen:
            raise ValueError("a delta cannot change the same target_key twice")
        seen.add(target_key)
        if str(change.source.chapter_id or "").strip() != chapter_id:
            raise ValueError("every source reference must belong to the delta chapter")
        if change.source.status is not StoryMemorySourceStatus.VALID:
            raise ValueError("new source references must be valid")
        if not str(change.source.excerpt or "").strip():
            raise ValueError("source excerpt is required")
        confidence = float(change.confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if change.operation is StoryMemoryOperation.UPSERT and not change.payload:
            raise ValueError("upsert changes require a payload")


__all__ = [
    "ChapterMemoryInvalidationReceipt",
    "SourceReference",
    "StoryMemoryApplyReceipt",
    "StoryMemoryChange",
    "StoryMemoryConflictError",
    "StoryMemoryDelta",
    "StoryMemoryDeltaDraft",
    "StoryMemoryDeltaStatus",
    "StoryMemoryError",
    "StoryMemoryKind",
    "StoryMemoryLifecycle",
    "StoryMemoryNotFoundError",
    "StoryMemoryOperation",
    "StoryMemoryProvenanceStatus",
    "StoryMemoryRecord",
    "StoryMemorySourceStatus",
    "StoryMemoryStatus",
    "StoryMemoryVersion",
    "validate_delta_draft",
]
