"""
Schema initialisation & migrations – port of initDatabase() from database.js.
"""

from __future__ import annotations

from datetime import datetime, timezone
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


def _utc_iso(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


async def _migrate_agent_lifecycle_events(db: DatabaseConnection) -> None:
    """Give historical lifecycle rows stable canonical replay identities."""
    lifecycle_types = (
        "run.started",
        "run.todos_updated",
        "run.todo_updated",
        "run.completed",
        "run.blocked",
        "run.failed",
        "run.canceled",
    )
    placeholders = ", ".join("?" for _ in lifecycle_types)
    rows = await db.fetch_all(
        "SELECT id, run_id, create_time FROM ai_agent_run_events "
        f"WHERE event_id IS NULL AND event_type IN ({placeholders}) "
        "ORDER BY run_id, id",
        lifecycle_types,
    )
    if not rows:
        return

    maxima = {
        str(row["run_id"]): int(row.get("max_sequence") or 0)
        for row in await db.fetch_all(
            "SELECT run_id, MAX(sequence) AS max_sequence "
            "FROM ai_agent_run_events WHERE sequence IS NOT NULL "
            "GROUP BY run_id"
        )
    }
    async with db.transaction():
        for row in rows:
            run_id = str(row["run_id"])
            sequence = maxima.get(run_id, 0) + 1
            maxima[run_id] = sequence
            row_id = int(row["id"])
            timestamp = _utc_iso(row.get("create_time"))
            await db.execute(
                "UPDATE ai_agent_run_events SET "
                "event_id = ?, sequence = ?, source = 'runtime', "
                "kind = 'run.lifecycle', channel = 'lifecycle', "
                "visibility = 'public', occurred_at = ?, emitted_at = ?, "
                "source_event_key = ? WHERE id = ? AND event_id IS NULL",
                [
                    f"legacy-run-event-{row_id}",
                    sequence,
                    timestamp,
                    timestamp,
                    f"legacy-run-event:{row_id}",
                    row_id,
                ],
            )


# 人物设定 Markdown 化迁移：旧表单字段 → profile_md 的小节布局
_CHAR_LEGACY_BASICS = [
    ("gender", "性别"), ("age", "年龄"), ("height", "身高"),
    ("occupation", "职业"), ("origin", "籍贯"),
]
_CHAR_LEGACY_SECTIONS = [
    ("appearance", "外貌"), ("personality", "性格"), ("background", "背景"),
    ("biography", "人物小传"), ("remark", "备注"),
]


def _legacy_character_profile_md(row: dict) -> str:
    """把旧的固定字段拼成 Markdown 人物档案；全空时返回空串。"""
    parts: list[str] = []
    basics = [
        f"- {label}：{str(row.get(key) or '').strip()}"
        for key, label in _CHAR_LEGACY_BASICS
        if str(row.get(key) or "").strip()
    ]
    if basics:
        parts.append("## 基本信息\n" + "\n".join(basics))
    for key, label in _CHAR_LEGACY_SECTIONS:
        val = str(row.get(key) or "").strip()
        if val:
            parts.append(f"## {label}\n{val}")
    return "\n\n".join(parts)


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

    # ── screenplay projects / versioned documents ────────────────
    # 剧本项目与书架作品是“引用”关系而不是所有权关系。source_book_id
    # 可以在来源书籍删除后置空，剧本项目及其文档仍然保留。
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_projects (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        source_kind TEXT NOT NULL DEFAULT 'original',
        source_book_id TEXT DEFAULT NULL,
        format TEXT NOT NULL DEFAULT '单集剧',
        approach TEXT NOT NULL DEFAULT '',
        premise TEXT NOT NULL DEFAULT '',
        source_scope_json TEXT NOT NULL DEFAULT '{"schemaVersion":1,"mode":"whole_book"}',
        delivery_manifest_json TEXT DEFAULT NULL,
        active_stage TEXT NOT NULL DEFAULT 'orientation',
        status TEXT NOT NULL DEFAULT 'active',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _try_exec(
        db,
        "ALTER TABLE screenplay_projects ADD COLUMN source_scope_json TEXT "
        "NOT NULL DEFAULT '{\"schemaVersion\":1,\"mode\":\"whole_book\"}'",
    )
    await _try_exec(
        db,
        "ALTER TABLE screenplay_projects ADD COLUMN delivery_manifest_json "
        "TEXT DEFAULT NULL",
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_projects_source_book "
        "ON screenplay_projects(source_book_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_projects_updated "
        "ON screenplay_projects(update_time DESC)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_source_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        agent_run_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        coverage_mode TEXT NOT NULL DEFAULT 'referenced',
        excerpt TEXT NOT NULL DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(agent_run_id, source_type, source_id)
    )""")
    await _try_exec(
        db,
        "ALTER TABLE screenplay_source_receipts ADD COLUMN coverage_mode TEXT "
        "NOT NULL DEFAULT 'referenced'",
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_source_receipts_project "
        "ON screenplay_source_receipts(project_id, create_time DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_screenplay_source_receipts_run "
        "ON screenplay_source_receipts(agent_run_id, create_time ASC)"
    )

    from database.screenplay_v2_schema import (
        init_screenplay_v2_runtime_schema,
        init_screenplay_v2_schema,
    )

    await init_screenplay_v2_schema(db)
    from database.screenplay_agent_schema import init_screenplay_agent_schema

    await init_screenplay_agent_schema(db)

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
    # scope: chapter = 章节会话（默认）；setting = 全局会话（不绑章节，整本书共享；UI 显示为「全局对话」）。
    # 历史遗留的无章节会话保持默认 chapter，不会被误判为全局会话。
    await _try_exec(db, "ALTER TABLE ai_sessions ADD COLUMN scope TEXT DEFAULT 'chapter'")
    await _try_exec(
        db,
        "ALTER TABLE ai_sessions ADD COLUMN screenplay_project_id TEXT DEFAULT NULL",
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_sessions_screenplay_project "
        "ON ai_sessions(screenplay_project_id, id DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_sessions_book "
        "ON ai_sessions(book_id, id DESC)"
    )

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
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN commentary TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN tool_call_segments TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN commentary_blocks TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN commentary_durations_ms TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN duration_ms INTEGER DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN task_plan TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN context_compaction TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN context_budget TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations DROP COLUMN screenplay_proposal")
    await _try_exec(db, "ALTER TABLE ai_conversations DROP COLUMN screenplay_revision_ref")
    await _try_exec(db, "ALTER TABLE ai_conversations DROP COLUMN thinking")
    await _try_exec(db, "ALTER TABLE ai_conversations DROP COLUMN thinking_blocks")
    await _try_exec(db, "ALTER TABLE ai_conversations DROP COLUMN thinking_durations_ms")
    remaining_conversation_columns = {
        str(column["name"])
        for column in await db.fetch_all("PRAGMA table_info(ai_conversations)")
    }
    retired_conversation_columns = {
        "screenplay_proposal",
        "screenplay_revision_ref",
        "thinking",
        "thinking_blocks",
        "thinking_durations_ms",
    }
    if remaining_conversation_columns & retired_conversation_columns:
        raise RuntimeError(
            "failed to retire legacy conversation process columns"
        )
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN agent_process TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN client_turn_id TEXT DEFAULT NULL")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_conversations_session_client_turn
        ON ai_conversations(session_id, client_turn_id)
        WHERE client_turn_id IS NOT NULL
    """)

    # Local Ask turns need an ownership record that survives Conversation
    # truncation. Agent requests use their separate stream/request receipt.
    await db.execute("""CREATE TABLE IF NOT EXISTS
        ai_local_conversation_turn_receipts (
            session_id INTEGER NOT NULL,
            client_turn_id TEXT NOT NULL,
            payload_digest TEXT DEFAULT NULL,
            status TEXT NOT NULL CHECK(status IN ('persisted', 'retired')),
            conversation_id INTEGER DEFAULT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, client_turn_id),
            CHECK(
                (status = 'persisted' AND payload_digest IS NOT NULL
                    AND conversation_id IS NOT NULL)
                OR (status = 'retired' AND conversation_id IS NULL)
            )
        )
    """)
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_local_turn_receipts_conversation
        ON ai_local_conversation_turn_receipts(conversation_id)
        WHERE conversation_id IS NOT NULL
    """)

    # ── ai_conversation_summaries ────────────────────────────────
    # Raw turns remain authoritative in ai_conversations. This table stores
    # only a replaceable model-input projection for long sessions.
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_conversation_summaries (
        session_id INTEGER PRIMARY KEY NOT NULL,
        version INTEGER NOT NULL,
        covered_through_conversation_id INTEGER NOT NULL,
        covered_turn_count INTEGER NOT NULL,
        source_digest TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        model TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── ai_agent_runs / todos / events ───────────────────────────
    # Agent Run 是一次用户请求的运行记录；To-dos 属于 run，而不是跨对话任务中心。
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_runs (
        id TEXT PRIMARY KEY NOT NULL,
        session_id INTEGER DEFAULT NULL,
        conversation_id INTEGER DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        mode TEXT DEFAULT NULL,
        prompt TEXT NOT NULL DEFAULT '',
        model_provider TEXT DEFAULT NULL,
        model_name TEXT DEFAULT NULL,
        context_window INTEGER DEFAULT NULL,
        endpoint_digest TEXT DEFAULT NULL,
        request_profile_digest TEXT DEFAULT NULL,
        requested_reasoning_mode TEXT DEFAULT NULL,
        output_contract TEXT DEFAULT NULL,
        tool_protocol_contract TEXT DEFAULT NULL,
        recovery_policy_id TEXT DEFAULT NULL,
        capability_snapshot_digest TEXT DEFAULT NULL,
        capability_snapshot_json TEXT DEFAULT NULL,
        binding_namespace TEXT DEFAULT NULL,
        binding_aggregate_id TEXT DEFAULT NULL,
        binding_command_id TEXT DEFAULT NULL,
        binding_attributes_json TEXT DEFAULT NULL,
        parent_run_id TEXT DEFAULT NULL,
        root_run_id TEXT DEFAULT NULL,
        delegation_id TEXT DEFAULT NULL,
        agent_role TEXT DEFAULT NULL,
        run_depth INTEGER NOT NULL DEFAULT 0,
        execution_owner_id TEXT DEFAULT NULL,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        heartbeat_at_ms INTEGER DEFAULT NULL,
        execution_attempt INTEGER NOT NULL DEFAULT 0,
        cancel_requested_at_ms INTEGER DEFAULT NULL,
        final_response TEXT DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    # Nullable migration preserves historical Runs. New Agent requests write
    # complete provenance atomically so diagnostics never assemble identity
    # from a partially migrated row.
    for column in (
        "model_provider TEXT DEFAULT NULL",
        "model_name TEXT DEFAULT NULL",
        "context_window INTEGER DEFAULT NULL",
        "endpoint_digest TEXT DEFAULT NULL",
        "request_profile_digest TEXT DEFAULT NULL",
        "requested_reasoning_mode TEXT DEFAULT NULL",
        "output_contract TEXT DEFAULT NULL",
        "tool_protocol_contract TEXT DEFAULT NULL",
        "recovery_policy_id TEXT DEFAULT NULL",
        "capability_snapshot_digest TEXT DEFAULT NULL",
        "capability_snapshot_json TEXT DEFAULT NULL",
        "binding_namespace TEXT DEFAULT NULL",
        "binding_aggregate_id TEXT DEFAULT NULL",
        "binding_command_id TEXT DEFAULT NULL",
        "binding_attributes_json TEXT DEFAULT NULL",
        "parent_run_id TEXT DEFAULT NULL",
        "root_run_id TEXT DEFAULT NULL",
        "delegation_id TEXT DEFAULT NULL",
        "agent_role TEXT DEFAULT NULL",
        "run_depth INTEGER NOT NULL DEFAULT 0",
        "execution_owner_id TEXT DEFAULT NULL",
        "lease_expires_at_ms INTEGER DEFAULT NULL",
        "heartbeat_at_ms INTEGER DEFAULT NULL",
        "execution_attempt INTEGER NOT NULL DEFAULT 0",
        "cancel_requested_at_ms INTEGER DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_runs ADD COLUMN {column}",
        )
    # Recreate the trigger so databases that once included additional routing
    # metadata enforce only the current model-request provenance contract.
    await db.execute("DROP TRIGGER IF EXISTS ai_agent_runs_provenance_immutable")
    await db.execute("""CREATE TRIGGER ai_agent_runs_provenance_immutable
        BEFORE UPDATE OF
            model_provider,
            model_name,
            context_window,
            endpoint_digest,
            request_profile_digest,
            requested_reasoning_mode,
            output_contract,
            tool_protocol_contract,
            recovery_policy_id,
            capability_snapshot_digest,
            capability_snapshot_json
        ON ai_agent_runs
        WHEN
            OLD.model_provider IS NOT NEW.model_provider
            OR OLD.model_name IS NOT NEW.model_name
            OR OLD.context_window IS NOT NEW.context_window
            OR OLD.endpoint_digest IS NOT NEW.endpoint_digest
            OR OLD.request_profile_digest IS NOT NEW.request_profile_digest
            OR OLD.requested_reasoning_mode IS NOT NEW.requested_reasoning_mode
            OR OLD.output_contract IS NOT NEW.output_contract
            OR OLD.tool_protocol_contract IS NOT NEW.tool_protocol_contract
            OR OLD.recovery_policy_id IS NOT NEW.recovery_policy_id
            OR OLD.capability_snapshot_digest IS NOT NEW.capability_snapshot_digest
            OR OLD.capability_snapshot_json IS NOT NEW.capability_snapshot_json
        BEGIN
            SELECT RAISE(ABORT, 'agent run provenance is immutable');
        END
    """)
    await db.execute("DROP TRIGGER IF EXISTS ai_agent_runs_binding_immutable")
    await db.execute("""CREATE TRIGGER ai_agent_runs_binding_immutable
        BEFORE UPDATE OF
            binding_namespace,
            binding_aggregate_id,
            binding_command_id,
            binding_attributes_json
        ON ai_agent_runs
        WHEN
            OLD.binding_namespace IS NOT NEW.binding_namespace
            OR OLD.binding_aggregate_id IS NOT NEW.binding_aggregate_id
            OR OLD.binding_command_id IS NOT NEW.binding_command_id
            OR OLD.binding_attributes_json IS NOT NEW.binding_attributes_json
        BEGIN
            SELECT RAISE(ABORT, 'agent run binding is immutable');
        END
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_binding
        ON ai_agent_runs(
            binding_namespace,
            binding_aggregate_id,
            binding_command_id
        )
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_execution_lease
        ON ai_agent_runs(status, lease_expires_at_ms)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_parent
        ON ai_agent_runs(parent_run_id, create_time)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_terminal_time
        ON ai_agent_runs(status, update_time DESC)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_session_time
        ON ai_agent_runs(session_id, update_time DESC)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_writing_chat_requests (
        request_id TEXT PRIMARY KEY NOT NULL,
        session_id INTEGER NOT NULL,
        request_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('accepted', 'starting', 'run_bound', 'rejected', 'canceled')
        ),
        run_id TEXT DEFAULT NULL UNIQUE,
        cancel_requested_at_ms INTEGER DEFAULT NULL,
        cancel_applied_run_id TEXT DEFAULT NULL,
        rejection_code TEXT DEFAULT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_writing_chat_requests_session_time
        ON ai_writing_chat_requests(session_id, create_time DESC)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_writing_chat_requests_run
        ON ai_writing_chat_requests(run_id)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_run_todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        executor TEXT NOT NULL DEFAULT 'model',
        step_type TEXT NOT NULL DEFAULT 'analyze',
        risk_level TEXT DEFAULT NULL,
        description TEXT DEFAULT NULL,
        expected_tools TEXT DEFAULT NULL,
        agent_role TEXT DEFAULT NULL,
        assignment_json TEXT DEFAULT NULL,
        depends_on_json TEXT DEFAULT NULL,
        result_summary TEXT DEFAULT NULL,
        error TEXT DEFAULT NULL,
        protocol_private INTEGER NOT NULL DEFAULT 0,
        planning_capability TEXT DEFAULT NULL,
        sort INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "step_type TEXT NOT NULL DEFAULT 'analyze'",
        "risk_level TEXT DEFAULT NULL",
        "description TEXT DEFAULT NULL",
        "agent_role TEXT DEFAULT NULL",
        "assignment_json TEXT DEFAULT NULL",
        "depends_on_json TEXT DEFAULT NULL",
        "protocol_private INTEGER NOT NULL DEFAULT 0",
        "planning_capability TEXT DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_run_todos ADD COLUMN {column}",
        )
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_run_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT DEFAULT NULL,
        event_id TEXT DEFAULT NULL,
        turn_id TEXT DEFAULT NULL,
        invocation_id TEXT DEFAULT NULL,
        output_stream_id TEXT DEFAULT NULL,
        sequence INTEGER DEFAULT NULL,
        source TEXT DEFAULT NULL,
        kind TEXT DEFAULT NULL,
        channel TEXT DEFAULT NULL,
        visibility TEXT DEFAULT NULL,
        occurred_at TEXT DEFAULT NULL,
        emitted_at TEXT DEFAULT NULL,
        source_event_key TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "event_id TEXT DEFAULT NULL",
        "turn_id TEXT DEFAULT NULL",
        "invocation_id TEXT DEFAULT NULL",
        "output_stream_id TEXT DEFAULT NULL",
        "sequence INTEGER DEFAULT NULL",
        "source TEXT DEFAULT NULL",
        "kind TEXT DEFAULT NULL",
        "channel TEXT DEFAULT NULL",
        "visibility TEXT DEFAULT NULL",
        "occurred_at TEXT DEFAULT NULL",
        "emitted_at TEXT DEFAULT NULL",
        "source_event_key TEXT DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_run_events ADD COLUMN {column}",
        )
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_run_events_run_type
        ON ai_agent_run_events(run_id, event_type, id)
    """)
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_run_events_event_id
        ON ai_agent_run_events(event_id)
        WHERE event_id IS NOT NULL
    """)
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_run_events_source_key
        ON ai_agent_run_events(source_event_key)
        WHERE source_event_key IS NOT NULL
    """)
    await db.execute(
        "DROP INDEX IF EXISTS idx_ai_agent_run_events_turn_sequence"
    )
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_run_events_run_sequence
        ON ai_agent_run_events(run_id, sequence)
        WHERE sequence IS NOT NULL
    """)
    await _migrate_agent_lifecycle_events(db)
    await db.execute(
        "DROP TRIGGER IF EXISTS ai_agent_run_events_canonical_immutable"
    )
    await db.execute("""CREATE TRIGGER ai_agent_run_events_canonical_immutable
        BEFORE UPDATE OF
            event_id,
            run_id,
            turn_id,
            invocation_id,
            output_stream_id,
            sequence,
            source,
            kind,
            channel,
            visibility,
            occurred_at,
            source_event_key
        ON ai_agent_run_events
        WHEN OLD.event_id IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'canonical agent output event is immutable');
        END
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_output_streams (
        id TEXT PRIMARY KEY NOT NULL,
        run_id TEXT NOT NULL,
        turn_id TEXT DEFAULT NULL,
        invocation_id TEXT NOT NULL UNIQUE,
        intent TEXT NOT NULL,
        commit_mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        finish_reason TEXT DEFAULT NULL,
        error_code TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_output_streams_run
        ON ai_agent_output_streams(run_id, create_time, id)
    """)
    await db.execute(
        "DROP TRIGGER IF EXISTS ai_agent_output_streams_identity_immutable"
    )
    await db.execute("""CREATE TRIGGER ai_agent_output_streams_identity_immutable
        BEFORE UPDATE OF
            run_id,
            turn_id,
            invocation_id,
            intent,
            commit_mode
        ON ai_agent_output_streams
        BEGIN
            SELECT RAISE(ABORT, 'agent output stream identity is immutable');
        END
    """)
    # ── ai_error_reports ─────────────────────────────────────────
    # Error reports are compact indexes over existing Run/event evidence.
    # They intentionally do not duplicate prompts, chapter text or model output.
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_error_reports (
        id TEXT PRIMARY KEY NOT NULL,
        stream_id TEXT NOT NULL UNIQUE,
        agent_run_id TEXT DEFAULT NULL,
        session_id INTEGER DEFAULT NULL,
        conversation_id INTEGER DEFAULT NULL,
        book_id TEXT DEFAULT NULL,
        chapter_id TEXT DEFAULT NULL,
        source TEXT NOT NULL DEFAULT 'ai_chat_stream',
        status TEXT NOT NULL DEFAULT 'captured',
        error_code TEXT DEFAULT NULL,
        error_message TEXT NOT NULL,
        model_name TEXT DEFAULT NULL,
        diagnostic_json TEXT NOT NULL DEFAULT '{}',
        user_note TEXT DEFAULT NULL,
        submitted_at DATETIME DEFAULT NULL,
        resolved_at DATETIME DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_error_reports_status_time
        ON ai_error_reports(status, create_time DESC)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_error_reports_agent_run
        ON ai_error_reports(agent_run_id, create_time DESC)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_approvals (
        id TEXT PRIMARY KEY NOT NULL,
        run_id TEXT NOT NULL,
        tool_call_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        title TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        expires_at_ms INTEGER NOT NULL,
        resolved_at_ms INTEGER DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_approvals_run_status
        ON ai_agent_approvals(run_id, status)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_tool_receipts (
        run_id TEXT NOT NULL,
        tool_call_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_digest TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        effects_json TEXT NOT NULL DEFAULT '[]',
        error_code TEXT DEFAULT NULL,
        step_disposition TEXT NOT NULL DEFAULT 'complete',
        planning_disposition TEXT NOT NULL DEFAULT 'keep_plan',
        effect_state TEXT NOT NULL DEFAULT 'unknown',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, tool_call_id)
    )""")
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_tool_receipts ADD COLUMN "
        "step_disposition TEXT NOT NULL DEFAULT 'complete'",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_tool_receipts ADD COLUMN "
        "effect_state TEXT NOT NULL DEFAULT 'unknown'",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_tool_receipts ADD COLUMN "
        "planning_disposition TEXT NOT NULL DEFAULT 'keep_plan'",
    )
    # ── durable Agent Work Items / recoverable artifacts ─────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_work_items (
        id TEXT PRIMARY KEY NOT NULL,
        namespace TEXT NOT NULL,
        kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        created_by_run_id TEXT DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        revision INTEGER NOT NULL DEFAULT 1,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_work_items_owner_status
        ON ai_agent_work_items(namespace, owner_id, kind, status, update_time DESC)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_work_item_runs (
        work_item_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        work_item_revision INTEGER NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (work_item_id, run_id)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_work_item_runs_run
        ON ai_agent_work_item_runs(run_id, create_time DESC)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_tasks (
        id TEXT PRIMARY KEY NOT NULL,
        work_item_id TEXT NOT NULL,
        namespace TEXT NOT NULL,
        kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        created_by_run_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        revision INTEGER NOT NULL DEFAULT 1,
        total_units INTEGER NOT NULL,
        completed_units INTEGER NOT NULL DEFAULT 0,
        failed_units INTEGER NOT NULL DEFAULT 0,
        max_parallelism INTEGER NOT NULL DEFAULT 1,
        cancel_requested_at_ms INTEGER DEFAULT NULL,
        usage_json TEXT NOT NULL DEFAULT '{"invocationCount":0,"inputTokens":0,"outputTokens":0,"reasoningTokens":0}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN "
        "cancel_requested_at_ms INTEGER DEFAULT NULL",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN usage_json TEXT NOT NULL "
        "DEFAULT '{\"invocationCount\":0,\"inputTokens\":0,"
        "\"outputTokens\":0,\"reasoningTokens\":0}'",
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_task_usage (
        task_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        invocation_count INTEGER NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        reasoning_tokens INTEGER DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (task_id, run_id)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_long_tasks_owner_status
        ON ai_agent_long_tasks(namespace, owner_id, kind, status, update_time DESC)
    """)
    # A durable workflow belongs to the conversation that created it.  The
    # earlier project-wide index caused a brand-new conversation to inherit
    # and even resume another conversation's task.  Keep race protection, but
    # scope it by the persisted originating session.
    await db.execute(
        "DROP INDEX IF EXISTS idx_ai_agent_long_tasks_one_active_owner_kind"
    )
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_long_tasks_one_active_owner_kind_session
        ON ai_agent_long_tasks(
            namespace,
            owner_id,
            kind,
            COALESCE(CAST(json_extract(metadata_json, '$.sessionId') AS TEXT), '')
        )
        WHERE status IN ('pending', 'running', 'paused')
    """)
    # Repair conversations written by the earlier SSE mapping, which copied a
    # long-task dispatch receipt into ai_conversations.response.  Those rows
    # are orchestration receipts, never model answers.
    await db.execute(
        "UPDATE ai_conversations SET response = '' WHERE id IN ("
        "  SELECT r.conversation_id FROM ai_agent_runs AS r "
        "  JOIN ai_agent_run_events AS e ON e.run_id = r.id "
        "  WHERE r.conversation_id IS NOT NULL "
        "    AND e.event_type = 'long_task.dispatched'"
        ") AND response <> ''"
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_task_units (
        task_id TEXT NOT NULL,
        unit_id TEXT NOT NULL,
        semantic_key TEXT NOT NULL,
        position INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        dependencies_json TEXT NOT NULL DEFAULT '[]',
        parent_unit_id TEXT DEFAULT NULL,
        required INTEGER NOT NULL DEFAULT 1,
        input_ref TEXT DEFAULT NULL,
        output_ref TEXT DEFAULT NULL,
        artifact_digest TEXT DEFAULT NULL,
        validation_receipt_json TEXT NOT NULL DEFAULT '{}',
        failure_json TEXT NOT NULL DEFAULT '{}',
        disposition TEXT DEFAULT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        worker_id TEXT DEFAULT NULL,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        run_id TEXT DEFAULT NULL,
        error_code TEXT DEFAULT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (task_id, unit_id),
        UNIQUE (task_id, position)
    )""")
    for column in (
        "semantic_key TEXT DEFAULT NULL",
        "parent_unit_id TEXT DEFAULT NULL",
        "required INTEGER NOT NULL DEFAULT 1",
        "artifact_digest TEXT DEFAULT NULL",
        "validation_receipt_json TEXT NOT NULL DEFAULT '{}'",
        "failure_json TEXT NOT NULL DEFAULT '{}'",
        "disposition TEXT DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_long_task_units ADD COLUMN {column}",
        )
    await db.execute(
        "UPDATE ai_agent_long_task_units SET semantic_key = unit_id "
        "WHERE semantic_key IS NULL OR TRIM(semantic_key) = ''"
    )
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_long_task_units_semantic_key
        ON ai_agent_long_task_units(task_id, semantic_key)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_long_task_units_ready
        ON ai_agent_long_task_units(task_id, status, position)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_artifacts (
        id TEXT PRIMARY KEY NOT NULL,
        namespace TEXT NOT NULL,
        kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        run_id TEXT DEFAULT NULL,
        artifact_scope TEXT NOT NULL DEFAULT 'run',
        work_item_id TEXT DEFAULT NULL,
        created_by_run_id TEXT DEFAULT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'open',
        revision INTEGER NOT NULL DEFAULT 1,
        next_sequence INTEGER NOT NULL DEFAULT 1,
        committed_item_count INTEGER NOT NULL DEFAULT 0,
        expected_item_count INTEGER DEFAULT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        resource_ref TEXT DEFAULT NULL,
        coverage_digest TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "artifact_scope TEXT NOT NULL DEFAULT 'run'",
        "work_item_id TEXT DEFAULT NULL",
        "created_by_run_id TEXT DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_artifacts ADD COLUMN {column}",
        )
    # Legacy artifacts were all Run-scoped. Preserve their original Run as
    # immutable creator provenance while making the new scope explicit.
    await db.execute(
        "UPDATE ai_agent_artifacts SET artifact_scope = 'run' "
        "WHERE artifact_scope IS NULL OR artifact_scope = ''"
    )
    await db.execute(
        "UPDATE ai_agent_artifacts SET created_by_run_id = run_id "
        "WHERE created_by_run_id IS NULL AND run_id IS NOT NULL"
    )
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_owner_status
        ON ai_agent_artifacts(namespace, owner_id, kind, status, update_time DESC)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_run
        ON ai_agent_artifacts(run_id, update_time DESC)
    """)
    # The legacy index did not understand Work Item scope. Recreate it with a
    # scope predicate so creator provenance cannot make a Work Item artifact
    # collide with a Run-owned artifact.
    await db.execute("DROP INDEX IF EXISTS idx_ai_agent_artifacts_run_kind_unique")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_run_scope_unique
        ON ai_agent_artifacts(namespace, owner_id, kind, run_id)
        WHERE artifact_scope = 'run' AND run_id IS NOT NULL
    """)
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_work_item_kind_unique
        ON ai_agent_artifacts(namespace, owner_id, kind, work_item_id)
        WHERE artifact_scope = 'work_item' AND work_item_id IS NOT NULL
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_artifact_batches (
        artifact_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        committed_revision INTEGER NOT NULL,
        next_sequence INTEGER NOT NULL,
        item_count INTEGER NOT NULL,
        items_json TEXT NOT NULL,
        coverage_keys_json TEXT NOT NULL DEFAULT '[]',
        content_digest TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (artifact_id, batch_id),
        UNIQUE(artifact_id, idempotency_key),
        UNIQUE(artifact_id, sequence)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifact_batches_sequence
        ON ai_agent_artifact_batches(artifact_id, sequence)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_artifact_claims (
        artifact_id TEXT PRIMARY KEY NOT NULL,
        work_item_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        claim_token TEXT NOT NULL,
        acquired_revision INTEGER NOT NULL,
        expires_at_ms INTEGER NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifact_claims_run
        ON ai_agent_artifact_claims(run_id, expires_at_ms)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifact_claims_expiry
        ON ai_agent_artifact_claims(expires_at_ms)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_artifact_projections (
        artifact_id TEXT NOT NULL,
        projector_namespace TEXT NOT NULL,
        result_ref TEXT NOT NULL,
        projected_by_run_id TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (artifact_id, projector_namespace)
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifact_projections_result
        ON ai_agent_artifact_projections(projector_namespace, result_ref)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_delegations (
        id TEXT PRIMARY KEY NOT NULL,
        parent_run_id TEXT NOT NULL,
        root_run_id TEXT NOT NULL,
        child_run_id TEXT DEFAULT NULL,
        agent_role TEXT NOT NULL,
        objective TEXT NOT NULL,
        input_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'queued',
        required INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 0,
        worker_id TEXT DEFAULT NULL,
        claim_expires_at_ms INTEGER DEFAULT NULL,
        claim_attempt INTEGER NOT NULL DEFAULT 0,
        result_summary TEXT DEFAULT NULL,
        error TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "claim_expires_at_ms INTEGER DEFAULT NULL",
        "claim_attempt INTEGER NOT NULL DEFAULT 0",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_delegations ADD COLUMN {column}",
        )
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_delegations_parent_status
        ON ai_agent_delegations(parent_run_id, status, priority DESC, create_time)
    """)
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

    # ── ai_prompt_templates ──────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_prompt_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        sort INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

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

    # ── memory_items：长期记忆统一召回面 ───────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        scope_type TEXT NOT NULL DEFAULT 'book',
        scope_id TEXT DEFAULT NULL,
        content TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        keywords TEXT NOT NULL DEFAULT '',
        importance INTEGER NOT NULL DEFAULT 3,
        confidence REAL NOT NULL DEFAULT 1.0,
        status TEXT NOT NULL DEFAULT 'active',
        pinned INTEGER NOT NULL DEFAULT 0,
        fingerprint TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT 'manual',
        source_id TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_used_at DATETIME DEFAULT NULL
    )""")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN keywords TEXT NOT NULL DEFAULT ''")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN importance INTEGER NOT NULL DEFAULT 3")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual'")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN source_id TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE memory_items ADD COLUMN last_used_at DATETIME DEFAULT NULL")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_book_status "
        "ON memory_items(book_id, status, kind)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_source "
        "ON memory_items(source_type, source_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_scope "
        "ON memory_items(book_id, scope_type, scope_id)"
    )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_fingerprint "
        "ON memory_items(book_id, fingerprint) WHERE fingerprint != ''"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS memory_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        from_memory_id INTEGER NOT NULL,
        to_memory_id INTEGER NOT NULL,
        relation TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_links_book "
        "ON memory_links(book_id, relation)"
    )

    await _try_exec(db, """CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
        USING fts5(content, summary, keywords, tokenize=trigram, content=memory_items, content_rowid=id)""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS memory_items_fts_ai
        AFTER INSERT ON memory_items BEGIN
            INSERT INTO memory_items_fts(rowid, content, summary, keywords)
            VALUES (new.id, new.content, new.summary, new.keywords);
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS memory_items_fts_au
        AFTER UPDATE ON memory_items BEGIN
            INSERT INTO memory_items_fts(memory_items_fts, rowid, content, summary, keywords)
                VALUES ('delete', old.id, old.content, old.summary, old.keywords);
            INSERT INTO memory_items_fts(rowid, content, summary, keywords)
                VALUES (new.id, new.content, new.summary, new.keywords);
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS memory_items_fts_ad
        AFTER DELETE ON memory_items BEGIN
            INSERT INTO memory_items_fts(memory_items_fts, rowid, content, summary, keywords)
                VALUES ('delete', old.id, old.content, old.summary, old.keywords);
        END""")

    # 非破坏式迁移：把旧「本书设定 / 伏笔」镜像进统一记忆池。
    await _try_exec(db, """
        INSERT INTO memory_items (
            book_id, kind, scope_type, scope_id, content, importance, status,
            fingerprint, source_type, source_id, create_time, update_time
        )
        SELECT
            book_id,
            'canon',
            CASE
                WHEN character_id IS NOT NULL THEN 'character'
                WHEN chapter_id IS NOT NULL THEN 'chapter'
                ELSE 'book'
            END,
            COALESCE(CAST(character_id AS TEXT), chapter_id),
            content,
            4,
            'active',
            'legacy:spark:' || id,
            'spark_idea',
            CAST(id AS TEXT),
            create_time,
            create_time
        FROM ai_memories AS old
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_items AS mi
            WHERE mi.source_type = 'spark_idea' AND mi.source_id = CAST(old.id AS TEXT)
        )
    """)
    await _try_exec(db, """
        INSERT INTO memory_items (
            book_id, kind, scope_type, scope_id, content, keywords, importance, status,
            fingerprint, source_type, source_id, create_time, update_time
        )
        SELECT
            book_id,
            'foreshadowing',
            'chapter',
            chapter_id,
            content,
            type || ' ' || status,
            4,
            CASE WHEN status = '已回收' THEN 'archived' ELSE 'active' END,
            'legacy:foreshadowing:' || id,
            'foreshadowing',
            CAST(id AS TEXT),
            create_time,
            update_time
        FROM ai_foreshadowing AS old
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_items AS mi
            WHERE mi.source_type = 'foreshadowing' AND mi.source_id = CAST(old.id AS TEXT)
        )
    """)
    await _try_exec(db, "INSERT INTO memory_items_fts(memory_items_fts) VALUES ('rebuild')")

    # ── story_memory_*：章节溯源、可版本化的正式故事状态 ──────────
    # memory_items 继续承担通用召回；这里保存可审计的项目级当前状态，
    # 以及每章带来的原子变化。两者在后续召回阶段通过适配器连接。
    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_records (
        id TEXT PRIMARY KEY NOT NULL,
        book_id TEXT NOT NULL,
        memory_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject_id TEXT DEFAULT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'confirmed',
        lifecycle TEXT NOT NULL DEFAULT 'active',
        provenance_status TEXT NOT NULL DEFAULT 'valid',
        version INTEGER NOT NULL DEFAULT 1,
        last_delta_id TEXT DEFAULT NULL,
        last_source_id TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(book_id, memory_key)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_records_book_kind "
        "ON story_memory_records(book_id, lifecycle, kind)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_records_subject "
        "ON story_memory_records(book_id, subject_id)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_deltas (
        id TEXT PRIMARY KEY NOT NULL,
        book_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        source_revision TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        applied_at DATETIME DEFAULT NULL,
        reverted_at DATETIME DEFAULT NULL,
        invalidated_at DATETIME DEFAULT NULL
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_deltas_chapter "
        "ON story_memory_deltas(book_id, chapter_id, status, create_time)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_analysis_runs (
        id TEXT PRIMARY KEY NOT NULL,
        book_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        delta_id TEXT DEFAULT NULL,
        model_provider TEXT NOT NULL DEFAULT '',
        model_name TEXT NOT NULL DEFAULT '',
        candidate_count INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(book_id, chapter_id, source_revision)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_analysis_chapter "
        "ON story_memory_analysis_runs(book_id, chapter_id, create_time)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_evolution_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        delta_id TEXT NOT NULL,
        target_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        classification TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        risk TEXT NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        field_changes_json TEXT NOT NULL DEFAULT '[]',
        candidate_payload_json TEXT NOT NULL DEFAULT '{}',
        source_excerpt TEXT NOT NULL DEFAULT '',
        related_memory_key TEXT DEFAULT NULL,
        existing_record_id TEXT DEFAULT NULL,
        existing_version INTEGER DEFAULT NULL,
        candidate_confidence REAL NOT NULL DEFAULT 1.0,
        review_status TEXT NOT NULL DEFAULT 'open',
        resolution TEXT NOT NULL DEFAULT 'pending',
        resolved_delta_id TEXT DEFAULT NULL,
        resolution_actor TEXT DEFAULT NULL,
        resolved_at DATETIME DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(delta_id, target_key)
    )""")
    await _try_exec(
        db,
        "ALTER TABLE story_memory_evolution_reviews "
        "ADD COLUMN candidate_payload_json TEXT NOT NULL DEFAULT '{}'",
    )
    await _try_exec(
        db,
        "ALTER TABLE story_memory_evolution_reviews "
        "ADD COLUMN source_excerpt TEXT NOT NULL DEFAULT ''",
    )
    await _try_exec(
        db,
        "ALTER TABLE story_memory_evolution_reviews "
        "ADD COLUMN resolved_delta_id TEXT DEFAULT NULL",
    )
    await _try_exec(
        db,
        "ALTER TABLE story_memory_evolution_reviews "
        "ADD COLUMN resolution_actor TEXT DEFAULT NULL",
    )
    await _try_exec(
        db,
        "ALTER TABLE story_memory_evolution_reviews "
        "ADD COLUMN resolved_at DATETIME DEFAULT NULL",
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_evolution_book_status "
        "ON story_memory_evolution_reviews(book_id, review_status, create_time)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_sources (
        id TEXT PRIMARY KEY NOT NULL,
        delta_id TEXT NOT NULL,
        operation_index INTEGER NOT NULL,
        book_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        source_revision TEXT NOT NULL DEFAULT '',
        excerpt TEXT NOT NULL,
        locator_json TEXT NOT NULL DEFAULT '{}',
        narrative_order INTEGER DEFAULT NULL,
        story_time TEXT DEFAULT NULL,
        story_time_precision TEXT DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'valid',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(delta_id, operation_index)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_sources_chapter "
        "ON story_memory_sources(book_id, chapter_id, status)"
    )

    # Story Memory records can refer to several entities at once (relationship
    # endpoints, event participants/location, plot-thread entities). A single
    # subject_id cannot support broad entity recall, so materialize every role.
    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_entity_links (
        record_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        role TEXT NOT NULL,
        PRIMARY KEY (record_id, entity_type, entity_id, role)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_entity_links_lookup "
        "ON story_memory_entity_links(book_id, entity_type, entity_id)"
    )
    await db.execute("""CREATE TRIGGER IF NOT EXISTS story_memory_entity_links_ad
        AFTER DELETE ON story_memory_records BEGIN
            DELETE FROM story_memory_entity_links WHERE record_id = old.id;
        END""")

    # Keep the first-stage lexical channel independent from the final LLM
    # relevance judgment. FTS may be unavailable in custom SQLite builds; every
    # caller retains a LIKE compatibility fallback.
    await _try_exec(db, """CREATE VIRTUAL TABLE IF NOT EXISTS story_memory_fts
        USING fts5(
            memory_key,
            subject_id,
            payload_json,
            source_excerpt,
            book_id UNINDEXED,
            record_id UNINDEXED,
            tokenize=trigram
        )""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS story_memory_fts_ai
        AFTER INSERT ON story_memory_records BEGIN
            INSERT INTO story_memory_fts(
                memory_key, subject_id, payload_json, source_excerpt,
                book_id, record_id
            ) VALUES (
                new.memory_key, COALESCE(new.subject_id, ''), new.payload_json,
                COALESCE((
                    SELECT excerpt FROM story_memory_sources
                    WHERE id = new.last_source_id
                ), ''),
                new.book_id, new.id
            );
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS story_memory_fts_au
        AFTER UPDATE ON story_memory_records BEGIN
            DELETE FROM story_memory_fts WHERE record_id = old.id;
            INSERT INTO story_memory_fts(
                memory_key, subject_id, payload_json, source_excerpt,
                book_id, record_id
            ) VALUES (
                new.memory_key, COALESCE(new.subject_id, ''), new.payload_json,
                COALESCE((
                    SELECT excerpt FROM story_memory_sources
                    WHERE id = new.last_source_id
                ), ''),
                new.book_id, new.id
            );
        END""")
    await _try_exec(db, """CREATE TRIGGER IF NOT EXISTS story_memory_fts_ad
        AFTER DELETE ON story_memory_records BEGIN
            DELETE FROM story_memory_fts WHERE record_id = old.id;
        END""")

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_delta_operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        delta_id TEXT NOT NULL,
        operation_index INTEGER NOT NULL,
        operation TEXT NOT NULL,
        target_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject_id TEXT DEFAULT NULL,
        after_payload_json TEXT NOT NULL DEFAULT '{}',
        after_status TEXT NOT NULL DEFAULT 'confirmed',
        confidence REAL NOT NULL DEFAULT 1.0,
        source_id TEXT NOT NULL,
        before_record_json TEXT DEFAULT NULL,
        after_record_json TEXT DEFAULT NULL,
        applied_version INTEGER DEFAULT NULL,
        UNIQUE(delta_id, operation_index),
        UNIQUE(delta_id, target_key)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_operations_delta "
        "ON story_memory_delta_operations(delta_id, operation_index)"
    )

    await db.execute("""CREATE TABLE IF NOT EXISTS story_memory_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        memory_key TEXT NOT NULL,
        version INTEGER NOT NULL,
        delta_id TEXT NOT NULL,
        action TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject_id TEXT DEFAULT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        lifecycle TEXT NOT NULL,
        provenance_status TEXT NOT NULL,
        source_id TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(record_id, version)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_memory_versions_key "
        "ON story_memory_versions(book_id, memory_key, version)"
    )

    # Non-destructive rebuilds cover records written by older application
    # versions before the indexes and link projection existed.
    await db.execute("DELETE FROM story_memory_entity_links")
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT id, book_id, 'generic', subject_id, 'subject'
        FROM story_memory_records
        WHERE subject_id IS NOT NULL AND TRIM(subject_id) != ''
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT id, book_id, 'character',
               CAST(json_extract(payload_json, '$.characterId') AS TEXT),
               'subject'
        FROM story_memory_records
        WHERE kind = 'character_state' AND json_valid(payload_json)
          AND json_extract(payload_json, '$.characterId') IS NOT NULL
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT id, book_id, 'character',
               CAST(json_extract(payload_json, '$.sourceCharacterId') AS TEXT),
               'source'
        FROM story_memory_records
        WHERE kind = 'relationship_state' AND json_valid(payload_json)
          AND json_extract(payload_json, '$.sourceCharacterId') IS NOT NULL
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT id, book_id, 'character',
               CAST(json_extract(payload_json, '$.targetCharacterId') AS TEXT),
               'target'
        FROM story_memory_records
        WHERE kind = 'relationship_state' AND json_valid(payload_json)
          AND json_extract(payload_json, '$.targetCharacterId') IS NOT NULL
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT r.id, r.book_id, 'character', CAST(value AS TEXT), 'participant'
        FROM story_memory_records AS r,
             json_each(r.payload_json, '$.participantIds')
        WHERE r.kind = 'timeline_event' AND json_valid(r.payload_json)
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT id, book_id, 'setting',
               CAST(json_extract(payload_json, '$.locationId') AS TEXT),
               'location'
        FROM story_memory_records
        WHERE kind = 'timeline_event' AND json_valid(payload_json)
          AND json_extract(payload_json, '$.locationId') IS NOT NULL
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT r.id, r.book_id, 'generic', CAST(value AS TEXT), 'related'
        FROM story_memory_records AS r,
             json_each(r.payload_json, '$.relatedEntityIds')
        WHERE r.kind = 'plot_thread' AND json_valid(r.payload_json)
    """)
    await _try_exec(db, """
        INSERT OR IGNORE INTO story_memory_entity_links
            (record_id, book_id, entity_type, entity_id, role)
        SELECT r.id, r.book_id, 'character', CAST(value AS TEXT), 'known_by'
        FROM story_memory_records AS r,
             json_each(r.payload_json, '$.knownByCharacterIds')
        WHERE r.kind = 'world_fact' AND json_valid(r.payload_json)
    """)
    await _try_exec(db, "DELETE FROM story_memory_fts")
    await _try_exec(db, """
        INSERT INTO story_memory_fts(
            memory_key, subject_id, payload_json, source_excerpt,
            book_id, record_id
        )
        SELECT r.memory_key, COALESCE(r.subject_id, ''), r.payload_json,
               COALESCE(s.excerpt, ''), r.book_id, r.id
        FROM story_memory_records AS r
        LEFT JOIN story_memory_sources AS s ON s.id = r.last_source_id
    """)

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

    # ── book_style ───────────────────────────────────────────────
    # 每本书一份「风格基调」，强制注入 system prompt（写作专家模式）
    await db.execute("""CREATE TABLE IF NOT EXISTS book_style (
        book_id TEXT NOT NULL PRIMARY KEY,
        pov TEXT DEFAULT '',
        tone TEXT DEFAULT '',
        pace TEXT DEFAULT '',
        banned_rules TEXT DEFAULT '',
        reference_chapter_ids TEXT DEFAULT '',
        free_notes TEXT DEFAULT '',
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── chapter_canvas ───────────────────────────────────────────
    # 每章一个 AI 草稿区（与 articles 一对一），AI 写到 canvas 上不污染正文，
    # 用户点「合并到正文」走 diff 流程后才落地到 articles。
    await db.execute("""CREATE TABLE IF NOT EXISTS chapter_canvas (
        chapter_id TEXT NOT NULL PRIMARY KEY,
        content TEXT NOT NULL DEFAULT '',
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── outline_history ──────────────────────────────────────────
    # 大纲修订历史；每次 update_outline / editGlobalOutline 落库前快照旧值，
    # 让用户能把被 LLM 改坏的大纲一键回退。
    await db.execute("""CREATE TABLE IF NOT EXISTS outline_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        outline_id TEXT NOT NULL,
        before_title TEXT DEFAULT NULL,
        before_type TEXT DEFAULT NULL,
        before_markdown_content TEXT DEFAULT NULL,
        before_xmind_data TEXT DEFAULT NULL,
        source TEXT NOT NULL DEFAULT 'user',
        note TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_outline_history_outline "
        "ON outline_history(outline_id, create_time DESC)"
    )

    # ── chapter_diff_history ─────────────────────────────────────
    # AI 改正文产生的 diff 历史；commit 时同时落盘 articles
    await db.execute("""CREATE TABLE IF NOT EXISTS chapter_diff_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chapter_id TEXT NOT NULL,
        before_text TEXT NOT NULL DEFAULT '',
        after_text TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'ai_rewrite',
        accepted_segments INTEGER DEFAULT 0,
        rejected_segments INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_diff_history_chapter "
        "ON chapter_diff_history(chapter_id, create_time DESC)"
    )

    # ── character_history ────────────────────────────────────────
    # 人物设定修订历史；commit diff 或手动保存前快照旧值，支持回滚。
    await db.execute("""CREATE TABLE IF NOT EXISTS character_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id INTEGER NOT NULL,
        before_name TEXT DEFAULT '',
        before_tags TEXT DEFAULT '',
        before_profile_md TEXT DEFAULT '',
        after_name TEXT DEFAULT '',
        after_tags TEXT DEFAULT '',
        after_profile_md TEXT DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user',
        accepted_segments INTEGER DEFAULT 0,
        rejected_segments INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_character_history_character "
        "ON character_history(character_id, create_time DESC)"
    )

    # ── story_background_history ─────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS story_background_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        before_content TEXT NOT NULL DEFAULT '',
        after_content TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user',
        accepted_segments INTEGER DEFAULT 0,
        rejected_segments INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_story_background_history_book "
        "ON story_background_history(book_id, create_time DESC)"
    )

    # ── story_background_attachments ─────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS story_background_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        name TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── setting_entities ─────────────────────────────────────────
    # 世界设定实体（地点 / 势力 / 物品 / 其他），结构与人物卡一致：
    # name / tags 结构化，正文统一 profile_md。
    await db.execute("""CREATE TABLE IF NOT EXISTS setting_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT 'location',
        name TEXT NOT NULL,
        tags TEXT DEFAULT '',
        profile_md TEXT DEFAULT '',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_setting_entities_book "
        "ON setting_entities(book_id, entity_type)"
    )

    # ── setting_entity_history ───────────────────────────────────
    # 设定实体修订历史；与 character_history 同构，支持 diff 审阅与回滚。
    await db.execute("""CREATE TABLE IF NOT EXISTS setting_entity_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id INTEGER NOT NULL,
        before_name TEXT DEFAULT '',
        before_tags TEXT DEFAULT '',
        before_profile_md TEXT DEFAULT '',
        after_name TEXT DEFAULT '',
        after_tags TEXT DEFAULT '',
        after_profile_md TEXT DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user',
        accepted_segments INTEGER DEFAULT 0,
        rejected_segments INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_setting_entity_history_entity "
        "ON setting_entity_history(entity_id, create_time DESC)"
    )

    # ── book_word_stats ──────────────────────────────────────────
    # 每书每日字数快照（当日结束时的全书总字数），用于日更统计 / 连续达标。
    # 正文保存时增量更新当日行；统计接口读取时用全量字数自校正。
    await db.execute("""CREATE TABLE IF NOT EXISTS book_word_stats (
        book_id TEXT NOT NULL,
        date TEXT NOT NULL,
        total_words INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (book_id, date)
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

    # ── 人物设定 Markdown 化迁移 ──────────────────────────────────
    # 表单字段（性别/年龄/…/小传/备注）合并为一篇 profile_md；name / tags 仍保留
    # 结构化（卡片列表与 AI 工具按姓名/标签定位需要）。旧列保留不再写入。
    # NULL 作为「尚未迁移」哨兵：迁移后至少写入空串，保证幂等。
    await _try_exec(db, "ALTER TABLE characters ADD COLUMN profile_md TEXT DEFAULT NULL")
    unmigrated = await db.fetch_all("SELECT * FROM characters WHERE profile_md IS NULL")
    for row in unmigrated:
        await db.execute(
            "UPDATE characters SET profile_md = ? WHERE id = ?",
            [_legacy_character_profile_md(row), row["id"]],
        )

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

    # Retire the pre-v2 screenplay store only after every generic Agent table
    # used by project deletion has been initialized. This keeps first startup,
    # repeated startup, and a legacy-database upgrade on the same code path.
    await init_screenplay_v2_runtime_schema(db)

    # TODO: migrateEntityIdsToText8 – placeholder for entity ID migration
