"""Map validated application DTOs into typed Story Memory changes."""

from __future__ import annotations

from typing import cast

from domains.writing.story_memory import SourceReference
from domains.writing.story_settings import (
    CharacterState,
    PlotThread,
    RelationshipState,
    StorySettingChange,
    StorySettingValue,
    TimelineEvent,
    WorldFact,
)
from schemas.story_memory import (
    CharacterStateInput,
    PlotThreadInput,
    RelationshipStateInput,
    StorySettingInput,
    TimelineEventInput,
    WorldFactInput,
)


def story_setting_change_from_input(
    value: StorySettingInput,
    chapter_id: str,
) -> StorySettingChange:
    source = SourceReference(
        chapter_id=chapter_id,
        excerpt=value.source.excerpt,
        source_revision=value.source.sourceRevision,
        locator=value.source.locator,
        narrative_order=value.source.narrativeOrder,
        story_time=value.source.storyTime,
        story_time_precision=value.source.storyTimePrecision,
    )
    setting: StorySettingValue
    if isinstance(value, CharacterStateInput):
        setting = CharacterState(
            character_id=value.characterId,
            attribute=value.attribute,
            value=value.value,
            note=value.note,
        )
    elif isinstance(value, RelationshipStateInput):
        setting = RelationshipState(
            source_character_id=value.sourceCharacterId,
            target_character_id=value.targetCharacterId,
            relation_type=value.relationType,
            state=value.state,
            description=value.description,
            directional=value.directional,
        )
    elif isinstance(value, WorldFactInput):
        setting = WorldFact(
            fact_id=value.factId,
            statement=value.statement,
            truth_mode=value.truthMode,
            subject_id_value=value.subjectId,
            known_by_character_ids=tuple(value.knownByCharacterIds),
            tags=tuple(value.tags),
        )
    elif isinstance(value, TimelineEventInput):
        setting = TimelineEvent(
            event_id=value.eventId,
            title=value.title,
            summary=value.summary,
            participant_ids=tuple(value.participantIds),
            location_id=value.locationId,
            story_time=value.storyTime,
            story_time_precision=value.storyTimePrecision,
            narrative_order=value.narrativeOrder,
            caused_by_event_ids=tuple(value.causedByEventIds),
        )
    else:
        plot = cast(PlotThreadInput, value)
        setting = PlotThread(
            thread_id=plot.threadId,
            title=plot.title,
            summary=plot.summary,
            state=plot.state,
            related_entity_ids=tuple(plot.relatedEntityIds),
            opened_chapter_id=plot.openedChapterId,
            expected_resolution_chapter_id=plot.expectedResolutionChapterId,
            resolved_chapter_id=plot.resolvedChapterId,
        )
    return StorySettingChange(
        setting=setting,
        source=source,
        operation=value.operation,
        status=value.status,
        confidence=value.confidence,
    )


__all__ = ["story_setting_change_from_input"]
