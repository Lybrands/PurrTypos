"""Run-owned public web receipts and bounded evidence snapshots."""


async def init_web_research_schema(db):
    await db.execute("""CREATE TABLE IF NOT EXISTS web_research_run_limits (
        run_id TEXT PRIMARY KEY,
        read_limit INTEGER NOT NULL CHECK(read_limit IN (8, 12))
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS web_research_attempts (
        run_id TEXT NOT NULL,
        call_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        arguments_digest TEXT NOT NULL,
        external_request INTEGER NOT NULL DEFAULT 1,
        result_json TEXT,
        PRIMARY KEY (run_id, call_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS web_research_sources (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        excerpt TEXT NOT NULL,
        backend TEXT NOT NULL,
        retrieved_at TEXT NOT NULL,
        page_text TEXT,
        page_url TEXT,
        page_retrieved_at TEXT,
        page_truncated INTEGER NOT NULL DEFAULT 0,
        UNIQUE(run_id, url)
    )""")
    await db.execute("DROP TRIGGER IF EXISTS web_research_delete_run")
    await db.execute("""CREATE TRIGGER web_research_delete_run
        AFTER DELETE ON ai_agent_runs BEGIN
        DELETE FROM web_research_attempts WHERE run_id = OLD.id;
        DELETE FROM web_research_sources WHERE run_id = OLD.id;
        DELETE FROM web_research_run_limits WHERE run_id = OLD.id;
    END""")
