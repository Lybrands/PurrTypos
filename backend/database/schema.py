"""
Schema initialisation & migrations – port of initDatabase() from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.id_utils import short_id8


async def _try_exec(db: DatabaseConnection, sql: str) -> None:
    """Execute DDL that may fail (e.g. column already exists)."""
    try:
        await db.execute(sql)
    except Exception:
        pass


async def init_schema(db: DatabaseConnection) -> None:
    # ── books ────────────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS books (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        cover_color TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        enable_volume INTEGER DEFAULT 0
    )""")
    await _try_exec(db, "ALTER TABLE books ADD COLUMN enable_volume INTEGER DEFAULT 0")

    # ── outlines ─────────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS outlines (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        type TEXT DEFAULT 'chapter',
        sort INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        xmind_data TEXT DEFAULT NULL,
        file_path TEXT DEFAULT NULL,
        book_id TEXT DEFAULT NULL,
        parent_outline_id TEXT DEFAULT NULL,
        writing_chapter_id TEXT DEFAULT NULL,
        markdown_content TEXT DEFAULT NULL
    )""")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN type TEXT DEFAULT 'chapter'")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN sort INTEGER DEFAULT 0")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN xmind_data TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN file_path TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN book_id TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN parent_outline_id TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN writing_chapter_id TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE outlines ADD COLUMN markdown_content TEXT DEFAULT NULL")
    await _try_exec(db, "UPDATE outlines SET type = 'chapter' WHERE type IS NULL")
    # 废弃「其他大纲」：启动时清理历史 type = other 的数据
    await _try_exec(
        db,
        "DELETE FROM outline_chapters WHERE outline_id IN "
        "(SELECT id FROM outlines WHERE type = 'other')",
    )
    await _try_exec(db, "DELETE FROM outlines WHERE type = 'other'")

    # ── outline_chapters ─────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS outline_chapters (
        id TEXT PRIMARY KEY NOT NULL,
        outline_id TEXT NOT NULL,
        title TEXT NOT NULL,
        level INTEGER DEFAULT 1,
        progress TEXT DEFAULT 'todo',
        sort INTEGER DEFAULT 0,
        parent_id TEXT DEFAULT NULL
    )""")

    # ── articles ─────────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chapter_id TEXT NOT NULL UNIQUE,
        content TEXT DEFAULT '',
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── ai_sessions ──────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chapter_id TEXT,
        title TEXT DEFAULT '新对话',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        closed INTEGER DEFAULT 0,
        book_id TEXT
    )""")
    await _try_exec(db, "ALTER TABLE ai_sessions ADD COLUMN closed INTEGER DEFAULT 0")
    await _try_exec(db, "ALTER TABLE ai_sessions ADD COLUMN book_id TEXT")

    # ── ai_conversations ─────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        chapter_id TEXT,
        prompt TEXT NOT NULL,
        response TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN session_id INTEGER")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN model TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN thinking TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN tool_call_segments TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN thinking_blocks TEXT DEFAULT NULL")

    # ── ai_favorites ─────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        session_title TEXT NOT NULL,
        prompt TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _try_exec(db, 'ALTER TABLE ai_favorites ADD COLUMN prompt TEXT DEFAULT ""')

    # ── ai_memories ──────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        layer TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        chapter_id TEXT DEFAULT NULL,
        character_id INTEGER DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # FTS5 full-text index for ai_memories (trigram tokenizer — CJK substring search)
    await _try_exec(db, """CREATE VIRTUAL TABLE IF NOT EXISTS ai_memories_fts
        USING fts5(content, tokenize=trigram, content=ai_memories, content_rowid=id)""")
    # Sync triggers
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS ai_memories_fts_ai
        AFTER INSERT ON ai_memories BEGIN
            INSERT INTO ai_memories_fts(rowid, content) VALUES (new.id, new.content);
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS ai_memories_fts_au
        AFTER UPDATE ON ai_memories BEGIN
            INSERT INTO ai_memories_fts(ai_memories_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            INSERT INTO ai_memories_fts(rowid, content) VALUES (new.id, new.content);
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS ai_memories_fts_ad
        AFTER DELETE ON ai_memories BEGIN
            INSERT INTO ai_memories_fts(ai_memories_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
        END""")
    # Rebuild FTS index to include any rows inserted before triggers existed
    await _try_exec(db, "INSERT INTO ai_memories_fts(ai_memories_fts) VALUES ('rebuild')")

    # ── ai_foreshadowing ─────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_foreshadowing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '悬念',
        expected_chapter_id TEXT DEFAULT NULL,
        status TEXT NOT NULL DEFAULT '未回收',
        resolved_chapter_id TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── orphaned conversations migration ─────────────────────────
    orphaned = await db.fetch_all(
        "SELECT DISTINCT chapter_id FROM ai_conversations "
        "WHERE session_id IS NULL AND chapter_id IS NOT NULL"
    )
    for row in orphaned:
        chapter_id = row["chapter_id"]
        session_id = await db.execute_and_get_id(
            "INSERT INTO ai_sessions (chapter_id, title, create_time) "
            "VALUES (?, '历史对话', datetime('now'))",
            [chapter_id],
        )
        await db.execute(
            "UPDATE ai_conversations SET session_id = ? "
            "WHERE chapter_id = ? AND session_id IS NULL",
            [session_id, chapter_id],
        )

    # ── settings ─────────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    )""")

    # ── story_background ─────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS story_background (
        book_id TEXT NOT NULL PRIMARY KEY,
        content TEXT DEFAULT '',
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── story_background_attachments ─────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS story_background_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        name TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── characters ───────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        name TEXT NOT NULL,
        gender TEXT DEFAULT '',
        age TEXT DEFAULT '',
        height TEXT DEFAULT '',
        occupation TEXT DEFAULT '',
        appearance TEXT DEFAULT '',
        origin TEXT DEFAULT '',
        personality TEXT DEFAULT '',
        background TEXT DEFAULT '',
        biography TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── character_options ────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS character_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        value TEXT NOT NULL,
        sort INTEGER DEFAULT 0
    )""")

    await _try_exec(db, "ALTER TABLE characters ADD COLUMN gender TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN age TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN height TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN occupation TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN appearance TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN origin TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN personality TEXT DEFAULT ''")
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN remark TEXT DEFAULT ''")

    # ── seed character_options defaults ───────────────────────────
    personality_seed = ["开朗", "内敛", "沉稳", "冲动", "善良", "冷酷", "腹黑", "正义"]
    tag_seed = ["主角", "配角", "反派", "导师", "神秘人物", "幕后黑手"]

    for i, v in enumerate(personality_seed):
        exists = await db.fetch_one(
            "SELECT id FROM character_options WHERE category = ? AND value = ?",
            ["personality", v],
        )
        if not exists:
            await db.execute(
                "INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)",
                ["personality", v, i],
            )

    for i, v in enumerate(tag_seed):
        exists = await db.fetch_one(
            "SELECT id FROM character_options WHERE category = ? AND value = ?",
            ["tag", v],
        )
        if not exists:
            await db.execute(
                "INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)",
                ["tag", v, i],
            )

    # ── legacy data migration: create default book if needed ─────
    book_count = await db.fetch_one("SELECT COUNT(*) as c FROM books")
    if not book_count or int(book_count["c"]) == 0:
        outline_count = await db.fetch_one("SELECT COUNT(*) as c FROM outlines")
        if outline_count and int(outline_count["c"]) > 0:
            default_bid = short_id8()
            await db.execute(
                "INSERT INTO books (id, title, cover_color) VALUES (?, ?, ?)",
                [default_bid, "我的作品", "#4A90D9"],
            )
            await db.execute(
                "UPDATE outlines SET book_id = ? WHERE book_id IS NULL",
                [default_bid],
            )

    # TODO: migrateEntityIdsToText8 – placeholder for entity ID migration
