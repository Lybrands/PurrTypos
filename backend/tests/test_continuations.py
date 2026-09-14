from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.continuation_service import ContinuationService
from application.agent_composition import (
    clear_agent_composition,
    set_agent_composition,
)
from application.continuation_context import ContinuationContextService
from application.novel_source_service import NovelSourceService
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from exceptions import AppError, NotFoundError
from routers.books import delete_book, get_books
from schemas.continuations import CreateContinuationRequest


def test_retired_allow_without_techniques_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateContinuationRequest.model_validate({
            "title": "续写",
            "sourceRevisionId": "revision-1",
            "sourceAnalysisId": "analysis-1",
            "forkSectionId": "section-1",
            "expectedSnapshotDigest": "sha256:" + "0" * 64,
            "operationId": "operation-1",
            "allowWithoutTechniques": True,
        })


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    set_db(connection)
    composition = SimpleNamespace(memory_resource=None)
    set_agent_composition(composition)
    try:
        yield connection
    finally:
        clear_agent_composition(composition)
        clear_db()
        await connection.close()


async def _published_analysis(db):
    source_service = NovelSourceService(db)
    content = "# 第一章\n甲打开红门。\n\n# 第二章\n乙拿走钥匙。"
    preview = source_service.preview_external_import(
        file_name="原作.md", extension=".md", content=content
    )
    revision = await source_service.confirm_external_import(
        title="原作",
        file_name="原作.md",
        extension=".md",
        content=content,
        expected_content_digest=preview["contentDigest"],
        confirm_single_section=False,
        rights_confirmed=True,
        model_data_boundary_confirmed=True,
    )
    first, second = revision["sections"]
    await db.execute(
        "INSERT INTO novel_source_analyses "
        "(id, source_revision_id, version_no, coverage_end_ordinal, "
        "schema_version, content_digest, summary_json) "
        "VALUES ('analysis-1', ?, 1, 1, 1, 'analysis-digest', '{}')",
        [revision["id"]],
    )
    facts = (
        ("fact-before", "event", "甲", "opened", '"红门"', 0, 0),
        ("fact-after", "event", "乙", "took", '"钥匙"', 1, 1),
        ("fact-style", "style", "narration", "tone", '"冷峻"', 0, 0),
    )
    for fact in facts:
        await db.execute(
            "INSERT INTO novel_source_analysis_facts "
            "(id, analysis_id, fact_kind, subject_key, predicate, value_json, "
            "lifecycle_status, first_section_ordinal, last_section_ordinal, "
            "content_digest) VALUES (?, 'analysis-1', ?, ?, ?, ?, 'active', ?, ?, ?)",
            [*fact, f"digest-{fact[0]}"],
        )
    await db.execute(
        "INSERT INTO novel_source_analysis_evidence "
        "(id, analysis_id, owner_type, owner_id, section_id, excerpt, "
        "locator_json, excerpt_digest) "
        "VALUES ('evidence-before', 'analysis-1', 'fact', 'fact-before', ?, "
        "'甲打开红门。', '{}', 'excerpt-before')",
        [first["id"]],
    )
    await db.execute(
        "INSERT INTO novel_source_analysis_evidence "
        "(id, analysis_id, owner_type, owner_id, section_id, excerpt, "
        "locator_json, excerpt_digest) "
        "VALUES ('evidence-after', 'analysis-1', 'fact', 'fact-after', ?, "
        "'乙拿走钥匙。', '{}', 'excerpt-after')",
        [second["id"]],
    )
    await db.execute(
        "INSERT INTO novel_source_analysis_evidence "
        "(id, analysis_id, owner_type, owner_id, section_id, excerpt, "
        "locator_json, excerpt_digest) "
        "VALUES ('evidence-style', 'analysis-1', 'fact', 'fact-style', ?, "
        "'甲打开红门。', '{}', 'excerpt-style')",
        [first["id"]],
    )
    return revision


async def test_legacy_books_schema_gets_creation_mode_without_masking_migration(tmp_path):
    path = tmp_path / "legacy"
    path.mkdir()
    legacy = sqlite3.connect(path / "purrtypos.db")
    legacy.execute(
        "CREATE TABLE books (id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL)"
    )
    legacy.execute("INSERT INTO books (id, title) VALUES ('legacy-1', '旧作品')")
    legacy.commit()
    legacy.close()
    connection = DatabaseConnection(path)
    await connection.init()
    try:
        assert await connection.fetch_one(
            "SELECT creation_mode FROM books WHERE id = 'legacy-1'"
        ) == {"creation_mode": "original"}
    finally:
        await connection.close()


async def test_canon_preview_uses_only_hard_facts_at_or_before_chapter_end(db):
    revision = await _published_analysis(db)
    first = revision["sections"][0]
    preview = await ContinuationService(db).preview_canon(
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=first["id"],
    )
    assert preview["forkOrdinal"] == 0
    assert preview["forkSectionTitle"] == "第一章"
    assert [item["sourceFactId"] for item in preview["records"]] == [
        "fact-before"
    ]
    assert preview["snapshotDigest"].startswith("sha256:")

    with pytest.raises(AppError, match="完整章节末尾"):
        await ContinuationService(db).preview_canon(
            source_revision_id=revision["id"],
            source_analysis_id="analysis-1",
            fork_section_id="not-a-section",
        )


