from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from exceptions import AppError
from database.connection import DatabaseConnection
from database.crud.screenplay_project_deletion import delete_screenplay_project_data
from application.product_owner_deletion import prepare_session_owner_deletion

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def owner_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def test_screenplay_project_delete_rejects_active_operation(owner_db):
    await owner_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('project-active', 'Active', 'original', '{}')"
    )
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope, screenplay_project_id) "
        "VALUES (91, 'screenplay', 'project-active')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-active', 'project-active', 91, 'command-active', "
        "'planning', '继续')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, status, target_role, "
        "manifest_digest) VALUES ('operation-active', 'turn-active', "
        "'project-active', 91, 'queued', 'screenplayDraft', 'digest')"
    )

    with pytest.raises(AppError) as conflict:
        await delete_screenplay_project_data(owner_db, 'project-active')

    assert conflict.value.status_code == 409
    assert await owner_db.fetch_one(
        "SELECT id FROM screenplay_projects WHERE id = 'project-active'"
    )


@pytest.mark.parametrize(
    ("active_owner", "status"),
    [
        ("turn", "queued"),
        ("turn", "planning"),
        ("turn", "running"),
        ("turn", "paused"),
        ("work_item", "open"),
    ],
)
async def test_screenplay_project_delete_rejects_active_pre_operation_owner(
    owner_db,
    active_owner: str,
    status: str,
):
    project_id = f"project-active-{active_owner}-{status}"
    await owner_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) VALUES (?, 'Active', "
        "'original', '{}')",
        [project_id],
    )
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope, screenplay_project_id) "
        "VALUES (93, 'screenplay', ?)",
        [project_id],
    )
    if active_owner == "turn":
        await owner_db.execute(
            "INSERT INTO screenplay_agent_turns "
            "(id, project_id, session_id, command_id, status, user_content) "
            "VALUES ('turn-without-operation', ?, 93, 'command', ?, '继续')",
            [project_id, status],
        )
    else:
        await owner_db.execute(
            "INSERT INTO ai_agent_work_items "
            "(id, namespace, kind, owner_id, status) VALUES "
            "('open-work-without-operation', 'purrtypos.screenplay', "
            "'screenplayDraft', ?, 'open')",
            [project_id],
        )

    with pytest.raises(AppError) as conflict:
        await delete_screenplay_project_data(owner_db, project_id)

    assert conflict.value.status_code == 409
    assert await owner_db.fetch_one(
        "SELECT id FROM screenplay_projects WHERE id = ?",
        [project_id],
    ) == {"id": project_id}


async def test_screenplay_project_delete_cleans_terminal_operation_and_receipts(
    owner_db,
):
    await owner_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('project-done', 'Done', 'original', '{}')"
    )
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope, screenplay_project_id) "
        "VALUES (92, 'screenplay', 'project-done')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-done', 'project-done', 92, 'command-done', "
        "'completed', '完成')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, status, target_role, "
        "manifest_digest) VALUES ('operation-done', 'turn-done', "
        "'project-done', 92, 'succeeded', 'screenplayDraft', 'digest')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_operation_commands "
        "(command_id, operation_id, command_type, request_digest, receipt_id) "
        "VALUES ('resume-done', 'operation-done', 'resume', 'digest', 'receipt')"
    )
    await owner_db.execute(
        "INSERT INTO ai_writing_chat_requests "
        "(request_id, session_id, request_digest, status, rejection_code) "
        "VALUES ('request-done', 92, 'sha256:done', 'rejected', 'done')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, prompt) "
        "VALUES ('run-project-done', 92, 'done', 'done')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_host_child_runs "
        "(host_child_key, identity_digest, contract_json, generation, "
        "attempt_key, run_id, terminal_status) VALUES "
        "('project-done/task/unit/main', 'identity-done', "
        "'{\"bindingAggregateId\":\"project-done\"}', 1, "
        "'attempt-project-done', 'run-project-done', 'done')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-before-operation', 'project-done', 92, "
        "'command-before-operation', 'failed', '失败')"
    )
    await owner_db.execute(
        "INSERT INTO screenplay_agent_cancel_commands "
        "(command_id, turn_id, operation_id, request_digest, receipt_id) "
        "VALUES ('cancel-before-operation', 'turn-before-operation', NULL, "
        "'digest', 'receipt-before-operation')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, status) VALUES "
        "('work-project-done', 'purrtypos.screenplay', 'screenplayDraft', "
        "'project-done', 'completed')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES ('task-project-done', "
        "'work-project-done', 'purrtypos.screenplay', 'screenplayDraft', "
        "'project-done', 'run-project-done', 'completed', 1)"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_long_task_usage "
        "(task_id, run_id, invocation_count, input_tokens, output_tokens) "
        "VALUES ('task-project-done', 'run-project-done', 1, 2, 3)"
    )

    assert await delete_screenplay_project_data(owner_db, 'project-done') is True
    for table in (
        'screenplay_agent_operations',
        'screenplay_agent_operation_commands',
        'screenplay_agent_cancel_commands',
        'screenplay_agent_turns',
        'ai_agent_long_task_usage',
        'ai_agent_long_tasks',
        'ai_agent_work_items',
        'ai_writing_chat_requests',
        'ai_sessions',
        'ai_agent_host_child_runs',
    ):
        assert await owner_db.fetch_one(
            f"SELECT 1 AS present FROM {table} LIMIT 1"
        ) is None, table
    assert await owner_db.fetch_one(
        "SELECT session_id FROM ai_agent_runs WHERE id = 'run-project-done'"
    ) == {"session_id": None}
    await owner_db.execute(
        "INSERT INTO ai_agent_host_child_runs "
        "(host_child_key, identity_digest, contract_json, generation, "
        "attempt_key) VALUES ('project-done/task/unit/main', 'new-identity', "
        "'{\"bindingAggregateId\":\"project-done\"}', 1, "
        "'new-attempt-project-done')"
    )


