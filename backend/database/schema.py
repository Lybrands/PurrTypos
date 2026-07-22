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
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN thinking_durations_ms TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN duration_ms INTEGER DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN task_plan TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN context_compaction TEXT DEFAULT NULL")
    await _try_exec(db, "ALTER TABLE ai_conversations ADD COLUMN context_budget TEXT DEFAULT NULL")

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
            request_profile_digest
        ON ai_agent_runs
        WHEN
            OLD.model_provider IS NOT NEW.model_provider
            OR OLD.model_name IS NOT NEW.model_name
            OR OLD.context_window IS NOT NEW.context_window
            OR OLD.endpoint_digest IS NOT NEW.endpoint_digest
            OR OLD.request_profile_digest IS NOT NEW.request_profile_digest
        BEGIN
            SELECT RAISE(ABORT, 'agent run provenance is immutable');
        END
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_execution_lease
        ON ai_agent_runs(status, lease_expires_at_ms)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_parent
        ON ai_agent_runs(parent_run_id, create_time)
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
        result_summary TEXT DEFAULT NULL,
        error TEXT DEFAULT NULL,
        sort INTEGER DEFAULT 0,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "step_type TEXT NOT NULL DEFAULT 'analyze'",
        "risk_level TEXT DEFAULT NULL",
        "description TEXT DEFAULT NULL",
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
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
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
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, tool_call_id)
    )""")
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

    # TODO: migrateEntityIdsToText8 – placeholder for entity ID migration
