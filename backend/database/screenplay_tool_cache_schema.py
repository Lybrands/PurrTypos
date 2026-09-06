"""Read-result cache invalidation at the source transaction boundary."""

from __future__ import annotations


_SOURCE = frozenset({
    "screenplay_projects", "books", "outlines", "outline_chapters",
})
_DELIVERABLE = frozenset({
    "screenplay_projects", "screenplay_deliverables", "screenplay_revisions",
    "screenplay_revision_parts", "screenplay_project_heads",
})
SCREENPLAY_READ_DEPENDENCIES = {
    "inspectScreenplayProject": _DELIVERABLE,
    "readScreenplayDeliverable": _DELIVERABLE,
    "searchScreenplayDeliverables": _DELIVERABLE,
    "getScreenplayEpisodeContext": _DELIVERABLE,
    "getScreenplaySceneContext": _DELIVERABLE | frozenset({
        "ai_agent_long_tasks", "ai_agent_long_task_units",
        "ai_agent_artifacts", "ai_agent_artifact_batches",
    }),
    "inspectSourceStructure": _SOURCE,
    "readSourceChapters": _SOURCE | {"articles"},
    "searchSourceText": _SOURCE | {"articles"},
    "listSourceCharacters": _SOURCE | {"characters"},
    "readSourceCharacters": _SOURCE | {"characters"},
    "listSourceWorldEntities": _SOURCE | {"setting_entities"},
    "readSourceWorldEntities": _SOURCE | {"setting_entities"},
    "readSourceBackground": _SOURCE | {"story_background"},
    "querySourceStoryFacts": _SOURCE | {
        "story_memory_records", "story_memory_sources",
        "story_memory_entity_links", "characters", "setting_entities",
    },
    "readSourceOutline": _SOURCE,
    "readScreenplayTaskDependencies": frozenset({
        "screenplay_projects", "ai_agent_long_tasks", "ai_agent_long_task_units",
        "ai_agent_artifacts", "ai_agent_artifact_batches",
    }),
}


async def init_screenplay_tool_cache_schema(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_tool_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key TEXT NOT NULL UNIQUE,
        tool_name TEXT NOT NULL,
        content TEXT NOT NULL
    )""")
    columns = {row["name"] for row in await db.fetch_all("PRAGMA table_info(screenplay_tool_cache)")}
    for name in ("scope_key", "arguments_json"):
        if name not in columns:
            await db.execute(f"ALTER TABLE screenplay_tool_cache ADD COLUMN {name} TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_tool_cache_tool "
        "ON screenplay_tool_cache(tool_name)"
    )
    tables = sorted(set().union(*SCREENPLAY_READ_DEPENDENCIES.values()))
    for table in tables:
        names = ",".join(
            f"'{name}'" for name, dependencies in SCREENPLAY_READ_DEPENDENCIES.items()
            if table in dependencies
        )
        for event in ("INSERT", "UPDATE", "DELETE"):
            trigger = f"screenplay_tool_cache_{table}_{event.lower()}"
            await db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            await db.execute(f"""CREATE TRIGGER {trigger}
                AFTER {event} ON {table}
                BEGIN
                    DELETE FROM screenplay_tool_cache WHERE tool_name IN ({names});
                END""")


__all__ = ["SCREENPLAY_READ_DEPENDENCIES", "init_screenplay_tool_cache_schema"]
