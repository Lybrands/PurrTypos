"""Schema for the native screenplay project aggregate."""

from __future__ import annotations


async def _try_exec(db, sql: str) -> None:
    try:
        await db.execute(sql)
    except Exception:
        # SQLite has no portable ``ADD COLUMN IF NOT EXISTS`` on the versions
        # supported by the desktop app. Existing schema initialization follows
        # the same idempotent migration convention.
        pass


async def init_screenplay_v2_schema(db) -> None:
    """Create the v2 aggregate, immutable revision, and operation tables."""

    await _try_exec(
        db,
        "ALTER TABLE screenplay_projects ADD COLUMN "
        "revision INTEGER NOT NULL DEFAULT 1",
    )
    await _try_exec(
        db,
        "ALTER TABLE screenplay_projects ADD COLUMN "
        "source_snapshot_json TEXT DEFAULT NULL",
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_deliverables (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        role TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, role)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_deliverables_project "
        "ON screenplay_deliverables(project_id, role)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_revisions (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        deliverable_id TEXT NOT NULL,
        revision_no INTEGER NOT NULL,
        parent_revision_id TEXT DEFAULT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        content_digest TEXT NOT NULL,
        summary_json TEXT NOT NULL DEFAULT '{}',
        operation_id TEXT DEFAULT NULL UNIQUE,
        root_run_id TEXT DEFAULT NULL,
        finalizing_run_id TEXT DEFAULT NULL,
        created_by TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(deliverable_id, revision_no)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_revisions_project "
        "ON screenplay_revisions(project_id, deliverable_id, revision_no DESC)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_revision_inputs (
        revision_id TEXT NOT NULL,
        input_role TEXT NOT NULL,
        input_revision_id TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(revision_id, input_role)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_revision_inputs_source "
        "ON screenplay_revision_inputs(input_revision_id, revision_id)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_revision_parts (
        revision_id TEXT NOT NULL,
        part_type TEXT NOT NULL,
        part_key TEXT NOT NULL,
        position INTEGER NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        content_text TEXT NOT NULL DEFAULT '',
        content_digest TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(revision_id, part_type, part_key),
        UNIQUE(revision_id, part_type, position)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_revision_parts_order "
        "ON screenplay_revision_parts(revision_id, part_type, position)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_project_heads (
        project_id TEXT NOT NULL,
        deliverable_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id, deliverable_id)
    )""")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenplay_project_heads_revision "
        "ON screenplay_project_heads(project_id, revision_id)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_working_copies (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        deliverable_id TEXT NOT NULL,
        base_revision_id TEXT DEFAULT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        content_manifest_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, deliverable_id)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_working_copies_project "
        "ON screenplay_working_copies(project_id, update_time DESC)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_operations (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        command_id TEXT NOT NULL,
        target_role TEXT NOT NULL,
        intent_json TEXT NOT NULL DEFAULT '{}',
        base_project_revision INTEGER NOT NULL,
        base_heads_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'queued',
        progress_json TEXT NOT NULL DEFAULT '{}',
        result_revision_id TEXT DEFAULT NULL,
        error_json TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, command_id)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_operations_project_status "
        "ON screenplay_operations(project_id, status, update_time DESC)"
    )
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_screenplay_operations_one_active_target
        ON screenplay_operations(project_id, target_role)
        WHERE status IN ('queued', 'running', 'paused')
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_operation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(operation_id, sequence)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_operation_events_cursor "
        "ON screenplay_operation_events(operation_id, sequence)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_conversation_turns (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        command_id TEXT NOT NULL UNIQUE,
        request_digest TEXT NOT NULL,
        route TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        user_content TEXT NOT NULL,
        assistant_content TEXT NOT NULL DEFAULT '',
        runtime_profile_json TEXT NOT NULL DEFAULT '{}',
        operation_id TEXT DEFAULT NULL UNIQUE,
        run_id TEXT DEFAULT NULL UNIQUE,
        revision_id TEXT DEFAULT NULL,
        error_json TEXT DEFAULT NULL,
        execution_owner_id TEXT DEFAULT NULL,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        heartbeat_at_ms INTEGER DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        CHECK(route IN ('read_only', 'operation')),
        CHECK(status IN ('queued', 'running', 'completed', 'failed', 'canceled'))
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_conversation_turns_session "
        "ON screenplay_conversation_turns(session_id, create_time, id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_conversation_turns_recovery "
        "ON screenplay_conversation_turns(status, lease_expires_at_ms)"
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_conversation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        turn_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(turn_id, sequence)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_conversation_events_cursor "
        "ON screenplay_conversation_events(session_id, id)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_revision_source_refs (
        revision_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        excerpt TEXT NOT NULL DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(revision_id, source_type, source_id)
    )""")

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_acceptance_events (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        command_id TEXT NOT NULL,
        deliverable_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        previous_revision_id TEXT DEFAULT NULL,
        invalidated_heads_json TEXT NOT NULL DEFAULT '[]',
        actor TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, command_id)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_acceptance_events_history "
        "ON screenplay_acceptance_events(project_id, deliverable_id, create_time DESC)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_command_receipts (
        command_id TEXT PRIMARY KEY NOT NULL,
        command_type TEXT NOT NULL,
        project_id TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        result_ref TEXT NOT NULL,
        response_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_outbox_events (
        id TEXT PRIMARY KEY NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        published_at DATETIME DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_outbox_unpublished "
        "ON screenplay_outbox_events(published_at, create_time)"
    )

    await db.execute("""CREATE TRIGGER IF NOT EXISTS
        trg_screenplay_project_v2_deliverables
        AFTER INSERT ON screenplay_projects
        BEGIN
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            VALUES ('spdel:' || NEW.id || ':creativeBrief', NEW.id, 'creativeBrief');
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            VALUES ('spdel:' || NEW.id || ':structure', NEW.id, 'structure');
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            VALUES ('spdel:' || NEW.id || ':sceneList', NEW.id, 'sceneList');
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            VALUES ('spdel:' || NEW.id || ':screenplayDraft', NEW.id, 'screenplayDraft');
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            VALUES ('spdel:' || NEW.id || ':review', NEW.id, 'review');
            INSERT OR IGNORE INTO screenplay_deliverables (id, project_id, role)
            SELECT 'spdel:' || NEW.id || ':sourceAnalysis', NEW.id, 'sourceAnalysis'
            WHERE NEW.source_kind = 'book';
        END
    """)


