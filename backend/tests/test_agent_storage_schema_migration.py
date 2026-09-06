from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.connection import DatabaseConnection


def _seed_legacy_storage(
    path: Path,
    *,
    unresolved_artifact: bool = False,
    orphan_work_item_artifact: bool = False,
) -> None:
    connection = sqlite3.connect(path / "purrtypos.db")
    try:
        connection.executescript("""
            CREATE TABLE ai_agent_work_items (
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
            );
            CREATE TABLE ai_agent_work_item_runs (
                work_item_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                work_item_revision INTEGER NOT NULL,
                create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (work_item_id, run_id)
            );
            CREATE TABLE ai_agent_long_tasks (
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
                metadata_json TEXT NOT NULL DEFAULT '{}',
                create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                cancel_requested_at_ms INTEGER DEFAULT NULL,
                usage_json TEXT NOT NULL DEFAULT '{"invocationCount":0,"inputTokens":0,"generationTokens":0,"reasoningTokens":0}'
            );
            CREATE TABLE ai_agent_artifacts (
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
            );
            CREATE TABLE ai_agent_artifact_claims (
                artifact_id TEXT PRIMARY KEY NOT NULL,
                work_item_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                claim_token TEXT NOT NULL,
                acquired_revision INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE ai_agent_delegations (
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
            );
            CREATE TABLE ai_agent_run_cancellations (
                root_run_id TEXT PRIMARY KEY NOT NULL,
                cancellation_epoch INTEGER NOT NULL,
                status TEXT NOT NULL,
                children_canceled INTEGER NOT NULL DEFAULT 0,
                requested_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER DEFAULT NULL,
                create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        connection.execute(
            "INSERT INTO ai_agent_work_items "
            "(id, namespace, kind, owner_id, created_by_run_id, status) "
            "VALUES ('work-1', 'purrtypos.screenplay', 'draft', "
            "'project-work', 'run-task', 'canceled')"
        )
        connection.execute(
            "INSERT INTO ai_agent_work_item_runs "
            "(work_item_id, run_id, relation, work_item_revision) "
            "VALUES ('work-1', 'run-task', 'created', 2)"
        )
        connection.execute(
            "INSERT INTO ai_agent_long_tasks "
            "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
            "status, revision, total_units) VALUES "
            "('task-1', 'work-1', 'purrtypos.screenplay', 'draft', "
            "'project-work', 'run-task', 'canceled', 2, 1)"
        )
        connection.execute(
            "INSERT INTO ai_agent_artifacts "
            "(id, namespace, kind, owner_id, run_id, artifact_scope, "
            "created_by_run_id) VALUES "
            "('artifact-run', 'purrtypos.screenplay', 'draft', "
            "'project-run', 'run-direct', 'run', 'run-direct')"
        )
        connection.execute(
            "INSERT INTO ai_agent_artifacts "
            "(id, namespace, kind, owner_id, artifact_scope, work_item_id, "
            "created_by_run_id) VALUES "
            "('artifact-task', 'purrtypos.screenplay', 'draft', "
            "'project-work', 'work_item', 'work-1', 'run-task')"
        )
        connection.execute(
            "INSERT INTO ai_agent_artifact_claims "
            "(artifact_id, work_item_id, run_id, claim_token, "
            "acquired_revision, expires_at_ms) VALUES "
            "('artifact-task', 'work-1', 'run-task', 'claim-1', 1, 99)"
        )
        connection.execute(
            "INSERT INTO ai_agent_delegations "
            "(id, parent_run_id, root_run_id, child_run_id, agent_role, "
            "objective, input_json, status, result_summary) VALUES "
            "('delegation-1', 'run-parent', 'run-root', 'run-child', "
            "'screenplay_writer', '写作', '{\"episode\":1}', 'done', '完成')"
        )
        if unresolved_artifact:
            connection.execute(
                "INSERT INTO ai_agent_artifacts "
                "(id, namespace, kind, owner_id, artifact_scope) VALUES "
                "('artifact-unresolved', 'test', 'draft', 'owner', 'run')"
            )
        if orphan_work_item_artifact:
            connection.execute(
                "INSERT INTO ai_agent_artifacts "
                "(id, namespace, kind, owner_id, artifact_scope, work_item_id, "
                "created_by_run_id) VALUES "
                "('artifact-orphan', 'test', 'draft', 'owner', 'work_item', "
                "'missing-work-item', 'run-orphan')"
            )
        connection.commit()
    finally:
        connection.close()


async def _columns(db: DatabaseConnection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in await db.fetch_all(f"PRAGMA table_info({table})")
    }


@pytest.mark.asyncio
async def test_startup_migrates_legacy_agent_storage_and_reopens_idempotently(
    tmp_path: Path,
):
    _seed_legacy_storage(tmp_path)

    first = DatabaseConnection(tmp_path)
    await first.init()
    assert await first.fetch_all(
        "SELECT id, owner_id, owner_ref_kind, owner_ref_id, created_by_run_id "
        "FROM ai_agent_artifacts ORDER BY id"
    ) == [
        {
            "id": "artifact-run",
            "owner_id": "project-run",
            "owner_ref_kind": "agent_run",
            "owner_ref_id": "run-direct",
            "created_by_run_id": "run-direct",
        },
        {
            "id": "artifact-task",
            "owner_id": "project-work",
            "owner_ref_kind": "durable_task",
            "owner_ref_id": "task-1",
            "created_by_run_id": "run-task",
        },
    ]
    assert "work_item_id" not in await _columns(first, "ai_agent_long_tasks")
    assert await first.fetch_all(
        "SELECT task_id, run_id, relation FROM ai_agent_long_task_runs"
    ) == [{"task_id": "task-1", "run_id": "run-task", "relation": "created"}]
    assert await first.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
        "('ai_agent_work_items', 'ai_agent_work_item_runs')"
    ) == []
    assert "work_item_id" not in await _columns(first, "ai_agent_artifact_claims")
    assert await first.fetch_one(
        "SELECT artifact_id, run_id, claim_token FROM ai_agent_artifact_claims"
    ) == {
        "artifact_id": "artifact-task",
        "run_id": "run-task",
        "claim_token": "claim-1",
    }
    assert await first.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'ai_agent_delegations'"
    ) == []
    cancellation_columns = await _columns(first, "ai_agent_run_cancellations")
    assert "run_id" in cancellation_columns
    assert "final_status" in cancellation_columns
    assert "root_run_id" not in cancellation_columns
    assert "children_canceled" not in cancellation_columns
    assert "delegations_canceled" not in cancellation_columns
    assert await first.fetch_one("PRAGMA integrity_check") == {
        "integrity_check": "ok"
    }
    await first.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    assert await reopened.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts"
    ) == {"count": 2}
    assert await reopened.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 1}
    assert await reopened.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'ai_agent_delegations'"
    ) == []
    await reopened.close()


@pytest.mark.asyncio
async def test_startup_refuses_unresolved_legacy_artifact_without_rewriting_it(
    tmp_path: Path,
):
    _seed_legacy_storage(tmp_path, unresolved_artifact=True)
    db = DatabaseConnection(tmp_path)

    with pytest.raises(
        RuntimeError,
        match="unresolved owner or creator identities",
    ):
        await db.init()

    assert "run_id" in await _columns(db, "ai_agent_artifacts")
    assert "owner_ref_kind" not in await _columns(db, "ai_agent_artifacts")
    assert await db.fetch_one(
        "SELECT id FROM ai_agent_artifacts WHERE id = 'artifact-unresolved'"
    ) == {"id": "artifact-unresolved"}
    await db.close()


@pytest.mark.asyncio
async def test_startup_refuses_orphan_work_item_artifact_without_guessing_owner(
    tmp_path: Path,
):
    _seed_legacy_storage(tmp_path, orphan_work_item_artifact=True)
    db = DatabaseConnection(tmp_path)

    with pytest.raises(
        RuntimeError,
        match="unresolved owner or creator identities",
    ):
        await db.init()

    assert "run_id" in await _columns(db, "ai_agent_artifacts")
    assert "owner_ref_kind" not in await _columns(db, "ai_agent_artifacts")
    assert await db.fetch_one(
        "SELECT id FROM ai_agent_artifacts WHERE id = 'artifact-orphan'"
    ) == {"id": "artifact-orphan"}
    await db.close()
