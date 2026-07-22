"""Explainable decisions for evolving authoritative Story Memory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from domains.writing.story_memory import StoryMemoryKind


class EvolutionClassification(StrEnum):
    ADDITION = "addition"
    UPDATE = "update"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    SUPERSESSION = "supersession"


class EvolutionRecommendation(StrEnum):
    APPLY = "apply"
    REJECT = "reject"
    REVIEW = "review"


class EvolutionRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvolutionReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    STALE = "stale"


class EvolutionResolution(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVERTED = "reverted"


@dataclass(frozen=True, slots=True)
class EvolutionFieldChange:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class StoryMemoryEvolutionDecision:
    delta_id: str
    target_key: str
    kind: StoryMemoryKind
    classification: EvolutionClassification
    recommendation: EvolutionRecommendation
    risk: EvolutionRisk
    rationale: str
    field_changes: tuple[EvolutionFieldChange, ...] = ()
    candidate_payload: Mapping[str, Any] | None = None
    source_excerpt: str = ""
    related_memory_key: str | None = None
    existing_record_id: str | None = None
    existing_version: int | None = None
    candidate_confidence: float = 1.0
    review_status: EvolutionReviewStatus = EvolutionReviewStatus.OPEN
    resolution: EvolutionResolution = EvolutionResolution.PENDING
    resolved_delta_id: str | None = None
    resolution_actor: str | None = None
    resolved_at: str | None = None
    create_time: str | None = None
    update_time: str | None = None


@dataclass(frozen=True, slots=True)
class StoryMemoryEvolutionReview:
    delta_id: str
    book_id: str
    chapter_id: str
    decisions: tuple[StoryMemoryEvolutionDecision, ...]
    status: EvolutionReviewStatus = EvolutionReviewStatus.OPEN

    @property
    def summary(self) -> dict[str, int]:
        result = {item.value: 0 for item in EvolutionClassification}
        for decision in self.decisions:
            result[decision.classification.value] += 1
        return result


@dataclass(frozen=True, slots=True)
class StoryMemoryEvolutionResolutionReceipt:
    original_delta_id: str
    applied_delta_id: str | None
    accepted_keys: tuple[str, ...]
    rejected_keys: tuple[str, ...]
    status: str
    actor: str


__all__ = [
    "EvolutionClassification",
    "EvolutionFieldChange",
    "EvolutionRecommendation",
    "EvolutionResolution",
    "EvolutionReviewStatus",
    "EvolutionRisk",
    "StoryMemoryEvolutionDecision",
    "StoryMemoryEvolutionReview",
    "StoryMemoryEvolutionResolutionReceipt",
]