async def init_screenplay_v2_runtime_schema(db) -> None:
    """Link generic runtime staging records after their base tables exist."""

    from database.crud.screenplay_project_deletion import (
        delete_screenplay_project_data,
    )

    legacy_projects = await db.fetch_all(
        "SELECT id FROM screenplay_projects "
        "WHERE source_snapshot_json IS NULL"
    )
    for project in legacy_projects:
        await delete_screenplay_project_data(db, str(project["id"]))

    for table in (
        "screenplay_document_source_refs",
        "screenplay_source_refs",
        "screenplay_draft_episodes",
        "screenplay_document_episodes",
        "screenplay_documents",
        "screenplay_legacy_revision_links",
        "screenplay_migration_reports",
    ):
        await db.execute(f"DROP TABLE IF EXISTS {table}")

    # Phase 5 has no legacy screenplay runtime migration. Product ownership is
    # represented only by immutable RunBinding; stale denormalized columns are
    # deleted from upgraded test databases instead of copied forward.
    for table in (
        "ai_agent_runs",
        "ai_agent_work_items",
        "ai_agent_long_tasks",
        "ai_agent_artifacts",
    ):
        await db.execute(f"DROP INDEX IF EXISTS idx_{table}_operation")
        await _try_exec(db, f"ALTER TABLE {table} DROP COLUMN operation_id")
        columns = {
            str(column["name"])
            for column in await db.fetch_all(f"PRAGMA table_info({table})")
        }
        if "operation_id" in columns:
            raise RuntimeError(
                f"failed to retire screenplay ownership column: {table}.operation_id"
            )


__all__ = [
    "init_screenplay_v2_runtime_schema",
    "init_screenplay_v2_schema",
]
