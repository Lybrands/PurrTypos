from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from exceptions import AppError
from database.connection import DatabaseConnection
from database.crud.screenplay_project_deletion import delete_screenplay_project_data

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

    assert await delete_screenplay_project_data(owner_db, 'project-done') is True
    for table in (
        'screenplay_agent_operations',
        'screenplay_agent_operation_commands',
        'ai_writing_chat_requests',
        'ai_sessions',
    ):
        assert await owner_db.fetch_one(
            f"SELECT 1 AS present FROM {table} LIMIT 1"
        ) is None, table
    assert await owner_db.fetch_one(
        "SELECT session_id FROM ai_agent_runs WHERE id = 'run-project-done'"
    ) == {"session_id": None}
