from __future__ import annotations

from pathlib import Path

import pytest

from database.connection import DatabaseConnection


pytestmark = pytest.mark.asyncio


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
            "screenplay_conversation_turns",
            "screenplay_conversation_events",
        }.issubset(native_tables)
    finally:
        await reopened.close()
