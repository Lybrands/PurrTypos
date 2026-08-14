from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.screenplay_agent_runtime_cleanup import (
    CleanupApplyInjectedFailure,
    CleanupPlanDigestMismatch,
    apply_cleanup,
    apply_existing_database,
    build_cleanup_plan,
    retire_legacy_output_tables,
)


@pytest_asyncio.fixture
async def cleanup_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await _seed(db)
        yield db
    finally:
        await db.close()


async def _seed(db: DatabaseConnection) -> None:
    await _create_legacy_output_tables(db)
    for project_id in ("project-target", "project-other"):
        await db.execute(
            "INSERT INTO screenplay_projects (id, title) VALUES (?, ?)",
            [project_id, project_id],
        )
    await db.execute(
        "INSERT INTO ai_sessions (id, title, screenplay_project_id) "
        "VALUES (10, 'target', 'project-target'), (20, 'other', 'project-other')"
    )
    await db.execute(
        "INSERT INTO ai_conversations (id, session_id, prompt, response) VALUES "
        "(100, 10, 'private target prompt', 'private target response'), "
        "(200, 20, 'private other prompt', 'private other response')"
    )
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, root_run_id) VALUES "
        "('run-target', 10, 100, 'target', 'screenplay.agent.turn', "
        "'project-target', 'turn-target', 'run-target'), "
        "('run-child', NULL, NULL, 'child', 'screenplay.agent.task', "
        "'project-target', 'task-target:unit-1', 'run-target'), "
        "('run-other', 20, 200, 'other', 'screenplay.agent.turn', "
        "'project-other', 'turn-other', 'run-other')"
    )
    await db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(root_run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES "
        "('run-target', 1, 'completed', 1, 2), "
        "('run-other', 1, 'completed', 1, 2)"
    )
    await db.execute(
        "UPDATE ai_agent_runs SET parent_run_id = 'run-target' "
        "WHERE id = 'run-child'"
    )
    await db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, user_content, "
        "planner_run_id, operation_id) VALUES "
        "('turn-target', 'project-target', 10, 'command-target', 'private', "
        "'run-target', 'operation-target'), "
        "('turn-other', 'project-other', 20, 'command-other', 'private', "
        "'run-other', 'operation-other')"
    )
    await db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, long_task_id, target_role, "
        "manifest_digest) VALUES "
        "('operation-target', 'turn-target', 'project-target', 10, "
        "'task-target', 'review', 'digest-target'), "
        "('operation-other', 'turn-other', 'project-other', 20, "
        "'task-other', 'review', 'digest-other')"
    )
    await db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id) VALUES "
        "('work-target', 'purrtypos.screenplay', 'screenplay.review', "
        "'project-target', 'run-target'), "
        "('work-other', 'purrtypos.screenplay', 'screenplay.review', "
        "'project-other', 'run-other')"
    )
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "total_units) VALUES "
        "('task-target', 'work-target', 'purrtypos.screenplay', "
        "'screenplay.review', 'project-target', 'run-target', 1), "
        "('task-other', 'work-other', 'purrtypos.screenplay', "
        "'screenplay.review', 'project-other', 'run-other', 1)"
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, run_id) VALUES "
        "('task-target', 'unit-1', 'unit-1', 1, 'run-child'), "
        "('task-other', 'unit-1', 'unit-1', 1, 'run-other')"
    )
    await db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) VALUES "
        "('work-target', 'run-target', 'created', 1), "
        "('work-other', 'run-other', 'created', 1)"
    )
    for index in range(4):
        await db.execute(
            "INSERT INTO screenplay_agent_chunks "
            "(project_id, session_id, turn_id, run_id, chunk_json) "
            "VALUES ('project-target', 10, 'turn-target', 'run-target', ?)",
            [f'{{"index":{index}}}'],
        )
    await db.execute(
        "INSERT INTO screenplay_agent_chunks "
        "(project_id, session_id, turn_id, run_id, chunk_json) VALUES "
        "('project-other', 20, 'turn-other', 'run-other', '{}')"
    )
    await db.execute(
        "INSERT INTO screenplay_agent_events "
        "(project_id, session_id, turn_id, task_id, event_type) VALUES "
        "('project-target', 10, 'turn-target', 'task-target', 'legacy'), "
        "('project-other', 20, 'turn-other', 'task-other', 'legacy')"
    )
    await db.execute(
        "INSERT INTO ai_agent_run_todos (run_id, step_id, title) VALUES "
        "('run-target', 'step-target', 'target'), "
        "('run-other', 'step-other', 'other')"
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type) VALUES "
        "('run-target', 'legacy'), ('run-child', 'legacy'), "
        "('run-other', 'legacy')"
    )
    await db.execute(
        "INSERT INTO ai_agent_approvals "
        "(id, run_id, tool_call_id, tool_name, title, risk_level, expires_at_ms) "
        "VALUES ('approval-target', 'run-target', 'call-target', 'tool', "
        "'target', 'low', 1), ('approval-other', 'run-other', 'call-other', "
        "'tool', 'other', 'low', 1)"
    )
    await db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, created_by_run_id) VALUES "
        "('artifact-target', 'purrtypos.screenplay', 'review', "
        "'project-target', 'run-target', 'run-target')"
    )
    await db.execute(
        "INSERT INTO screenplay_deliverables (id, project_id, role) VALUES "
        "('deliverable-target', 'project-target', 'cleanup-review')"
    )
    await db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "created_by, agent_task_id) VALUES "
        "('revision-target', 'project-target', 'deliverable-target', 1, "
        "'content-digest', 'screenplay_agent_task', 'task-target')"
    )
    await db.execute(
        "INSERT INTO screenplay_review_decisions "
        "(project_id, review_revision_id, draft_revision_id, issue_id, "
        "status, actor) VALUES "
        "('project-target', 'revision-target', 'revision-target', "
        "'issue-1', 'accepted', 'user')"
    )