async def test_session_unlink_retains_terminal_host_child_receipt(owner_db):
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope) VALUES (96, 'screenplay')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, prompt) "
        "VALUES ('run-session-audit', 96, 'done', 'done')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_host_child_runs "
        "(host_child_key, identity_digest, contract_json, generation, "
        "attempt_key, run_id, terminal_status) VALUES "
        "('project-audit/task/unit/main', 'identity-audit', "
        "'{\"bindingAggregateId\":\"project-audit\"}', 1, "
        "'attempt-project-audit', 'run-session-audit', 'done')"
    )

    await prepare_session_owner_deletion(owner_db, [96])

    assert await owner_db.fetch_one(
        "SELECT run_id, terminal_status FROM ai_agent_host_child_runs "
        "WHERE host_child_key = 'project-audit/task/unit/main'"
    ) == {"run_id": "run-session-audit", "terminal_status": "done"}
    assert await owner_db.fetch_one(
        "SELECT session_id, status FROM ai_agent_runs "
        "WHERE id = 'run-session-audit'"
    ) == {"session_id": None, "status": "done"}


@pytest.mark.parametrize("relation", ["reference", "continuation"])
async def test_project_delete_preserves_cross_owner_work_linked_to_its_run(
    owner_db,
    relation: str,
):
    for project_id in ("project-delete", "project-keep"):
        await owner_db.execute(
            "INSERT INTO screenplay_projects "
            "(id, title, source_kind, source_snapshot_json) "
            "VALUES (?, ?, 'original', '{}')",
            [project_id, project_id],
        )
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope, screenplay_project_id) "
        "VALUES (94, 'screenplay', 'project-delete')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, binding_namespace, binding_aggregate_id, "
        "binding_command_id) VALUES ('run-delete', 94, 'done', 'done', "
        "'screenplay.agent.turn', 'project-delete', 'command-delete')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, status) VALUES "
        "('work-keep', 'purrtypos.screenplay', 'screenplayDraft', "
        "'project-keep', 'completed')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) "
        "VALUES ('work-keep', 'run-delete', ?, 1)",
        [relation],
    )

    assert await delete_screenplay_project_data(owner_db, "project-delete") is True

    assert await owner_db.fetch_one(
        "SELECT owner_id FROM ai_agent_work_items WHERE id = 'work-keep'"
    ) == {"owner_id": "project-keep"}
    assert await owner_db.fetch_one(
        "SELECT relation FROM ai_agent_work_item_runs "
        "WHERE work_item_id = 'work-keep' AND run_id = 'run-delete'"
    ) == {"relation": relation}


async def test_project_delete_preserves_cross_owner_task_on_owned_work(
    owner_db,
):
    for project_id in ("project-delete-task", "project-keep-task"):
        await owner_db.execute(
            "INSERT INTO screenplay_projects "
            "(id, title, source_kind, source_snapshot_json) "
            "VALUES (?, ?, 'original', '{}')",
            [project_id, project_id],
        )
    await owner_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, status) VALUES "
        "('work-delete-task', 'purrtypos.screenplay', 'screenplayDraft', "
        "'project-delete-task', 'completed')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES ('task-keep-cross-owner', "
        "'work-delete-task', 'purrtypos.screenplay', 'screenplayDraft', "
        "'project-keep-task', 'run-cross-owner', 'completed', 1)"
    )

    assert await delete_screenplay_project_data(
        owner_db,
        "project-delete-task",
    ) is True

    assert await owner_db.fetch_one(
        "SELECT owner_id FROM ai_agent_long_tasks "
        "WHERE id = 'task-keep-cross-owner'"
    ) == {"owner_id": "project-keep-task"}
