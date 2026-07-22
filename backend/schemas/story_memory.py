from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domains.writing.story_memory import (
    StoryMemoryOperation,
    StoryMemoryStatus,
)
from domains.writing.story_settings import PlotThreadState, WorldFactTruthMode


class StoryMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoryMemorySourceInput(StoryMemoryModel):
    excerpt: str = Field(min_length=1)
    sourceRevision: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    narrativeOrder: int | None = Field(default=None, ge=0)
    storyTime: str | None = None
    storyTimePrecision: str | None = None


class StorySettingChangeInput(StoryMemoryModel):
    operation: StoryMemoryOperation = StoryMemoryOperation.UPSERT
    status: StoryMemoryStatus = StoryMemoryStatus.CONFIRMED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: StoryMemorySourceInput


class CharacterStateInput(StorySettingChangeInput):
    kind: Literal["character_state"]
    characterId: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    value: Any
    note: str = ""


class RelationshipStateInput(StorySettingChangeInput):
    kind: Literal["relationship_state"]
    sourceCharacterId: str = Field(min_length=1)
    targetCharacterId: str = Field(min_length=1)
    relationType: str = Field(min_length=1)
    state: str = Field(min_length=1)
    description: str = ""
    directional: bool = True


class WorldFactInput(StorySettingChangeInput):
    kind: Literal["world_fact"]
    factId: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    truthMode: WorldFactTruthMode = WorldFactTruthMode.CANON
    subjectId: str | None = None
    knownByCharacterIds: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TimelineEventInput(StorySettingChangeInput):
    kind: Literal["timeline_event"]
    eventId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    participantIds: list[str] = Field(default_factory=list)
    locationId: str | None = None
    storyTime: str | None = None
    storyTimePrecision: str | None = None
    narrativeOrder: int | None = Field(default=None, ge=0)
    causedByEventIds: list[str] = Field(default_factory=list)


class PlotThreadInput(StorySettingChangeInput):
    kind: Literal["plot_thread"]
    threadId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    state: PlotThreadState = PlotThreadState.OPEN
    relatedEntityIds: list[str] = Field(default_factory=list)
    openedChapterId: str | None = None
    expectedResolutionChapterId: str | None = None
    resolvedChapterId: str | None = None


StorySettingInput = Annotated[
    CharacterStateInput
    | RelationshipStateInput
    | WorldFactInput
    | TimelineEventInput
    | PlotThreadInput,
    Field(discriminator="kind"),
]


class StageStoryMemoryDeltaRequest(StoryMemoryModel):
    bookId: str = Field(min_length=1)
    chapterId: str = Field(min_length=1)
    sourceRevision: str = ""
    sourceType: str = "manual"
    note: str = ""
    changes: list[StorySettingInput] = Field(min_length=1)


class InvalidateStoryMemoryRequest(StoryMemoryModel):
    currentRevision: str | None = None


class AnalyzeChapterStoryMemoryRequest(StoryMemoryModel):
    modelId: str | None = None


class ResolveStoryMemoryEvolutionRequest(StoryMemoryModel):
    resolutions: dict[str, Literal["accepted", "rejected"]] = Field(min_length=1)


__all__ = [
    "CharacterStateInput",
    "AnalyzeChapterStoryMemoryRequest",
    "InvalidateStoryMemoryRequest",
    "PlotThreadInput",
    "ResolveStoryMemoryEvolutionRequest",
    "RelationshipStateInput",
    "StageStoryMemoryDeltaRequest",
    "StoryMemorySourceInput",
    "StorySettingInput",
    "TimelineEventInput",
    "WorldFactInput",
]
