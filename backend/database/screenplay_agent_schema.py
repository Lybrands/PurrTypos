"""Conversation, Operation authority, and task projection schema.

The screenplay product aggregate and immutable Revision store live in
``screenplay_v2_schema``. A Turn owns messages, a screenplay Operation owns the
product lifecycle, and PurrA owns the generic Part execution graph.
"""

from __future__ import annotations

import hashlib
import json


async def init_screenplay_agent_schema(db) -> None:
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenplay_revisions_agent_task "
        "ON screenplay_revisions(agent_task_id) WHERE agent_task_id IS NOT NULL"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_agent_operations (
        id TEXT PRIMARY KEY NOT NULL,
        turn_id TEXT NOT NULL UNIQUE,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        long_task_id TEXT DEFAULT NULL UNIQUE,
        target_role TEXT NOT NULL,
        requirements_json TEXT NOT NULL DEFAULT '{}',
        manifest_digest TEXT NOT NULL,
        result_revision_id TEXT DEFAULT NULL,
        finalization_receipt_id TEXT DEFAULT NULL UNIQUE,
        cancel_receipt_id TEXT DEFAULT NULL UNIQUE,
        error_json TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_screenplay_agent_one_active_operation
        ON screenplay_agent_operations(project_id, session_id)
        WHERE status IN ('queued', 'running', 'paused')
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS
        screenplay_agent_operation_commands (
        command_id TEXT PRIMARY KEY NOT NULL,
        operation_id TEXT NOT NULL,
        command_type TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        response_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(operation_id, command_type, request_digest)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_agent_operation_commands "
        "ON screenplay_agent_operation_commands(operation_id, create_time)"
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
        operation_id TEXT DEFAULT NULL UNIQUE,
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
        ("operation_id", "TEXT DEFAULT NULL"),
        ("task_id", "TEXT DEFAULT NULL"),
        ("target_role", "TEXT DEFAULT NULL"),
        ("result_revision_id", "TEXT DEFAULT NULL"),
    ):
        if name not in turn_columns:
            await db.execute(
                f"ALTER TABLE screenplay_agent_turns ADD COLUMN {name} {definition}"
            )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenplay_agent_turn_operation "
        "ON screenplay_agent_turns(operation_id) WHERE operation_id IS NOT NULL"
    )
    await _migrate_actionable_turns_to_operations(db)
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


async def _migrate_actionable_turns_to_operations(db) -> None:
    turns = await db.fetch_all(
        "SELECT * FROM screenplay_agent_turns WHERE operation_id IS NULL "
        "ORDER BY rowid"
    )
    for turn in turns:
        intent = _json_object(turn.get("intent_json"))
        action = str(intent.get("action") or "").strip()
        if (
            action not in {"create", "revise", "review"}
            and not turn.get("task_id")
            and not turn.get("result_revision_id")
        ):
            continue
        requirements = {"intent": intent, "migratedFromTurn": True}
        canonical = json.dumps(
            requirements,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        turn_id = str(turn["id"])
        operation_id = (
            "spaop_migrated_"
            + hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:24]
        )
        turn_status = str(turn.get("status") or "")
        result_revision_id = str(turn.get("result_revision_id") or "").strip()
        status = {
            "queued": "queued",
            "planning": "queued",
            "running": "running",
            "paused": "paused",
            "failed": "failed",
            "canceled": "canceled",
        }.get(turn_status, "succeeded" if result_revision_id else "failed")
        error = _json_object(turn.get("error_json"))
        if status == "failed" and not error:
            error = {
                "code": "screenplay_operation_migration_incomplete",
                "message": "Legacy actionable turn has no finalized Revision.",
            }
        target_role = str(turn.get("target_role") or "").strip()
        if not target_role:
            target_role = str(intent.get("requestedDeliverable") or "").strip()
        if not target_role:
            target_role = "review" if action == "review" else "screenplayDraft"
        await db.execute(
            "INSERT OR IGNORE INTO screenplay_agent_operations "
            "(id, turn_id, project_id, session_id, status, long_task_id, "
            "target_role, requirements_json, manifest_digest, result_revision_id, "
            "error_json, create_time, update_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                operation_id,
                turn_id,
                str(turn["project_id"]),
                int(turn["session_id"]),
                status,
                str(turn.get("task_id") or "").strip() or None,
                target_role,
                canonical,
                digest,
                result_revision_id or None,
                json.dumps(error, ensure_ascii=False, separators=(",", ":"))
                if error else None,
                turn.get("create_time"),
                turn.get("update_time"),
            ],
        )
        operation = await db.fetch_one(
            "SELECT id FROM screenplay_agent_operations WHERE turn_id = ?",
            [turn_id],
        )
        if operation is not None:
            await db.execute(
                "UPDATE screenplay_agent_turns SET operation_id = ? WHERE id = ?",
                [str(operation["id"]), turn_id],
            )


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["init_screenplay_agent_schema"]