async def _create_legacy_output_tables(db: DatabaseConnection) -> None:
    """Recreate retired stores only inside the one-time cleanup fixture."""
    await db.execute("""CREATE TABLE screenplay_agent_chunks (
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
    await db.execute("""CREATE TABLE screenplay_agent_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        turn_id TEXT DEFAULT NULL,
        task_id TEXT DEFAULT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")


@pytest.mark.asyncio
async def test_dry_run_resolves_exact_relationships_without_text_matching(cleanup_db):
    plan = await build_cleanup_plan(cleanup_db, project_ids=("project-target",))

    assert plan.table_counts["screenplay_agent_chunks"] == 4
    assert plan.run_ids == ("run-child", "run-target")
    assert "run-other" not in plan.run_ids
    assert plan.table_counts["ai_conversations"] == 1
    assert plan.table_counts["ai_agent_run_cancellations"] == 1
    assert plan.protected_counts["ai_agent_artifacts"] == 1


@pytest.mark.asyncio
async def test_cleanup_preserves_domain_documents_artifacts_and_review_decisions(
    cleanup_db,
):
    plan = await build_cleanup_plan(cleanup_db, project_ids=("project-target",))
    await apply_cleanup(
        cleanup_db,
        plan.digest,
        project_ids=("project-target",),
    )

    assert await cleanup_db.fetch_one(
        "SELECT id FROM screenplay_revisions WHERE id = 'revision-target'"
    )
    assert await cleanup_db.fetch_one(
        "SELECT id FROM ai_agent_artifacts WHERE id = 'artifact-target'"
    )
    assert await cleanup_db.fetch_one(
        "SELECT issue_id FROM screenplay_review_decisions WHERE issue_id = 'issue-1'"
    )
    assert await cleanup_db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE id = 'run-target'"
    ) is None
    assert await cleanup_db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE id = 'run-other'"
    )
    assert await cleanup_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations "
        "WHERE root_run_id = 'run-target'"
    ) is None
    assert await cleanup_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations "
        "WHERE root_run_id = 'run-other'"
    ) == {"root_run_id": "run-other"}
    await cleanup_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, root_run_id) "
        "VALUES ('run-target', 'done', 'rebuilt', 'run-target')"
    )
    await cleanup_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(root_run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES ('run-target', 1, 'completed', 3, 4)"
    )
    assert await cleanup_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations "
        "WHERE root_run_id = 'run-target'"
    ) == {"root_run_id": "run-target"}


@pytest.mark.asyncio
async def test_cleanup_digest_changes_when_owned_cancellation_receipt_appears(
    cleanup_db,
):
    await cleanup_db.execute(
        "DELETE FROM ai_agent_run_cancellations WHERE root_run_id = 'run-target'"
    )
    stale = await build_cleanup_plan(
        cleanup_db,
        project_ids=("project-target",),
    )
    await cleanup_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(root_run_id, cancellation_epoch, status, requested_at_ms) "
        "VALUES ('run-target', 1, 'completed', 1)"
    )

    with pytest.raises(CleanupPlanDigestMismatch):
        await apply_cleanup(
            cleanup_db,
            stale.digest,
            project_ids=("project-target",),
        )

    assert await cleanup_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations "
        "WHERE root_run_id = 'run-target'"
    ) == {"root_run_id": "run-target"}


