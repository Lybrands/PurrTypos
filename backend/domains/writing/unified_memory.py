"""User-facing read model shared by memory management and recall diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class UnifiedMemorySource(StrEnum):
    SEMANTIC = "semantic"
    STORY_STATE = "story_state"
    STORY_CANDIDATE = "story_candidate"


class UnifiedMemoryStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    CONFLICT = "conflict"
    STALE = "stale"
    ARCHIVED = "archived"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class UnifiedMemoryItem:
    id: str
    book_id: str
    source: UnifiedMemorySource
    kind: str
    status: UnifiedMemoryStatus
    content: str
    summary: str = ""
    structured_data: Mapping[str, Any] = field(default_factory=dict)
    scope_type: str = "book"
    scope_id: str | None = None
    chapter_id: str | None = None
    chapter_title: str | None = None
    evidence_excerpt: str = ""
    confidence: float = 1.0
    version: int | None = None
    pinned: bool = False
    source_type: str = ""
    source_id: str | None = None
    memory_key: str | None = None
    delta_id: str | None = None
    target_key: str | None = None
    classification: str | None = None
    recommendation: str | None = None
    risk: str | None = None
    resolution: str | None = None
    actions: tuple[str, ...] = ()
    create_time: str | None = None
    update_time: str | None = None


@dataclass(frozen=True, slots=True)
class UnifiedMemoryPage:
    items: tuple[UnifiedMemoryItem, ...]
    total: int
    suppressed_duplicates: int = 0
    unavailable_sources: Mapping[str, str] = field(default_factory=dict)


__all__ = [
    "UnifiedMemoryItem",
    "UnifiedMemoryPage",
    "UnifiedMemorySource",
    "UnifiedMemoryStatus",
]
