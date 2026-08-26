"""Schema for immutable novel sources, analysis, canon, and continuations."""

from __future__ import annotations


async def _try_exec(db, sql: str) -> bool:
    try:
        await db.execute(sql)
        return True
    except Exception:
        return False


async def init_continuation_schema(db) -> None:
    book_columns = {
        str(row["name"])
        for row in await db.fetch_all("PRAGMA table_info(books)")
    }
    if "creation_mode" not in book_columns:
        await db.execute(
            "ALTER TABLE books ADD COLUMN creation_mode TEXT NOT NULL DEFAULT 'original'"
        )
    await db.execute(
        "UPDATE books SET creation_mode = 'original' "
        "WHERE creation_mode IS NULL OR creation_mode NOT IN ('original', 'continuation')"
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_works (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        source_type TEXT NOT NULL,
        origin_book_id TEXT DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_revisions (
        id TEXT PRIMARY KEY NOT NULL,
        work_id TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        content_digest TEXT NOT NULL,
        parser_version INTEGER NOT NULL,
        source_metadata_json TEXT NOT NULL DEFAULT '{}',
        byte_count INTEGER NOT NULL,
        character_count INTEGER NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(work_id, version_no)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_sections (
        id TEXT PRIMARY KEY NOT NULL,
        revision_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        section_type TEXT NOT NULL DEFAULT 'chapter',
        title TEXT NOT NULL,
        text_content TEXT NOT NULL,
        content_digest TEXT NOT NULL,
        locator_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(revision_id, ordinal)
    )""")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_source_revisions_work ON novel_source_revisions(work_id, version_no)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_source_sections_revision ON novel_source_sections(revision_id, ordinal)")

    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_analyses (
        id TEXT PRIMARY KEY NOT NULL,
        source_revision_id TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        coverage_end_ordinal INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        content_digest TEXT NOT NULL,
        summary_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_revision_id, version_no)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_analysis_facts (
        id TEXT PRIMARY KEY NOT NULL,
        analysis_id TEXT NOT NULL,
        fact_kind TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        predicate TEXT NOT NULL,
        value_json TEXT NOT NULL,
        lifecycle_status TEXT NOT NULL DEFAULT 'active',
        first_section_ordinal INTEGER NOT NULL,
        last_section_ordinal INTEGER NOT NULL,
        content_digest TEXT NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_analysis_evidence (
        id TEXT PRIMARY KEY NOT NULL,
        analysis_id TEXT NOT NULL,
        owner_type TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        section_id TEXT NOT NULL,
        excerpt TEXT NOT NULL,
        locator_json TEXT NOT NULL DEFAULT '{}',
        excerpt_digest TEXT NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS novel_source_craft_cards (
        id TEXT PRIMARY KEY NOT NULL,
        analysis_id TEXT NOT NULL,
        card_kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body_markdown TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'verified',
        content_digest TEXT NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS continuation_canon_snapshots (
        id TEXT PRIMARY KEY NOT NULL,
        source_revision_id TEXT NOT NULL,
        source_analysis_id TEXT NOT NULL,
        fork_section_id TEXT NOT NULL,
        fork_ordinal INTEGER NOT NULL,
        content_digest TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS continuation_canon_records (
        id TEXT PRIMARY KEY NOT NULL,
        snapshot_id TEXT NOT NULL,
        source_fact_id TEXT NOT NULL,
        fact_kind TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        predicate TEXT NOT NULL,
        value_json TEXT NOT NULL,
        content_digest TEXT NOT NULL
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS continuation_bindings (
        id TEXT PRIMARY KEY NOT NULL,
        target_book_id TEXT NOT NULL UNIQUE,
        source_work_id TEXT NOT NULL,
        source_revision_id TEXT NOT NULL,
        fork_section_id TEXT NOT NULL,
        fork_ordinal INTEGER NOT NULL,
        canon_snapshot_id TEXT NOT NULL,
        binding_digest TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _try_exec(db, """CREATE VIRTUAL TABLE IF NOT EXISTS novel_source_sections_fts
        USING fts5(section_id UNINDEXED, revision_id UNINDEXED, title, text_content)""")


__all__ = ["init_continuation_schema"]