@pytest.mark.asyncio
async def test_cleanup_owns_continuation_lineage_receipts_but_not_foreign_receipts(
    cleanup_db,
):
    await cleanup_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, prompt, status, "
        "binding_namespace, binding_aggregate_id, binding_command_id, "
        "binding_attributes_json, root_run_id) VALUES "
        "('run-continuation', 10, '', 'done', 'screenplay.conversation_turn', "
        "'project-target', 'resume-1', '{\"continuationOf\":\"run-target\"}', "
        "'run-continuation'), "
        "('run-continuation-child', NULL, '', 'done', "
        "'screenplay.agent.task', 'project-target', 'part-1', '{}', "
        "'run-continuation')"
    )
    await cleanup_db.execute(
        "UPDATE ai_agent_runs SET parent_run_id = 'run-continuation' "
        "WHERE id = 'run-continuation-child'"
    )
    for key, run_id in (
        ("receipt-target", "run-continuation-child"),
        ("receipt-other", "run-other"),
    ):
        await cleanup_db.execute(
            "INSERT INTO ai_agent_host_child_runs "
            "(host_child_key, identity_digest, contract_json, attempt_key, "
            "run_id, terminal_status) VALUES (?, ?, '{}', ?, ?, 'done')",
            [key, f"digest-{key}", f"attempt-{key}", run_id],
        )

    plan = await build_cleanup_plan(
        cleanup_db,
        project_ids=("project-target",),
    )
    assert "run-continuation" in plan.run_ids
    assert "run-continuation-child" in plan.run_ids
    assert plan.table_counts["ai_agent_host_child_runs"] == 1
    await apply_cleanup(
        cleanup_db,
        plan.digest,
        project_ids=("project-target",),
    )

    assert await cleanup_db.fetch_one(
        "SELECT host_child_key FROM ai_agent_host_child_runs "
        "WHERE host_child_key = 'receipt-target'"
    ) is None
    assert await cleanup_db.fetch_one(
        "SELECT host_child_key FROM ai_agent_host_child_runs "
        "WHERE host_child_key = 'receipt-other'"
    ) == {"host_child_key": "receipt-other"}
    assert await cleanup_db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE id = 'run-other'"
    )


@pytest.mark.asyncio
async def test_cleanup_rolls_back_every_table_after_any_delete_failure(cleanup_db):
    plan = await build_cleanup_plan(cleanup_db, project_ids=("project-target",))
    before = await cleanup_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_chunks"
    )

    with pytest.raises(CleanupApplyInjectedFailure):
        await apply_cleanup(
            cleanup_db,
            plan.digest,
            project_ids=("project-target",),
            fail_after_table=2,
        )

    after = await cleanup_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_chunks"
    )
    assert after == before
    assert await cleanup_db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE id = 'run-target'"
    )
    assert await cleanup_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations "
        "WHERE root_run_id = 'run-target'"
    ) == {"root_run_id": "run-target"}


@pytest.mark.asyncio
async def test_existing_database_apply_does_not_run_schema_initialization(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await _seed(db)
    plan = await build_cleanup_plan(db, project_ids=("project-target",))
    path = db.get_db_path()
    await db.close()

    await apply_existing_database(
        path,
        plan.digest,
        project_ids=("project-target",),
    )

    reopened = await aiosqlite.connect(path)
    try:
        target = await (await reopened.execute(
            "SELECT COUNT(*) FROM screenplay_agent_chunks "
            "WHERE project_id = 'project-target'"
        )).fetchone()
        other = await (await reopened.execute(
            "SELECT COUNT(*) FROM screenplay_agent_chunks "
            "WHERE project_id = 'project-other'"
        )).fetchone()
    finally:
        await reopened.close()
    assert target == (0,)
    assert other == (1,)


@pytest.mark.asyncio
async def test_legacy_tables_retire_only_when_both_are_empty(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await _create_legacy_output_tables(db)
    path = db.get_db_path()
    await db.close()

    assert await retire_legacy_output_tables(path) == (
        "screenplay_agent_chunks",
        "screenplay_agent_events",
    )
    reopened = await aiosqlite.connect(path)
    try:
        tables = await (await reopened.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('screenplay_agent_chunks', 'screenplay_agent_events')"
        )).fetchall()
    finally:
        await reopened.close()
    assert tables == []


@pytest.mark.asyncio
async def test_legacy_table_retirement_rolls_back_when_one_store_has_rows(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await _create_legacy_output_tables(db)
    await db.execute(
        "INSERT INTO screenplay_agent_events "
        "(project_id, session_id, event_type) VALUES ('project', 1, 'legacy')"
    )
    path = db.get_db_path()
    await db.close()

    with pytest.raises(RuntimeError, match="non-empty legacy table"):
        await retire_legacy_output_tables(path)

    reopened = await aiosqlite.connect(path)
    try:
        tables = await (await reopened.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('screenplay_agent_chunks', 'screenplay_agent_events')"
        )).fetchall()
    finally:
        await reopened.close()
    assert {row[0] for row in tables} == {
        "screenplay_agent_chunks",
        "screenplay_agent_events",
    }
