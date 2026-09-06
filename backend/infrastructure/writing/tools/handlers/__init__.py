"""Explicit immutable source for all dependency-aware Writing operations."""

from __future__ import annotations

from types import MappingProxyType

from infrastructure.writing.tools.handlers import (
    chapter_tools,
    character_tools,
    dashboard_tools,
    memory_tools,
    outline_tools,
    setting_entity_tools,
    story_background_tools,
    continuation_tools,
)


WRITING_TOOL_OPERATIONS = MappingProxyType({
    "getChapterContent": chapter_tools._tool_get_chapter_content,
    "listWritingChapters": chapter_tools._tool_list_writing_chapters,
    "createWritingChapter": chapter_tools._tool_create_writing_chapter,
    "batchGetChapterContents": chapter_tools._tool_batch_get_chapter_contents,
    "editChapterContent": chapter_tools._tool_edit_chapter_content,
    "getBookCharacters": character_tools._tool_get_book_characters,
    "listBookCharacters": character_tools._tool_list_book_characters,
    "createCharacter": character_tools._tool_create_character,
    "updateCharacter": character_tools._tool_update_character,
    "deleteCharacter": character_tools._tool_delete_character,
    "getStoryHealthDashboard": dashboard_tools._tool_get_story_health_dashboard,
    "getWritingStatsDashboard": dashboard_tools._tool_get_writing_stats_dashboard,
    "addSparkIdea": memory_tools._tool_add_spark_idea,
    "updateSparkIdea": memory_tools._tool_update_spark_idea,
    "deleteSparkIdea": memory_tools._tool_delete_spark_idea,
    "addForeshadowing": memory_tools._tool_add_foreshadowing,
    "searchSparkIdeas": memory_tools._tool_search_spark_ideas,
    "createMemory": memory_tools._tool_create_memory,
    "updateMemory": memory_tools._tool_update_memory,
    "archiveMemory": memory_tools._tool_archive_memory,
    "linkMemories": memory_tools._tool_link_memories,
    "resolveForeshadowing": memory_tools._tool_resolve_foreshadowing,
    "queryOutline": outline_tools._tool_query_outline,
    "getGlobalOutline": outline_tools._tool_get_global_outline,
    "editGlobalOutline": outline_tools._tool_edit_global_outline,
    "listOutlines": outline_tools._tool_list_outlines,
    "updateOutline": outline_tools._tool_update_outline,
    "listSettingEntities": setting_entity_tools._tool_list_setting_entities,
    "getSettingEntities": setting_entity_tools._tool_get_setting_entities,
    "createSettingEntity": setting_entity_tools._tool_create_setting_entity,
    "updateSettingEntity": setting_entity_tools._tool_update_setting_entity,
    "deleteSettingEntity": setting_entity_tools._tool_delete_setting_entity,
    "getStoryBackground": story_background_tools._tool_get_story_background,
    "editStoryBackground": story_background_tools._tool_edit_story_background,
    "readContinuationSourceSection": continuation_tools._tool_read_continuation_source_section,
})


__all__ = ["WRITING_TOOL_OPERATIONS"]
