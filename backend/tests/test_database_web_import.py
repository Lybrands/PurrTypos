from __future__ import annotations

from pathlib import Path

import pytest

from database.connection import DatabaseConnection
from application.continuation_service import ContinuationService
from agents.writing.profile import WritingReplacementProfile
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageRole,
    ModelRequest,
)
import json


@pytest.mark.asyncio
async def test_import_from_buffer_replaces_live_database_and_keeps_connection_usable(
    tmp_path: Path,
):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source = DatabaseConnection(source_dir)
    target = DatabaseConnection(target_dir)
    await source.init()
    await target.init()
    try:
        await source.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            ["source01", "导入后的书"],
        )
        payload = await source.export_to_buffer()
        assert payload

        await target.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            ["target01", "导入前的书"],
        )
        await target.import_from_buffer(payload)

        books = await target.fetch_all("SELECT id, title FROM books ORDER BY id")
        assert books == [{"id": "source01", "title": "导入后的书"}]
        assert await target.is_healthy() is True
        assert list(target_dir.glob("purrtypos.db.before-import-*.bak"))
    finally:
        await source.close()
        await target.close()


@pytest.mark.asyncio
async def test_import_from_buffer_rejects_non_purrtypos_database(tmp_path: Path):
    target = DatabaseConnection(tmp_path / "target")
    await target.init()
    try:
        with pytest.raises(ValueError, match="有效的 PurrTypos"):
            await target.import_from_buffer(b"not a sqlite database")
        assert await target.is_healthy() is True
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_database_import_preserves_source_canon_and_run_binding(tmp_path: Path):
    source = DatabaseConnection(tmp_path / "source-full")
    target = DatabaseConnection(tmp_path / "target-full")
    await source.init()
    await target.init()
    try:
        await source.execute(
            "INSERT INTO novel_source_works (id, title, source_type) "
            "VALUES ('work-1', '来源', 'external_markdown')"
        )
        await source.execute(
            "INSERT INTO novel_source_revisions "
            "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
            "VALUES ('source-rev', 'work-1', 1, 'source-digest', 1, 12, 6)"
        )
        await source.execute(
            "INSERT INTO novel_source_sections "
            "(id, revision_id, ordinal, title, text_content, content_digest) "
            "VALUES ('source-section', 'source-rev', 0, '第一章', '甲开门。', 'section-digest')"
        )
        await source.execute(
            "INSERT INTO novel_source_analyses "
            "(id, source_revision_id, version_no, coverage_end_ordinal, schema_version, content_digest) "
            "VALUES ('analysis-1', 'source-rev', 1, 0, 1, 'analysis-digest')"
        )
        await source.execute(
            "INSERT INTO novel_source_analysis_facts "
            "(id, analysis_id, fact_kind, subject_key, predicate, value_json, "
            "first_section_ordinal, last_section_ordinal, content_digest) "
            "VALUES ('fact-1', 'analysis-1', 'event', '甲', 'opened', '" + '"门"' + "', 0, 0, 'fact-digest')"
        )
        await source.execute(
            "INSERT INTO novel_source_analysis_evidence "
            "(id, analysis_id, owner_type, owner_id, section_id, excerpt, excerpt_digest) "
            "VALUES ('evidence-1', 'analysis-1', 'fact', 'fact-1', 'source-section', '甲开门。', 'excerpt-digest')"
        )
        continuation = ContinuationService(source)
        preview = await continuation.preview_canon(
            source_revision_id="source-rev",
            source_analysis_id="analysis-1",
            fork_section_id="source-section",
        )
        created = await continuation.create_continuation(
            title="门后",
            source_revision_id="source-rev",
            source_analysis_id="analysis-1",
            fork_section_id="source-section",
            expected_snapshot_digest=preview["snapshotDigest"],
            operation_id="import-fixture",
        )
        profile = WritingReplacementProfile(source)
        prepared = await profile.prepare_request(AgentRunRequest(
            messages=(AgentMessage(role=MessageRole.USER, content="续写"),),
            model=ModelRequest(provider="test", model="model"),
            domain_context=DomainContext(
                namespace="purrtypos.writing",
                payload={"book_id": created["book"]["id"]},
            ),
        ))
        binding = profile.run_binding_attributes(prepared)
        await source.execute(
            "INSERT INTO ai_agent_runs "
            "(id, status, binding_namespace, binding_aggregate_id, binding_command_id, "
            "binding_attributes_json) VALUES ('run-1', 'completed', 'writing.chat.request', ?, 'turn-1', ?)",
            [created["book"]["id"], json.dumps(binding, ensure_ascii=False)],
        )

        await target.import_from_buffer(await source.export_to_buffer())
        assert await target.fetch_one(
            "SELECT source_revision_id, canon_snapshot_id FROM continuation_bindings "
            "WHERE target_book_id = ?",
            [created["book"]["id"]],
        ) == {
            "source_revision_id": "source-rev",
            "canon_snapshot_id": created["binding"]["canonSnapshotId"],
        }
        restored = await target.fetch_one(
            "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = 'run-1'"
        )
        assert json.loads(restored["binding_attributes_json"]) == binding
        assert await target.is_healthy() is True
    finally:
        await source.close()
        await target.close()
