"""
Schema initialisation & migrations – port of initDatabase() from database.js.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.id_utils import short_id8


_AGENT_ROOT_JOURNAL_MIGRATION_ID = "agent-root-journal-v1"


async def _try_exec(db: DatabaseConnection, sql: str) -> None:
    """Execute DDL that may fail (e.g. column already exists)."""
    try:
        await db.execute(sql)
    except Exception:
        pass


async def _table_columns(
    db: DatabaseConnection,
    table: str,
) -> set[str]:
    return {
        str(row["name"])
        for row in await db.fetch_all(f"PRAGMA table_info({table})")
    }


async def _table_exists(db: DatabaseConnection, table: str) -> bool:
    row = await db.fetch_one(
        "SELECT 1 AS present FROM sqlite_master "
        "WHERE type = 'table' AND name = ?",
        [table],
    )
    return row is not None


async def _migrate_legacy_agent_artifacts(db: DatabaseConnection) -> None:
    columns = await _table_columns(db, "ai_agent_artifacts")
    if not columns or "owner_ref_kind" in columns:
        return
    required = {
        "run_id",
        "artifact_scope",
        "work_item_id",
        "created_by_run_id",
    }
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        raise RuntimeError(
            f"legacy Agent artifact schema is missing columns: {missing}"
        )

    long_task_columns = await _table_columns(db, "ai_agent_long_tasks")
    has_legacy_task_mapping = "work_item_id" in long_task_columns
    if not has_legacy_task_mapping:
        work_item_artifacts = await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
            "WHERE artifact_scope = 'work_item'"
        )
        if int((work_item_artifacts or {}).get("count") or 0):
            raise RuntimeError(
                "legacy Work Item artifacts cannot be mapped to durable tasks"
            )
    task_id_lookup = (
        "(SELECT NULLIF(t.id, '') FROM ai_agent_long_tasks AS t "
        " WHERE t.work_item_id = a.work_item_id LIMIT 1)"
        if has_legacy_task_mapping
        else "NULL"
    )
    task_creator_lookup = (
        "(SELECT NULLIF(t.created_by_run_id, '') "
        " FROM ai_agent_long_tasks AS t "
        " WHERE t.work_item_id = a.work_item_id LIMIT 1)"
        if has_legacy_task_mapping
        else "NULL"
    )

    unresolved = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts AS a WHERE "
        "CASE WHEN a.artifact_scope = 'work_item' THEN "
        f"  {task_id_lookup} "
        "ELSE COALESCE(NULLIF(a.run_id, ''), NULLIF(a.created_by_run_id, '')) "
        "END IS NULL OR "
        "COALESCE(NULLIF(a.created_by_run_id, ''), NULLIF(a.run_id, ''), "
        f"  {task_creator_lookup}) IS NULL"
    )
    if int((unresolved or {}).get("count") or 0):
        raise RuntimeError(
            "legacy Agent artifacts contain unresolved owner or creator identities"
        )

    async with db.transaction():
        await db.execute("DROP TABLE IF EXISTS ai_agent_artifacts_migrating")
        await db.execute("""CREATE TABLE ai_agent_artifacts_migrating (
            id TEXT PRIMARY KEY NOT NULL,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            owner_ref_kind TEXT NOT NULL,
            owner_ref_id TEXT NOT NULL,
            created_by_run_id TEXT NOT NULL,
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
        await db.execute(f"""INSERT INTO ai_agent_artifacts_migrating (
            id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id,
            created_by_run_id, schema_version, status, revision, next_sequence,
            committed_item_count, expected_item_count, metadata_json,
            resource_ref, coverage_digest, create_time, update_time
        )
        SELECT
            a.id, a.namespace, a.kind, a.owner_id,
            CASE WHEN a.artifact_scope = 'work_item'
                 THEN 'durable_task' ELSE 'agent_run' END,
            CASE WHEN a.artifact_scope = 'work_item' THEN
                {task_id_lookup}
            ELSE COALESCE(NULLIF(a.run_id, ''), NULLIF(a.created_by_run_id, ''))
            END,
            COALESCE(
                NULLIF(a.created_by_run_id, ''),
                NULLIF(a.run_id, ''),
                {task_creator_lookup}
            ),
            a.schema_version, a.status, a.revision, a.next_sequence,
            a.committed_item_count, a.expected_item_count, a.metadata_json,
            a.resource_ref, a.coverage_digest, a.create_time, a.update_time
        FROM ai_agent_artifacts AS a""")
        await db.execute("DROP TABLE ai_agent_artifacts")
        await db.execute(
            "ALTER TABLE ai_agent_artifacts_migrating "
            "RENAME TO ai_agent_artifacts"
        )


async def _migrate_legacy_long_tasks(db: DatabaseConnection) -> None:
    columns = await _table_columns(db, "ai_agent_long_tasks")
    if "work_item_id" not in columns:
        return
    has_work_items = await _table_exists(db, "ai_agent_work_items")
    has_work_item_runs = await _table_exists(db, "ai_agent_work_item_runs")
    if has_work_items:
        unmapped = await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_work_items AS w "
            "LEFT JOIN ai_agent_long_tasks AS t ON t.work_item_id = w.id "
            "WHERE t.id IS NULL"
        )
        if int((unmapped or {}).get("count") or 0):
            raise RuntimeError(
                "legacy Agent Work Items contain rows without a durable task"
            )

    async with db.transaction():
        if has_work_item_runs:
            await db.execute("""INSERT OR IGNORE INTO ai_agent_long_task_runs (
                task_id, run_id, relation, create_time
            )
            SELECT t.id, r.run_id, r.relation, r.create_time
            FROM ai_agent_work_item_runs AS r
            JOIN ai_agent_long_tasks AS t ON t.work_item_id = r.work_item_id""")
        await db.execute("""INSERT OR IGNORE INTO ai_agent_long_task_runs (
            task_id, run_id, relation, create_time
        )
        SELECT id, created_by_run_id, 'created', create_time
        FROM ai_agent_long_tasks""")

        await db.execute("DROP TABLE IF EXISTS ai_agent_long_tasks_migrating")
        await db.execute("""CREATE TABLE ai_agent_long_tasks_migrating (
            id TEXT PRIMARY KEY NOT NULL,
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
        await db.execute("""INSERT INTO ai_agent_long_tasks_migrating (
            id, namespace, kind, owner_id, created_by_run_id, status, revision,
            total_units, completed_units, failed_units, max_parallelism,
            cancel_requested_at_ms, usage_json, metadata_json,
            create_time, update_time
        )
        SELECT id, namespace, kind, owner_id, created_by_run_id, status, revision,
            total_units, completed_units, failed_units, max_parallelism,
            cancel_requested_at_ms, usage_json, metadata_json,
            create_time, update_time
        FROM ai_agent_long_tasks""")
        await db.execute("DROP TABLE ai_agent_long_tasks")
        await db.execute(
            "ALTER TABLE ai_agent_long_tasks_migrating "
            "RENAME TO ai_agent_long_tasks"
        )
        if has_work_item_runs:
            await db.execute("DROP TABLE ai_agent_work_item_runs")
        if has_work_items:
            await db.execute("DROP TABLE ai_agent_work_items")


async def _migrate_legacy_artifact_claims(db: DatabaseConnection) -> None:
    columns = await _table_columns(db, "ai_agent_artifact_claims")
    if "work_item_id" not in columns:
        return
    async with db.transaction():
        await db.execute("DROP TABLE IF EXISTS ai_agent_artifact_claims_migrating")
        await db.execute("""CREATE TABLE ai_agent_artifact_claims_migrating (
            artifact_id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            acquired_revision INTEGER NOT NULL,
            expires_at_ms INTEGER NOT NULL,
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""INSERT INTO ai_agent_artifact_claims_migrating (
            artifact_id, run_id, claim_token, acquired_revision, expires_at_ms,
            create_time, update_time
        )
        SELECT artifact_id, run_id, claim_token, acquired_revision, expires_at_ms,
            create_time, update_time
        FROM ai_agent_artifact_claims""")
        await db.execute("DROP TABLE ai_agent_artifact_claims")
        await db.execute(
            "ALTER TABLE ai_agent_artifact_claims_migrating "
            "RENAME TO ai_agent_artifact_claims"
        )


async def _migrate_legacy_delegations(db: DatabaseConnection) -> None:
    columns = await _table_columns(db, "ai_agent_delegations")
    if not columns:
        return
    if "run_id" in columns:
        if "agent_role" not in columns:
            return
        async with db.transaction():
            await db.execute(
                "UPDATE ai_agent_delegations SET agent_name = agent_role "
                "WHERE TRIM(agent_name) = '' AND TRIM(agent_role) <> ''"
            )
            await db.execute(
                "UPDATE ai_agent_delegations SET agent_title = agent_name "
                "WHERE TRIM(agent_title) = ''"
            )
            await db.execute(
                "UPDATE ai_agent_delegations SET agent_instruction = "
                "'Execute the delegated objective.' "
                "WHERE TRIM(agent_instruction) = ''"
            )
            await db.execute(
                "ALTER TABLE ai_agent_delegations DROP COLUMN agent_role"
            )
        return
    required = {"parent_run_id", "root_run_id", "child_run_id", "agent_role"}
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        raise RuntimeError(
            f"legacy Agent delegation schema is missing columns: {missing}"
        )
    unresolved = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations "
        "WHERE TRIM(parent_run_id) = '' OR TRIM(agent_role) = ''"
    )
    if int((unresolved or {}).get("count") or 0):
        raise RuntimeError(
            "legacy Agent delegations contain unresolved Run or Agent identities"
        )

    async with db.transaction():
        await db.execute("DROP TABLE IF EXISTS ai_agent_delegations_migrating")
        await db.execute("""CREATE TABLE ai_agent_delegations_migrating (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            agent_title TEXT NOT NULL,
            agent_instruction TEXT NOT NULL,
            objective TEXT NOT NULL,
            input_json TEXT NOT NULL DEFAULT '{}',
            context_mode TEXT NOT NULL DEFAULT 'isolated',
            status TEXT NOT NULL DEFAULT 'queued',
            required INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 0,
            result_summary TEXT DEFAULT NULL,
            error TEXT DEFAULT NULL,
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""INSERT INTO ai_agent_delegations_migrating (
            id, run_id, batch_id, agent_name, agent_title, agent_instruction,
            objective, input_json, context_mode, status, required, priority,
            result_summary, error, create_time, update_time
        )
        SELECT
            id,
            parent_run_id,
            'legacy-delegation:' || id,
            agent_role,
            agent_role,
            'Execute the delegated objective.',
            objective,
            json_set(
                CASE WHEN json_valid(input_json) THEN input_json ELSE '{}' END,
                '$._legacyChildRunId', child_run_id,
                '$._legacyRootRunId', root_run_id
            ),
            'isolated',
            status,
            required,
            priority,
            result_summary,
            error,
            create_time,
            update_time
        FROM ai_agent_delegations""")
        await db.execute("DROP TABLE ai_agent_delegations")
        await db.execute(
            "ALTER TABLE ai_agent_delegations_migrating "
            "RENAME TO ai_agent_delegations"
        )


async def _migrate_agent_run_scope(db: DatabaseConnection) -> None:
    columns = {
        str(row["name"])
        for row in await db.fetch_all("PRAGMA table_info(ai_agent_runs)")
    }
    obsolete = (
        "delegation_id",
        "agent_role",
        "run_depth",
    )
    await db.execute("DROP INDEX IF EXISTS idx_ai_agent_runs_parent")
    for column in obsolete:
        if column in columns:
            await db.execute(f"ALTER TABLE ai_agent_runs DROP COLUMN {column}")

    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_runs SET root_run_id = id "
            "WHERE root_run_id IS NULL OR TRIM(root_run_id) = ''"
        )
        await db.execute(
            "UPDATE ai_agent_runs SET agent_id = id "
            "WHERE agent_id IS NULL OR TRIM(agent_id) = ''"
        )
        invalid_root = await db.fetch_one(
            "SELECT child.id FROM ai_agent_runs AS child "
            "LEFT JOIN ai_agent_runs AS root ON root.id = child.root_run_id "
            "WHERE root.id IS NULL OR root.root_run_id <> root.id LIMIT 1"
        )
        if invalid_root is not None:
            raise RuntimeError(
                "Agent Run scope contains an invalid Root Run reference: "
                f"{invalid_root['id']}"
            )
        invalid_parent = await db.fetch_one(
            "SELECT child.id FROM ai_agent_runs AS child "
            "LEFT JOIN ai_agent_runs AS parent ON parent.id = child.parent_run_id "
            "WHERE (child.id = child.root_run_id AND child.parent_run_id IS NOT NULL) "
            "OR (child.id <> child.root_run_id AND ("
            "child.parent_run_id IS NULL OR parent.id IS NULL "
            "OR parent.root_run_id <> child.root_run_id)) LIMIT 1"
        )
        if invalid_parent is not None:
            raise RuntimeError(
                "Agent Run scope contains an invalid parent Run reference: "
                f"{invalid_parent['id']}"
            )
        invalid_lease = await db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE "
            "(agent_tree_lease_owner_id IS NULL) <> "
            "(agent_tree_lease_epoch IS NULL) LIMIT 1"
        )
        if invalid_lease is not None:
            raise RuntimeError(
                "Agent Run scope contains an incomplete tree lease: "
                f"{invalid_lease['id']}"
            )


async def _migrate_agent_root_journal(db: DatabaseConnection) -> bool:
    canonical = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE event_id IS NOT NULL"
    )
    if not int((canonical or {}).get("count") or 0):
        return False

    async with db.transaction():
        mismatch = await db.fetch_one(
            "SELECT event.id FROM ai_agent_run_events AS event "
            "JOIN ai_agent_runs AS run ON run.id = event.run_id "
            "WHERE event.event_id IS NOT NULL AND ("
            "(event.root_run_id IS NOT NULL "
            "AND event.root_run_id <> run.root_run_id) OR "
            "(event.agent_id IS NOT NULL AND event.agent_id <> run.agent_id) OR "
            "(event.parent_run_id IS NOT NULL AND "
            "event.parent_run_id IS NOT run.parent_run_id)) LIMIT 1"
        )
        if mismatch is not None:
            raise RuntimeError(
                "Canonical Agent output has conflicting Root scope metadata: "
                f"{mismatch['id']}"
            )
        await db.execute(
            "UPDATE ai_agent_run_events SET "
            "root_run_id = (SELECT root_run_id FROM ai_agent_runs "
            "WHERE id = ai_agent_run_events.run_id), "
            "agent_id = (SELECT agent_id FROM ai_agent_runs "
            "WHERE id = ai_agent_run_events.run_id), "
            "parent_run_id = (SELECT parent_run_id FROM ai_agent_runs "
            "WHERE id = ai_agent_run_events.run_id) "
            "WHERE event_id IS NOT NULL AND (root_run_id IS NULL "
            "OR agent_id IS NULL OR (parent_run_id IS NULL AND EXISTS ("
            "SELECT 1 FROM ai_agent_runs WHERE id = ai_agent_run_events.run_id "
            "AND parent_run_id IS NOT NULL)))"
        )
        unresolved = await db.fetch_one(
            "SELECT event.id FROM ai_agent_run_events AS event "
            "LEFT JOIN ai_agent_runs AS run ON run.id = event.run_id "
            "WHERE event.event_id IS NOT NULL AND (run.id IS NULL "
            "OR event.root_run_id IS NOT run.root_run_id "
            "OR event.agent_id IS NOT run.agent_id "
            "OR event.parent_run_id IS NOT run.parent_run_id) LIMIT 1"
        )
        if unresolved is not None:
            raise RuntimeError(
                "Canonical Agent output references an unknown Run: "
                f"{unresolved['id']}"
            )
        missing_sequence = await db.fetch_one(
            "SELECT 1 AS value FROM ai_agent_run_events "
            "WHERE event_id IS NOT NULL AND root_sequence IS NULL LIMIT 1"
        )
        if missing_sequence is not None:
            await db.execute("""WITH maxima AS (
                SELECT root_run_id, MAX(root_sequence) AS value
                FROM ai_agent_run_events
                WHERE event_id IS NOT NULL AND root_sequence IS NOT NULL
                GROUP BY root_run_id
            ), ranked AS (
                SELECT event.id,
                    COALESCE(maxima.value, 0) + ROW_NUMBER() OVER (
                        PARTITION BY event.root_run_id ORDER BY event.id
                    ) AS value
                FROM ai_agent_run_events AS event
                LEFT JOIN maxima ON maxima.root_run_id = event.root_run_id
                WHERE event.event_id IS NOT NULL
                    AND event.root_sequence IS NULL
            )
            UPDATE ai_agent_run_events
            SET root_sequence = (
                SELECT value FROM ranked
                WHERE ranked.id = ai_agent_run_events.id
            )
            WHERE event_id IS NOT NULL AND root_sequence IS NULL""")
    return True


async def _migrate_agent_root_journal_once(db: DatabaseConnection) -> None:
    applied = await db.fetch_one(
        "SELECT 1 AS applied FROM app_schema_migrations WHERE id = ?",
        [_AGENT_ROOT_JOURNAL_MIGRATION_ID],
    )
    if applied is not None:
        return
    if await _migrate_agent_root_journal(db):
        await db.execute(
            "INSERT OR IGNORE INTO app_schema_migrations (id) VALUES (?)",
            [_AGENT_ROOT_JOURNAL_MIGRATION_ID],
        )


async def _migrate_run_cancellation_receipts(db: DatabaseConnection) -> None:
    columns = {
        str(row["name"])
        for row in await db.fetch_all(
            "PRAGMA table_info(ai_agent_run_cancellations)"
        )
    }
    if "root_run_id" in columns:
        await db.execute(
            "ALTER TABLE ai_agent_run_cancellations "
            "RENAME COLUMN root_run_id TO run_id"
        )
    if "children_canceled" in columns:
        await db.execute(
            "ALTER TABLE ai_agent_run_cancellations "
            "RENAME COLUMN children_canceled TO delegations_canceled"
        )
    if "final_status" not in columns:
        await db.execute(
            "ALTER TABLE ai_agent_run_cancellations "
            "ADD COLUMN final_status TEXT DEFAULT NULL"
        )


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
    await db.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
        id TEXT PRIMARY KEY NOT NULL,
        applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── books ────────────────────────────────────────────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS books (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        cover_color TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        enable_volume INTEGER DEFAULT 0
    )""")
    await _try_exec(db, "ALTER TABLE books ADD COLUMN enable_volume INTEGER DEFAULT 0")

    from database.continuation_schema import init_continuation_schema

    await init_continuation_schema(db)

    from database.writing_method_schema import init_writing_method_schema

    await init_writing_method_schema(db)

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
        root_run_id TEXT DEFAULT NULL,
        agent_id TEXT DEFAULT NULL,
        parent_run_id TEXT DEFAULT NULL,
        agent_tree_lease_owner_id TEXT DEFAULT NULL,
        agent_tree_lease_epoch INTEGER DEFAULT NULL,
        execution_owner_id TEXT DEFAULT NULL,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        heartbeat_at_ms INTEGER DEFAULT NULL,
        execution_attempt INTEGER NOT NULL DEFAULT 0,
        cancel_requested_at_ms INTEGER DEFAULT NULL,
        cancellation_epoch INTEGER NOT NULL DEFAULT 0,
        deadline_at_ms INTEGER DEFAULT NULL,
        runtime_limits_json TEXT NOT NULL DEFAULT '{}',
        agent_preset_snapshot_json TEXT NOT NULL DEFAULT '{}',
        plan_title TEXT NOT NULL DEFAULT 'To-dos',
        plan_goal TEXT DEFAULT NULL,
        task_spec_json TEXT DEFAULT NULL,
        work_step_ids_json TEXT DEFAULT NULL,
        execution_checkpoint_json TEXT DEFAULT NULL,
        error TEXT DEFAULT NULL,
        model_attempt_count INTEGER NOT NULL DEFAULT 0,
        unreported_usage_attempts INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        provider_output_events INTEGER NOT NULL DEFAULT 0,
        provider_output_bytes INTEGER NOT NULL DEFAULT 0,
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
        "root_run_id TEXT DEFAULT NULL",
        "agent_id TEXT DEFAULT NULL",
        "parent_run_id TEXT DEFAULT NULL",
        "agent_tree_lease_owner_id TEXT DEFAULT NULL",
        "agent_tree_lease_epoch INTEGER DEFAULT NULL",
        "execution_owner_id TEXT DEFAULT NULL",
        "lease_expires_at_ms INTEGER DEFAULT NULL",
        "heartbeat_at_ms INTEGER DEFAULT NULL",
        "execution_attempt INTEGER NOT NULL DEFAULT 0",
        "cancel_requested_at_ms INTEGER DEFAULT NULL",
        "cancellation_epoch INTEGER NOT NULL DEFAULT 0",
        "deadline_at_ms INTEGER DEFAULT NULL",
        "runtime_limits_json TEXT NOT NULL DEFAULT '{}'",
        "agent_preset_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        "plan_title TEXT NOT NULL DEFAULT 'To-dos'",
        "plan_goal TEXT DEFAULT NULL",
        "task_spec_json TEXT DEFAULT NULL",
        "work_step_ids_json TEXT DEFAULT NULL",
        "execution_checkpoint_json TEXT DEFAULT NULL",
        "error TEXT DEFAULT NULL",
        "model_attempt_count INTEGER NOT NULL DEFAULT 0",
        "unreported_usage_attempts INTEGER NOT NULL DEFAULT 0",
        "input_tokens INTEGER NOT NULL DEFAULT 0",
        "output_tokens INTEGER NOT NULL DEFAULT 0",
        "reasoning_tokens INTEGER NOT NULL DEFAULT 0",
        "provider_output_events INTEGER NOT NULL DEFAULT 0",
        "provider_output_bytes INTEGER NOT NULL DEFAULT 0",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_runs ADD COLUMN {column}",
        )
    await db.execute("DROP TRIGGER IF EXISTS ai_agent_runs_scope_immutable")
    await _migrate_agent_run_scope(db)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_root
        ON ai_agent_runs(root_run_id, id)
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_runs_parent
        ON ai_agent_runs(parent_run_id, id)
        WHERE parent_run_id IS NOT NULL
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_run_model_attempts (
        run_id TEXT NOT NULL,
        invocation_id TEXT NOT NULL,
        settled INTEGER NOT NULL DEFAULT 0,
        usage_json TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, invocation_id)
    )""")
    await db.execute(
        "DROP TRIGGER IF EXISTS ai_agent_runs_runtime_authority_immutable"
    )
    await db.execute("""CREATE TRIGGER ai_agent_runs_runtime_authority_immutable
        BEFORE UPDATE OF deadline_at_ms, runtime_limits_json,
            agent_preset_snapshot_json
        ON ai_agent_runs
        BEGIN
            SELECT RAISE(ABORT, 'agent Run runtime authority is immutable');
        END
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_run_cancellations (
        run_id TEXT PRIMARY KEY NOT NULL,
        cancellation_epoch INTEGER NOT NULL CHECK (cancellation_epoch >= 1),
        status TEXT NOT NULL CHECK (status IN ('draining', 'completed')),
        delegations_canceled INTEGER NOT NULL DEFAULT 0,
        requested_at_ms INTEGER NOT NULL,
        completed_at_ms INTEGER DEFAULT NULL,
        final_status TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _migrate_run_cancellation_receipts(db)
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
    await db.execute("DROP TRIGGER IF EXISTS ai_agent_runs_scope_immutable")
    await db.execute("""CREATE TRIGGER ai_agent_runs_scope_immutable
        BEFORE UPDATE OF
            root_run_id,
            agent_id,
            parent_run_id,
            agent_tree_lease_owner_id,
            agent_tree_lease_epoch
        ON ai_agent_runs
        BEGIN
            SELECT RAISE(ABORT, 'agent Run scope is immutable');
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
        "assignment_json TEXT DEFAULT NULL",
        "depends_on_json TEXT DEFAULT NULL",
        "protocol_private INTEGER NOT NULL DEFAULT 0",
        "planning_capability TEXT DEFAULT NULL",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_run_todos ADD COLUMN {column}",
        )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_run_todos DROP COLUMN agent_role",
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
        root_run_id TEXT DEFAULT NULL,
        agent_id TEXT DEFAULT NULL,
        parent_run_id TEXT DEFAULT NULL,
        root_sequence INTEGER DEFAULT NULL,
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
        "root_run_id TEXT DEFAULT NULL",
        "agent_id TEXT DEFAULT NULL",
        "parent_run_id TEXT DEFAULT NULL",
        "root_sequence INTEGER DEFAULT NULL",
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
    await db.execute(
        "DROP TRIGGER IF EXISTS ai_agent_run_events_canonical_immutable"
    )
    await _migrate_agent_lifecycle_events(db)
    await _migrate_agent_root_journal_once(db)
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_run_events_root_sequence
        ON ai_agent_run_events(root_run_id, root_sequence)
        WHERE event_id IS NOT NULL
    """)
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
            source_event_key,
            root_run_id,
            agent_id,
            parent_run_id,
            root_sequence
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
        output_protocol TEXT DEFAULT NULL,
        planning_run_id TEXT DEFAULT NULL,
        planning_operation_id TEXT DEFAULT NULL,
        planning_revision INTEGER NOT NULL DEFAULT 0,
        planning_attempt INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        finish_reason TEXT DEFAULT NULL,
        error_code TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    for column in (
        "output_protocol TEXT DEFAULT NULL",
        "planning_run_id TEXT DEFAULT NULL",
        "planning_operation_id TEXT DEFAULT NULL",
        "planning_revision INTEGER NOT NULL DEFAULT 0",
        "planning_attempt INTEGER NOT NULL DEFAULT 0",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE ai_agent_output_streams ADD COLUMN {column}",
        )
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
            commit_mode,
            output_protocol,
            planning_run_id,
            planning_operation_id,
            planning_revision,
            planning_attempt
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
    # ── durable Agent tasks / recoverable artifacts ──────────────
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_tasks (
        id TEXT PRIMARY KEY NOT NULL,
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
        deadline_at_ms INTEGER DEFAULT NULL,
        budget_limits_json TEXT NOT NULL DEFAULT '{}',
        cancel_requested_at_ms INTEGER DEFAULT NULL,
        usage_json TEXT NOT NULL DEFAULT '{"invocationCount":0,"unreportedUsageAttempts":0,"inputTokens":0,"outputTokens":0,"reasoningTokens":0}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_task_runs (
        task_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (task_id, run_id)
    )""")
    # Preserve ownership while the legacy Work Item mapping still exists.
    await _migrate_legacy_agent_artifacts(db)
    await _migrate_legacy_long_tasks(db)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_long_task_runs_run
        ON ai_agent_long_task_runs(run_id, create_time DESC)
    """)
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN "
        "cancel_requested_at_ms INTEGER DEFAULT NULL",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN usage_json TEXT NOT NULL "
        "DEFAULT '{\"invocationCount\":0,\"unreportedUsageAttempts\":0,\"inputTokens\":0,"
        "\"outputTokens\":0,\"reasoningTokens\":0}'",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN deadline_at_ms INTEGER DEFAULT NULL",
    )
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_tasks ADD COLUMN "
        "budget_limits_json TEXT NOT NULL DEFAULT '{}'",
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_long_task_usage (
        task_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        invocation_count INTEGER NOT NULL,
        unreported_usage_attempts INTEGER NOT NULL DEFAULT 0,
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
    await _try_exec(
        db,
        "ALTER TABLE ai_agent_long_task_usage ADD COLUMN "
        "unreported_usage_attempts INTEGER NOT NULL DEFAULT 0",
    )
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
        lease_epoch INTEGER NOT NULL DEFAULT 0,
        lease_expires_at_ms INTEGER DEFAULT NULL,
        settled_by_worker_id TEXT DEFAULT NULL,
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
        "lease_epoch INTEGER NOT NULL DEFAULT 0",
        "settled_by_worker_id TEXT DEFAULT NULL",
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
    await db.execute("""CREATE TABLE IF NOT EXISTS screenplay_checkpoint_plans (
        operation_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        checkpoint_key TEXT NOT NULL,
        root_run_id TEXT NOT NULL,
        status TEXT NOT NULL,
        input_digest TEXT NOT NULL,
        plan_json TEXT DEFAULT NULL,
        plan_digest TEXT DEFAULT NULL,
        outcome TEXT DEFAULT NULL,
        error_code TEXT DEFAULT NULL,
        reservation_owner TEXT DEFAULT NULL,
        reservation_expires_at_ms INTEGER DEFAULT NULL,
        reservation_epoch INTEGER NOT NULL DEFAULT 1,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (operation_id, checkpoint_key),
        UNIQUE (task_id, checkpoint_key)
    )""")
    for column in (
        "reservation_owner TEXT DEFAULT NULL",
        "reservation_expires_at_ms INTEGER DEFAULT NULL",
        "reservation_epoch INTEGER NOT NULL DEFAULT 1",
    ):
        await _try_exec(
            db,
            f"ALTER TABLE screenplay_checkpoint_plans ADD COLUMN {column}",
        )
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_screenplay_checkpoint_plans_root
        ON screenplay_checkpoint_plans(root_run_id, status, update_time)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS ai_agent_artifacts (
        id TEXT PRIMARY KEY NOT NULL,
        namespace TEXT NOT NULL,
        kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        owner_ref_kind TEXT NOT NULL,
        owner_ref_id TEXT NOT NULL,
        created_by_run_id TEXT NOT NULL,
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
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_owner_status
        ON ai_agent_artifacts(namespace, owner_id, kind, status, update_time DESC)
    """)
    await db.execute("DROP INDEX IF EXISTS idx_ai_agent_artifacts_run_kind_unique")
    await db.execute("DROP INDEX IF EXISTS idx_ai_agent_artifacts_run_scope_unique")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        idx_ai_agent_artifacts_owner_ref_unique
        ON ai_agent_artifacts(
            namespace, owner_id, kind, owner_ref_kind, owner_ref_id
        )
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
        run_id TEXT NOT NULL,
        claim_token TEXT NOT NULL,
        acquired_revision INTEGER NOT NULL,
        expires_at_ms INTEGER NOT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _migrate_legacy_artifact_claims(db)
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
        run_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        agent_title TEXT NOT NULL,
        agent_instruction TEXT NOT NULL,
        objective TEXT NOT NULL,
        input_json TEXT NOT NULL DEFAULT '{}',
        context_mode TEXT NOT NULL DEFAULT 'isolated',
        status TEXT NOT NULL DEFAULT 'queued',
        required INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 0,
        result_summary TEXT DEFAULT NULL,
        error TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await _migrate_legacy_delegations(db)
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_ai_agent_delegations_run_batch_status
        ON ai_agent_delegations(run_id, batch_id, status, priority DESC, create_time)
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

    # Durable business-source delivery to purra-mem0. This is an outbox only;
    # memory state/version/idempotency remain owned by the component journal.
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_source_heads (
        book_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (book_id, source_id)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_source_deliveries (
        operation_key TEXT PRIMARY KEY NOT NULL,
        book_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_revision TEXT DEFAULT NULL,
        action TEXT NOT NULL CHECK (
            action IN ('add', 'extract', 'revoke_revision', 'revoke_source')
        ),
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (
            status IN ('pending', 'delivering', 'completed', 'failed')
        ),
        attempt_count INTEGER NOT NULL DEFAULT 0,
        receipt_json TEXT DEFAULT NULL,
        error_code TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE INDEX IF NOT EXISTS
        idx_memory_source_deliveries_recovery
        ON memory_source_deliveries(status, attempt_count, create_time)
    """)
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_book_deletions (
        book_id TEXT PRIMARY KEY NOT NULL,
        operation_key TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (
            status IN ('pending', 'delivering', 'completed', 'failed')
        ),
        attempt_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── story_memory_*：章节溯源、可版本化的正式故事状态 ──────────
    # Story Memory 保存可审计的项目级当前状态，
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

    # The legacy fixed-field style model is intentionally destructive: product
    # writing methods replace it and no compatibility read is retained.
    await db.execute("DROP TABLE IF EXISTS " + "book_" + "style")

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

    from database.screenplay_tool_cache_schema import init_screenplay_tool_cache_schema

    await init_screenplay_tool_cache_schema(db)
    from database.writing_tool_cache_schema import init_writing_tool_cache_schema
    await init_writing_tool_cache_schema(db)

    # TODO: migrateEntityIdsToText8 – placeholder for entity ID migration
