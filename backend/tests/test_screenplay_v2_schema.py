from __future__ import annotations

from pathlib import Path

import pytest

from database.connection import DatabaseConnection


pytestmark = pytest.mark.asyncio


async def test_startup_drops_discarded_conversation_and_operation_tables(
    tmp_path: Path,
):
    first = DatabaseConnection(tmp_path)
    await first.init()
    await first.execute("DROP TABLE IF EXISTS screenplay_conversation_events")
    await first.execute("DROP TABLE IF EXISTS screenplay_conversation_turns")
    await first.execute("""CREATE TABLE screenplay_conversation_turns (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL,
        session_id INTEGER NOT NULL,
        status TEXT NOT NULL
    )""")
    await first.execute("""CREATE TABLE screenplay_conversation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        turn_id TEXT NOT NULL
    )""")
    await first.execute("""CREATE TABLE screenplay_operations (
        id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL
    )""")
    await first.execute("""CREATE TABLE screenplay_operation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT NOT NULL
    )""")
    await first.execute(
        "INSERT INTO screenplay_conversation_turns "
        "(id, project_id, session_id, status) "
        "VALUES ('discarded-turn', 'project', 1, 'completed')"
    )
    await first.execute(
        "INSERT INTO screenplay_projects (id, title, source_snapshot_json) "
        "VALUES ('kept-native-project', '保留项目', '{}')"
    )
    await first.execute(
        "INSERT INTO screenplay_operations (id, project_id) "
        "VALUES ('discarded-operation', 'kept-native-project')"
    )
    await first.execute(
        "INSERT INTO screenplay_command_receipts "
        "(command_id, command_type, project_id, request_digest, result_ref) "
        "VALUES ('discarded-turn-command', 'submitConversationTurn', "
        "'kept-native-project', 'digest', "
        "'screenplay-conversation-turn://discarded-turn')"
    )
    await first.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        assert await reopened.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'screenplay_conversation_turns'"
        ) is None
        assert await reopened.fetch_one(
            "SELECT title FROM screenplay_projects "
            "WHERE id = 'kept-native-project'"
        ) == {"title": "保留项目"}
        assert await reopened.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_command_receipts "
            "WHERE command_type = 'submitConversationTurn'"
        ) == {"count": 0}
        assert await reopened.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('screenplay_operations', 'screenplay_operation_events')"
        ) is None
    finally:
        await reopened.close()


async def test_startup_retires_revision_operation_ownership_without_data_loss(
    tmp_path: Path,
):
    first = DatabaseConnection(tmp_path)
    await first.init()
    await first.execute(
        "INSERT INTO screenplay_projects (id, title, source_snapshot_json) "
        "VALUES ('revision-migration-project', '保留版本', '{}')"
    )
    await first.execute(
        "ALTER TABLE screenplay_revisions ADD COLUMN operation_id TEXT"
    )
    await first.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "summary_json, created_by, operation_id) VALUES "
        "('kept-revision', 'revision-migration-project', "
        "'spdel:revision-migration-project:creativeBrief', 1, 'digest', '{}', "
        "'agent', 'discarded-owner')"
    )
    await first.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        columns = {
            str(column["name"])
            for column in await reopened.fetch_all(
                "PRAGMA table_info(screenplay_revisions)"
            )
        }
        assert "operation_id" not in columns
        assert "agent_task_id" in columns
        assert await reopened.fetch_one(
            "SELECT id, content_digest FROM screenplay_revisions "
            "WHERE id = 'kept-revision'"
        ) == {"id": "kept-revision", "content_digest": "digest"}
    finally:
        await reopened.close()


