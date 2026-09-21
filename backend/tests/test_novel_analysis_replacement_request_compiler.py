from __future__ import annotations

import pytest
import pytest_asyncio

from agents.novel_analysis.request_compiler import (
    NovelAnalysisRequestCompilationError,
    compile_novel_analysis_request_scope,
    split_analysis_source_text,
)
from database.connection import DatabaseConnection


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '测试小说', 'text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, 5000, 5000)"
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) VALUES "
        "('section-1', 'revision-1', 0, '第一章', ?, 'digest-1'), "
        "('section-2', 'revision-1', 1, '第二章', ?, 'digest-2')",
        ["第一段。" * 700, "第二段。" * 20],
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_compiler_freezes_order_digest_and_complete_ranges(temp_db) -> None:
    scope = await compile_novel_analysis_request_scope(
        temp_db,
        source_revision_id="revision-1",
        command_id="command-1",
        segment_token_budget=256,
    )

    first_section = [
        item for item in scope.segments if item.section_id == "section-1"
    ]
    assert len(first_section) > 1
    assert first_section[0].start_character == 0
    assert first_section[-1].end_character == len("第一段。" * 700)
    assert all(
        left.end_character == right.start_character
        for left, right in zip(first_section, first_section[1:])
    )
    assert {item.section_digest for item in first_section} == {"digest-1"}
    assert scope.section_ids == ("section-1", "section-2")


def test_splitter_rejects_implicit_tiny_budget() -> None:
    with pytest.raises(NovelAnalysisRequestCompilationError, match="at least 256"):
        split_analysis_source_text("正文", 10)


@pytest.mark.asyncio
async def test_compiler_rejects_missing_or_empty_revision(temp_db) -> None:
    with pytest.raises(NovelAnalysisRequestCompilationError, match="does not exist"):
        await compile_novel_analysis_request_scope(
            temp_db,
            source_revision_id="missing",
            command_id="command-1",
            segment_token_budget=256,
        )
    await temp_db.execute(
        "DELETE FROM novel_source_sections WHERE revision_id = 'revision-1'"
    )
    with pytest.raises(NovelAnalysisRequestCompilationError, match="no sections"):
        await compile_novel_analysis_request_scope(
            temp_db,
            source_revision_id="revision-1",
            command_id="command-1",
            segment_token_budget=256,
        )
