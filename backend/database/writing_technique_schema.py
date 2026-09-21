"""Rebuildable catalog and durable writing-technique permissions/settings."""


async def init_writing_technique_schema(db):
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_catalog (
        kind TEXT NOT NULL, id TEXT NOT NULL, record_json TEXT NOT NULL,
        PRIMARY KEY(kind, id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_grants (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL, kind TEXT NOT NULL,
        object_id TEXT NOT NULL, version_id TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
        status_generation INTEGER NOT NULL DEFAULT 0,
        member_epochs_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(book_id, kind, object_id, version_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_modes (
        scope_kind TEXT NOT NULL CHECK(scope_kind IN ('book', 'session')),
        scope_id TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'manual' CHECK(mode IN ('manual','auto')),
        PRIMARY KEY(scope_kind, scope_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_inputs (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, ref_json TEXT NOT NULL,
        removed INTEGER NOT NULL DEFAULT 0
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_run_inputs (
        run_id TEXT NOT NULL, input_id TEXT NOT NULL, PRIMARY KEY(run_id, input_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_run_state (
        run_id TEXT PRIMARY KEY, automatic_refs_json TEXT NOT NULL DEFAULT '[]',
        entry_refs_json TEXT NOT NULL DEFAULT '[]', entry_budget_tokens INTEGER NOT NULL DEFAULT 0
    )""")
    columns = {row["name"] for row in await db.fetch_all("PRAGMA table_info(writing_technique_run_state)")}
    if "entry_budget_tokens" not in columns:
        await db.execute("ALTER TABLE writing_technique_run_state ADD COLUMN entry_budget_tokens INTEGER NOT NULL DEFAULT 0")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_request_inputs (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL, session_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL
    )""")

    await db.execute("""CREATE TABLE IF NOT EXISTS writing_technique_selections (
        scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, refs_json TEXT NOT NULL,
        PRIMARY KEY(scope_kind,scope_id)
    )""")