async def test_atomic_continuation_create_freezes_snapshot_and_exact_method_revision(db):
    revision = await _published_analysis(db)
    service = ContinuationService(db)
    preview = await service.preview_canon(
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
    )
    created = await service.create_continuation(
        title="红门之后",
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
        operation_id=__import__("uuid").uuid4().hex,
        expected_snapshot_digest=preview["snapshotDigest"],
    )
    book_id = created["book"]["id"]
    assert created["book"]["creation_mode"] == "continuation"
    assert created["binding"]["sourceRevisionId"] == revision["id"]
    assert created["binding"]["sourceAnalysisId"] == "analysis-1"
    assert len(created["canonRecords"]) == 1
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM outlines WHERE book_id = ? AND type = 'writing'",
        [book_id],
    ) == {"count": 1}
    from application.writing_technique_service import WritingTechniqueService
    assert await WritingTechniqueService(db).get_mode("book", book_id) == "manual"
    assert not await db.fetch_all("SELECT id FROM writing_technique_grants WHERE book_id=?", [book_id])

    rows = (await get_books())["data"]
    continuation = next(row for row in rows if row["id"] == book_id)
    assert continuation["continuation_source_title"] == "原作"
    assert continuation["continuation_fork_section_title"] == "第一章"


async def test_creation_failure_rolls_back_book_snapshot_binding_and_outline(db, monkeypatch):
    revision = await _published_analysis(db)
    service = ContinuationService(db)
    preview = await service.preview_canon(
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
    )
    before = {
        table: (await db.fetch_one(f"SELECT COUNT(*) AS count FROM {table}"))["count"]
        for table in (
            "books",
            "outlines",
            "continuation_canon_snapshots",
            "continuation_bindings",
        )
    }
    execute = db.execute
    async def fail_binding(sql, parameters=()):
        if "INSERT INTO continuation_bindings" in sql:
            raise RuntimeError("injected binding failure")
        return await execute(sql, parameters)
    monkeypatch.setattr(db, "execute", fail_binding)
    with pytest.raises(RuntimeError, match="injected binding failure"):
        await service.create_continuation(
            title="应回滚",
            source_revision_id=revision["id"],
            source_analysis_id="analysis-1",
            fork_section_id=revision["sections"][0]["id"],
            operation_id=__import__("uuid").uuid4().hex,
        expected_snapshot_digest=preview["snapshotDigest"],
        )
    after = {
        table: (await db.fetch_one(f"SELECT COUNT(*) AS count FROM {table}"))["count"]
        for table in before
    }
    assert after == before


async def test_source_update_does_not_change_existing_binding_and_delete_cleans_target(db):
    revision = await _published_analysis(db)
    service = ContinuationService(db)
    preview = await service.preview_canon(
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
    )
    created = await service.create_continuation(
        title="冻结的续写",
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
        operation_id=__import__("uuid").uuid4().hex,
        expected_snapshot_digest=preview["snapshotDigest"],
    )
    book_id = created["book"]["id"]
    snapshot_id = created["binding"]["canonSnapshotId"]

    source_service = NovelSourceService(db)
    changed = source_service.preview_external_import(
        file_name="原作.md", extension=".md", content="# 新版\n不同正文"
    )
    revision2 = await source_service.confirm_external_import(
        title="原作",
        file_name="原作.md",
        extension=".md",
        content="# 新版\n不同正文",
        expected_content_digest=changed["contentDigest"],
        confirm_single_section=True,
        rights_confirmed=True,
        model_data_boundary_confirmed=True,
        work_id=revision["work_id"],
    )
    persisted = await service.get_continuation(book_id)
    assert persisted["binding"]["sourceRevisionId"] == revision["id"]
    assert revision2["id"] != revision["id"]

    await delete_book(book_id)
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM continuation_bindings WHERE target_book_id = ?",
        [book_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM continuation_canon_snapshots WHERE id = ?",
        [snapshot_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_revisions WHERE id = ?",
        [revision["id"]],
    ) == {"count": 1}


async def test_source_delete_preserves_frozen_continuation_but_removes_source_archive(db):
    revision = await _published_analysis(db)
    service = ContinuationService(db)
    preview = await service.preview_canon(
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
    )
    created = await service.create_continuation(
        title="删除来源后仍可续写",
        source_revision_id=revision["id"],
        source_analysis_id="analysis-1",
        fork_section_id=revision["sections"][0]["id"],
        operation_id=__import__("uuid").uuid4().hex,
        expected_snapshot_digest=preview["snapshotDigest"],
    )
    book_id = created["book"]["id"]

    await NovelSourceService(db).delete_work(revision["work_id"])

    persisted = await service.get_continuation(book_id)
    writing_context = await ContinuationContextService(db).load_for_writing(book_id)
    assert persisted["binding"]["sourceTitle"] == "原作"
    assert persisted["binding"]["forkSectionTitle"] == "第一章"
    assert len(persisted["canonRecords"]) == 1
    assert writing_context["binding"]["sourceRevisionId"] == revision["id"]
    assert len(writing_context["canonRecords"]) == 1
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM continuation_bindings WHERE target_book_id = ?",
        [book_id],
    ) == {"count": 1}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_works WHERE id = ?",
        [revision["work_id"]],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_analyses WHERE id = 'analysis-1'"
    ) == {"count": 0}
    historical = await ContinuationContextService(db).read_source_section(
        book_id=book_id, section_id=revision["sections"][0]["id"])
    assert "甲打开红门" in historical["text"]
