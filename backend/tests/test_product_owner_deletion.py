from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from exceptions import AppError
from database.connection import DatabaseConnection
from database.crud.screenplay_project_deletion import delete_screenplay_project_data
from application.agent_cancellation_service import AgentCancellationService
from application.composition_factory import create_agent_composition
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
    await owner_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-without-operation', ?, 93, 'command', ?, '继续')",
        [project_id, status],
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
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, cancellation_epoch, "
        "cancel_requested_at_ms) VALUES "
        "('run-project-done', 92, 'canceled', 'done', 1, 1), "
        "('run-foreign-audit', NULL, 'done', 'foreign', 0, NULL)"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES "
        "('run-project-done', 1, 'completed', 1, 2), "
        "('run-foreign-audit', 1, 'completed', 1, 2)"
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
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES ('task-project-done', "
        "'purrtypos.screenplay', 'screenplayDraft', "
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
        'ai_writing_chat_requests',
        'ai_sessions',
    ):
        assert await owner_db.fetch_one(
            f"SELECT 1 AS present FROM {table} LIMIT 1"
        ) is None, table
    assert await owner_db.fetch_one(
        "SELECT session_id FROM ai_agent_runs WHERE id = 'run-project-done'"
    ) == {"session_id": None}
    assert await owner_db.fetch_one(
        "SELECT run_id FROM ai_agent_run_cancellations "
        "WHERE run_id = 'run-project-done'"
    ) is None
    assert await owner_db.fetch_one(
        "SELECT run_id FROM ai_agent_run_cancellations "
        "WHERE run_id = 'run-foreign-audit'"
    ) == {"run_id": "run-foreign-audit"}

    class CountingProjector:
        calls = 0

        async def project(self, run_id, receipt):
            self.calls += 1

    projector = CountingProjector()
    composition = create_agent_composition(
        owner_db,
        run_cancellation_projectors=(projector,),
    )
    try:
        replay = await AgentCancellationService(
            owner_db,
            composition,
        ).cancel("run-project-done")
    finally:
        await composition.shutdown()
    assert replay == {
        "status": "canceled",
        "newlyRequested": False,
        "delegationsCanceled": 0,
        "terminalized": False,
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
        "cancellationReceipt": "tombstoned",
    }
    assert projector.calls == 0
    assert await owner_db.fetch_one(
        "SELECT status, cancellation_epoch, execution_owner_id, "
        "lease_expires_at_ms FROM ai_agent_runs "
        "WHERE id = 'run-project-done'"
    ) == {
        "status": "canceled",
        "cancellation_epoch": 1,
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }
    assert await owner_db.fetch_one(
        "SELECT run_id FROM ai_agent_run_cancellations "
        "WHERE run_id = 'run-project-done'"
    ) is None
    await owner_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES "
        "('run-project-done', 1, 'completed', 3, 4)"
    )


async def test_session_unlink_retains_terminal_run_cancellation_receipt(owner_db):
    await owner_db.execute(
        "INSERT INTO ai_sessions (id, scope) VALUES (96, 'screenplay')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, prompt) "
        "VALUES ('run-session-audit', 96, 'done', 'done')"
    )
    await owner_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES "
        "('run-session-audit', 1, 'completed', 1, 2)"
    )

    await prepare_session_owner_deletion(owner_db, [96])

    assert await owner_db.fetch_one(
        "SELECT session_id, status FROM ai_agent_runs "
        "WHERE id = 'run-session-audit'"
    ) == {"session_id": None, "status": "done"}
    assert await owner_db.fetch_one(
        "SELECT run_id, status FROM ai_agent_run_cancellations "
        "WHERE run_id = 'run-session-audit'"
    ) == {"run_id": "run-session-audit", "status": "completed"}


async def test_project_delete_preserves_cross_owner_task(
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
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES ('task-keep-cross-owner', "
        "'purrtypos.screenplay', 'screenplayDraft', "
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
