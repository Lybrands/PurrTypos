"""Persistent Writing read snapshots invalidated with their source data."""


async def init_writing_tool_cache_schema(db):
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_tool_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key TEXT NOT NULL UNIQUE,
        scope_key TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        content TEXT NOT NULL
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_writing_tool_cache_scope ON writing_tool_cache(scope_key)")
    for table in ("books", "articles", "outlines", "outline_chapters", "characters",
                  "setting_entities", "story_background"):
        for event in ("INSERT", "UPDATE", "DELETE"):
            await db.execute(f"""CREATE TRIGGER IF NOT EXISTS writing_tool_cache_{table}_{event.lower()}
                AFTER {event} ON {table} BEGIN DELETE FROM writing_tool_cache; END""")