async def test_startup_retires_legacy_screenplay_store_without_touching_writing_data(
    tmp_path: Path,
):
    first = DatabaseConnection(tmp_path)
    await first.init()
    await first.execute(
        "INSERT INTO books (id, title) VALUES ('kept-book', '保留的书')"
    )
    await first.execute(
        "INSERT INTO outlines (id, title, book_id) "
        "VALUES ('kept-outline', '保留的大纲', 'kept-book')"
    )
    await first.execute(
        "INSERT INTO articles (chapter_id, content) "
        "VALUES ('kept-chapter', '保留的正文')"
    )
    await first.execute(
        "INSERT INTO screenplay_projects (id, title) "
        "VALUES ('legacy-project', '旧剧本')"
    )
    await first.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_snapshot_json) "
        "VALUES ('native-project', '原生剧本', '{}')"
    )
    for table in (
        "screenplay_document_source_refs",
        "screenplay_source_refs",
        "screenplay_draft_episodes",
        "screenplay_document_episodes",
        "screenplay_documents",
        "screenplay_legacy_revision_links",
        "screenplay_migration_reports",
    ):
        await first.execute(f"CREATE TABLE {table} (id TEXT)")
    await first.execute(
        "ALTER TABLE ai_conversations ADD COLUMN screenplay_proposal TEXT"
    )
    await first.execute(
        "ALTER TABLE ai_conversations ADD COLUMN screenplay_revision_ref TEXT"
    )
    for table in (
        "ai_agent_runs",
        "ai_agent_work_items",
        "ai_agent_long_tasks",
        "ai_agent_artifacts",
    ):
        await first.execute(
            f"ALTER TABLE {table} ADD COLUMN operation_id TEXT"
        )
        await first.execute(
            f"CREATE INDEX idx_{table}_operation ON {table}(operation_id)"
        )
    await first.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        assert await reopened.fetch_one(
            "SELECT id FROM screenplay_projects WHERE id = 'legacy-project'"
        ) is None
        assert await reopened.fetch_one(
            "SELECT id FROM screenplay_projects WHERE id = 'native-project'"
        ) == {"id": "native-project"}
        assert await reopened.fetch_one(
            "SELECT COUNT(*) AS count FROM books WHERE id = 'kept-book'"
        ) == {"count": 1}
        assert await reopened.fetch_one(
            "SELECT COUNT(*) AS count FROM outlines WHERE id = 'kept-outline'"
        ) == {"count": 1}
        assert await reopened.fetch_one(
            "SELECT COUNT(*) AS count FROM articles "
            "WHERE chapter_id = 'kept-chapter'"
        ) == {"count": 1}

        retired_tables = await reopened.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "('screenplay_document_source_refs', 'screenplay_source_refs', "
            "'screenplay_draft_episodes', 'screenplay_document_episodes', "
            "'screenplay_documents', 'screenplay_legacy_revision_links', "
            "'screenplay_migration_reports')"
        )
        assert retired_tables == []
        conversation_columns = await reopened.fetch_all(
            "PRAGMA table_info(ai_conversations)"
        )
        assert "screenplay_proposal" not in {
            column["name"] for column in conversation_columns
        }
        assert "screenplay_revision_ref" not in {
            column["name"] for column in conversation_columns
        }
        for table in (
            "ai_agent_runs",
            "ai_agent_work_items",
            "ai_agent_long_tasks",
            "ai_agent_artifacts",
        ):
            assert "operation_id" not in {
                column["name"]
                for column in await reopened.fetch_all(
                    f"PRAGMA table_info({table})"
                )
            }
        native_tables = {
            row["name"]
            for row in await reopened.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "screenplay_agent_turns",
            "screenplay_agent_events",
            "screenplay_agent_chunks",
            "screenplay_agent_task_outputs",
        }.issubset(native_tables)
        assert "screenplay_agent_jobs" not in native_tables
        assert "screenplay_agent_job_steps" not in native_tables
        assert "screenplay_conversation_turns" not in native_tables
    finally:
        await reopened.close()


async def test_startup_adds_review_adjudication_schema_and_marks_legacy_completion(
    tmp_path: Path,
):
    first = DatabaseConnection(tmp_path)
    await first.init()
    await first.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json, active_stage) "
        "VALUES ('legacy-completed-project', '旧流程成片', 'original', '{}', 'completed')"
    )
    await first.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        tables = {
            str(row["name"])
            for row in await reopened.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "screenplay_review_decisions",
            "screenplay_review_decision_events",
            "screenplay_finalization_events",
        }.issubset(tables)
        assert await reopened.fetch_one(
            "SELECT completion_source FROM screenplay_projects "
            "WHERE id = 'legacy-completed-project'"
        ) == {"completion_source": "legacyAgentVerdict"}
    finally:
        await reopened.close()
