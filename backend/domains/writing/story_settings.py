"""Typed story-setting contracts projected into the Story Memory ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, TypeAlias
from urllib.parse import quote

from domains.writing.story_memory import (
    SourceReference,
    StoryMemoryChange,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryStatus,
)


class WorldFactTruthMode(StrEnum):
    CANON = "canon"
    CHARACTER_BELIEF = "character_belief"
    RUMOR = "rumor"
    UNRESOLVED = "unresolved"


class PlotThreadState(StrEnum):
    OPEN = "open"
    ADVANCING = "advancing"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class CharacterState:
    character_id: str
    attribute: str
    value: Any
    note: str = ""

    def __post_init__(self) -> None:
        _require(self.character_id, "character_id")
        _require(self.attribute, "attribute")
        if self.value is None:
            raise ValueError("character state value is required")

    @property
    def target_key(self) -> str:
        return _key("character", self.character_id, "state", self.attribute)

    @property
    def kind(self) -> StoryMemoryKind:
        return StoryMemoryKind.CHARACTER_STATE

    @property
    def subject_id(self) -> str:
        return str(self.character_id).strip()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "characterId": str(self.character_id).strip(),
            "attribute": str(self.attribute).strip(),
            "value": self.value,
            "note": str(self.note or "").strip(),
        }


@dataclass(frozen=True, slots=True)
class RelationshipState:
    source_character_id: str
    target_character_id: str
    relation_type: str
    state: str
    description: str = ""
    directional: bool = True

    def __post_init__(self) -> None:
        source = _require(self.source_character_id, "source_character_id")
        target = _require(self.target_character_id, "target_character_id")
        _require(self.relation_type, "relation_type")
        _require(self.state, "relationship state")
        if source == target:
            raise ValueError("a relationship requires two different characters")

    @property
    def target_key(self) -> str:
        source = str(self.source_character_id).strip()
        target = str(self.target_character_id).strip()
        direction = "directed" if self.directional else "undirected"
        if not self.directional:
            source, target = sorted((source, target))
        return _key("relationship", direction, source, target, self.relation_type)

    @property
    def kind(self) -> StoryMemoryKind:
        return StoryMemoryKind.RELATIONSHIP_STATE

    @property
    def subject_id(self) -> str:
        return str(self.source_character_id).strip()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "sourceCharacterId": str(self.source_character_id).strip(),
            "targetCharacterId": str(self.target_character_id).strip(),
            "relationType": str(self.relation_type).strip(),
            "state": str(self.state).strip(),
            "description": str(self.description or "").strip(),
            "directional": bool(self.directional),
        }


@dataclass(frozen=True, slots=True)
class WorldFact:
    fact_id: str
    statement: str
    truth_mode: WorldFactTruthMode = WorldFactTruthMode.CANON
    subject_id_value: str | None = None
    known_by_character_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.fact_id, "fact_id")
        _require(self.statement, "statement")
        _validate_list(self.known_by_character_ids, "known_by_character_ids")

    @property
    def target_key(self) -> str:
        return _key("world_fact", self.fact_id)

    @property
    def kind(self) -> StoryMemoryKind:
        return StoryMemoryKind.WORLD_FACT

    @property
    def subject_id(self) -> str | None:
        value = str(self.subject_id_value or "").strip()
        return value or None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "factId": str(self.fact_id).strip(),
            "statement": str(self.statement).strip(),
            "truthMode": self.truth_mode.value,
            "subjectId": self.subject_id,
            "knownByCharacterIds": _clean_list(self.known_by_character_ids),
            "tags": _clean_list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    event_id: str
    title: str
    summary: str
    participant_ids: tuple[str, ...] = ()
    location_id: str | None = None
    story_time: str | None = None
    story_time_precision: str | None = None
    narrative_order: int | None = None
    caused_by_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.event_id, "event_id")
        _require(self.title, "title")
        _require(self.summary, "summary")
        _validate_list(self.participant_ids, "participant_ids")
        _validate_list(self.caused_by_event_ids, "caused_by_event_ids")
        if self.narrative_order is not None and self.narrative_order < 0:
            raise ValueError("narrative_order cannot be negative")

    @property
    def target_key(self) -> str:
        return _key("timeline_event", self.event_id)

    @property
    def kind(self) -> StoryMemoryKind:
        return StoryMemoryKind.TIMELINE_EVENT

    @property
    def subject_id(self) -> str:
        return str(self.event_id).strip()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "eventId": str(self.event_id).strip(),
            "title": str(self.title).strip(),
            "summary": str(self.summary).strip(),
            "participantIds": _clean_list(self.participant_ids),
            "locationId": _optional(self.location_id),
            "storyTime": _optional(self.story_time),
            "storyTimePrecision": _optional(self.story_time_precision),
            "narrativeOrder": self.narrative_order,
            "causedByEventIds": _clean_list(self.caused_by_event_ids),
        }


@dataclass(frozen=True, slots=True)
class PlotThread:
    thread_id: str
    title: str
    summary: str
    state: PlotThreadState = PlotThreadState.OPEN
    related_entity_ids: tuple[str, ...] = ()
    opened_chapter_id: str | None = None
    expected_resolution_chapter_id: str | None = None
    resolved_chapter_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.thread_id, "thread_id")
        _require(self.title, "title")
        _require(self.summary, "summary")
        _validate_list(self.related_entity_ids, "related_entity_ids")
        if self.state is PlotThreadState.RESOLVED and not _optional(
            self.resolved_chapter_id
        ):
            raise ValueError("resolved plot threads require resolved_chapter_id")

    @property
    def target_key(self) -> str:
        return _key("plot_thread", self.thread_id)

    @property
    def kind(self) -> StoryMemoryKind:
        return StoryMemoryKind.PLOT_THREAD

    @property
    def subject_id(self) -> str:
        return str(self.thread_id).strip()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "threadId": str(self.thread_id).strip(),
            "title": str(self.title).strip(),
            "summary": str(self.summary).strip(),
            "state": self.state.value,
            "relatedEntityIds": _clean_list(self.related_entity_ids),
            "openedChapterId": _optional(self.opened_chapter_id),
            "expectedResolutionChapterId": _optional(
                self.expected_resolution_chapter_id
            ),
            "resolvedChapterId": _optional(self.resolved_chapter_id),
        }


StorySettingValue: TypeAlias = (
    CharacterState | RelationshipState | WorldFact | TimelineEvent | PlotThread
)


@dataclass(frozen=True, slots=True)
class StorySettingChange:
    setting: StorySettingValue
    source: SourceReference
    operation: StoryMemoryOperation = StoryMemoryOperation.UPSERT
    status: StoryMemoryStatus = StoryMemoryStatus.CONFIRMED
    confidence: float = 1.0

    def to_memory_change(self) -> StoryMemoryChange:
        return StoryMemoryChange(
            target_key=self.setting.target_key,
            kind=self.setting.kind,
            operation=self.operation,
            source=self.source,
            subject_id=self.setting.subject_id,
            payload=self.setting.to_payload(),
            status=self.status,
            confidence=self.confidence,
        )


def _require(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional(value: object | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _validate_list(values: tuple[str, ...], field_name: str) -> None:
    if any(not str(value or "").strip() for value in values):
        raise ValueError(f"{field_name} cannot contain empty values")


def _clean_list(values: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values))


def _key(*segments: object) -> str:
    clean = [_require(value, "story setting key segment") for value in segments]
    return ":".join(quote(value, safe="-_.~") for value in clean)


__all__ = [
    "CharacterState",
    "PlotThread",
    "PlotThreadState",
    "RelationshipState",
    "StorySettingChange",
    "StorySettingValue",
    "TimelineEvent",
    "WorldFact",
    "WorldFactTruthMode",
]
