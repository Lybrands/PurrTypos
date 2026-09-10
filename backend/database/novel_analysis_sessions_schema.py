async def init_analysis_sessions(db):
    await db.execute('''CREATE TABLE IF NOT EXISTS novel_analysis_sessions (
        id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES novel_source_revisions(id) ON DELETE CASCADE, title TEXT NOT NULL,
        closed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    await db.execute('''CREATE TABLE IF NOT EXISTS novel_analysis_session_commands (
        command_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES novel_analysis_sessions(id) ON DELETE CASCADE,
        revision_id TEXT NOT NULL)''')
    await db.execute('CREATE INDEX IF NOT EXISTS analysis_sessions_revision ON novel_analysis_sessions(revision_id)')



    await db.execute("""CREATE TABLE IF NOT EXISTS novel_analysis_superseded_runs (
        run_id TEXT PRIMARY KEY REFERENCES ai_agent_runs(id) ON DELETE CASCADE,
        replacement_command_id TEXT NOT NULL,
        target_run_id TEXT NOT NULL)""")
    await db.execute('CREATE INDEX IF NOT EXISTS analysis_replacements_command ON novel_analysis_superseded_runs(replacement_command_id)')
