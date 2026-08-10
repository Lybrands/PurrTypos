"""Conversation projection and task-output schema for the screenplay Agent.

The screenplay product aggregate and immutable Revision store live in
``screenplay_v2_schema``.  These tables own only AI interaction state.  A Turn
records one semantic conversation decision. PurrA owns all durable execution
state; this module stores only shared chunks, UI events, and referenced payloads.
"""

from __future__ import annotations


async def init_screenplay_agent_schema(db) -> None:
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenplay_revisions_agent_task "
        "ON screenplay_revisions(agent_task_id) WHERE agent_task_id IS NOT NULL"
    )

    # No compatibility layer is kept for the discarded operation-driven Agent.
    # These tables never own accepted Revisions, so removing their runtime state
    # leaves the project document chain intact.
    await db.execute(
        "DELETE FROM screenplay_command_receipts WHERE command_type IN "
        "('startOperation', 'pauseOperation', 'resumeOperation', "
        "'cancelOperation', 'submitConversationTurn', "
        "'resumeConversationTurn', 'cancelConversationTurn')"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_agent_turns (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        command_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        user_content TEXT NOT NULL,
        assistant_content TEXT NOT NULL DEFAULT '',
        intent_json TEXT NOT NULL DEFAULT '{}',
        runtime_profile_json TEXT NOT NULL DEFAULT '{}',
        planner_run_id TEXT DEFAULT NULL,
        task_id TEXT DEFAULT NULL,
        target_role TEXT DEFAULT NULL,
        result_revision_id TEXT DEFAULT NULL,
        error_json TEXT DEFAULT NULL,
        execution_owner_id TEXT DEFAULT NULL,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        heartbeat_at_ms INTEGER DEFAULT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, command_id)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_agent_turns_session "
        "ON screenplay_agent_turns(project_id, session_id, create_time, id)"
    )
    turn_columns = {
        str(column["name"])
        for column in await db.fetch_all("PRAGMA table_info(screenplay_agent_turns)")
    }
    for name, definition in (
        ("task_id", "TEXT DEFAULT NULL"),
        ("target_role", "TEXT DEFAULT NULL"),
        ("result_revision_id", "TEXT DEFAULT NULL"),
    ):
        if name not in turn_columns:
            await db.execute(
                f"ALTER TABLE screenplay_agent_turns ADD COLUMN {name} {definition}"
            )
    await db.execute("DROP INDEX IF EXISTS idx_screenplay_agent_one_active_turn")
    await db.execute(
        "CREATE UNIQUE INDEX idx_screenplay_agent_one_active_turn "
        "ON screenplay_agent_turns(project_id) "
        "WHERE status IN ('queued', 'planning', 'running')"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_agent_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        turn_id TEXT DEFAULT NULL,
        task_id TEXT DEFAULT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_agent_events_session "
        "ON screenplay_agent_events(project_id, session_id, id)"
    )
    event_columns = {
        str(column["name"])
        for column in await db.fetch_all("PRAGMA table_info(screenplay_agent_events)")
    }
    if "task_id" not in event_columns:
        await db.execute(
            "ALTER TABLE screenplay_agent_events ADD COLUMN task_id TEXT DEFAULT NULL"
        )

    # Materialized shared Agent chunks are the conversation transport and
    # recovery source. Core Run events remain the diagnostic source of truth;
    # the frontend never parses their screenplay-specific structured output.
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_agent_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        turn_id TEXT NOT NULL,
        task_id TEXT DEFAULT NULL,
        run_id TEXT DEFAULT NULL,
        protocol_version INTEGER NOT NULL DEFAULT 2,
        chunk_json TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    chunk_columns = {
        str(column["name"])
        for column in await db.fetch_all("PRAGMA table_info(screenplay_agent_chunks)")
    }
    if "run_id" not in chunk_columns:
        await db.execute(
            "ALTER TABLE screenplay_agent_chunks ADD COLUMN run_id TEXT DEFAULT NULL"
        )
    if "protocol_version" not in chunk_columns:
        await db.execute(
            "ALTER TABLE screenplay_agent_chunks ADD COLUMN "
            "protocol_version INTEGER NOT NULL DEFAULT 1"
        )
    if "task_id" not in chunk_columns:
        await db.execute(
            "ALTER TABLE screenplay_agent_chunks ADD COLUMN task_id TEXT DEFAULT NULL"
        )
    # This project is still in test phase: v1 leaked structured model payloads
    # into the conversation, so those transport rows are discarded rather
    # than kept behind a compatibility parser.
    await db.execute(
        "DELETE FROM screenplay_agent_chunks WHERE protocol_version < 2"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_agent_chunks_session "
        "ON screenplay_agent_chunks(project_id, session_id, id)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_agent_task_outputs (
        task_id TEXT NOT NULL,
        unit_id TEXT NOT NULL,
        output_ref TEXT NOT NULL UNIQUE,
        output_json TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(task_id, unit_id)
    )""")

    # Test-phase migration: the discarded Job engine owns no accepted document
    # state. Remove its runtime rows instead of preserving a second scheduler.
    if "job_id" in turn_columns:
        legacy_turns = await db.fetch_all(
            "SELECT id FROM screenplay_agent_turns "
            "WHERE job_id IS NOT NULL AND task_id IS NULL"
        )
        legacy_turn_ids = [str(row["id"]) for row in legacy_turns]
        if legacy_turn_ids:
            marks = ",".join("?" for _ in legacy_turn_ids)
            await db.execute(
                f"DELETE FROM screenplay_agent_events WHERE turn_id IN ({marks})",
                legacy_turn_ids,
            )
            await db.execute(
                f"DELETE FROM screenplay_agent_chunks WHERE turn_id IN ({marks})",
                legacy_turn_ids,
            )
            await db.execute(
                f"DELETE FROM screenplay_agent_turns WHERE id IN ({marks})",
                legacy_turn_ids,
            )
    await db.execute("DROP INDEX IF EXISTS idx_screenplay_agent_one_active_job")
    await db.execute("DROP INDEX IF EXISTS idx_screenplay_agent_jobs_project")
    await db.execute("DROP TABLE IF EXISTS screenplay_agent_job_steps")
    await db.execute("DROP TABLE IF EXISTS screenplay_agent_jobs")


__all__ = ["init_screenplay_agent_schema"]
