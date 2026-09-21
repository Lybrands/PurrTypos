from __future__ import annotations

from hashlib import sha256

import pytest

from agents.novel_analysis.source_slicing import (
    SliceSourceSection,
    SourceTokenizer,
    compile_persisted_source_slice_manifest,
    compile_source_slice_manifest,
)
from application.novel_source_service import NovelSourceService
from database.connection import DatabaseConnection
from database.continuation_schema import init_continuation_schema


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


def _tokenizer() -> SourceTokenizer:
    return SourceTokenizer(
        id="test.characters",
        version="1",
        count_kind="exact",
        count=len,
        safety_basis_points=10_000,
    )


def _section(ordinal: int, text: str) -> SliceSourceSection:
    return SliceSourceSection(
        id=f"section-{ordinal}",
        ordinal=ordinal,
        title=f"第 {ordinal + 1} 章",
        text=text,
        content_digest=sha256(text.encode()).hexdigest(),
        byte_count=len(text.encode("utf-8")),
        character_count=len(text),
        token_count=len(text),
    )


def test_slice_compiler_uses_forty_percent_and_keeps_fitting_chapters_whole():
    manifest = compile_source_slice_manifest(
        source_revision_id="revision",
        source_revision_digest="digest",
        context_window_tokens=1_000,
        sections=(
            _section(0, "甲" * 80),
            _section(1, "乙" * 110),
            _section(2, "丙" * 150),
            _section(3, "丁" * 120),
        ),
        tokenizer=_tokenizer(),
    )

    assert manifest.source_token_limit == 400
    assert manifest.packing_token_limit == 400
    assert [item.token_count for item in manifest.slices] == [340, 120]
    assert [
        [source_range.section_ordinal for source_range in item.ranges]
        for item in manifest.slices
    ] == [[0, 1, 2], [3]]


def test_slice_compiler_only_splits_a_chapter_that_exceeds_the_limit():
    oversized = "第一段。\n\n" + "中" * 500 + "。\n\n最后一段。"
    sections = (_section(0, "前章" * 60), _section(1, oversized))
    manifest = compile_source_slice_manifest(
        source_revision_id="revision",
        source_revision_digest="digest",
        context_window_tokens=1_000,
        sections=sections,
        tokenizer=_tokenizer(),
    )

    assert len(manifest.slices[0].ranges) == 1
    assert manifest.slices[0].ranges[0].section_id == "section-0"
    oversized_ranges = [
        source_range
        for source_slice in manifest.slices[1:]
        for source_range in source_slice.ranges
    ]
    assert len(oversized_ranges) > 1
    assert oversized_ranges[0].start_character == 0
    assert oversized_ranges[-1].end_character == len(oversized)
    assert all(
        left.end_character == right.start_character
        for left, right in zip(oversized_ranges, oversized_ranges[1:])
    )
    assert all(item.token_count <= 400 for item in manifest.slices)


def test_slice_manifest_identity_is_deterministic():
    arguments = dict(
        source_revision_id="revision",
        source_revision_digest="digest",
        context_window_tokens=1_000,
        sections=(_section(0, "正文" * 100),),
        tokenizer=_tokenizer(),
    )
    first = compile_source_slice_manifest(**arguments)
    second = compile_source_slice_manifest(**arguments)

    assert first.to_mapping() == second.to_mapping()
    assert first.slices[0].id.startswith("source-slice-")


def test_slice_identity_binds_tokenizer_version_even_when_counts_match():
    arguments = dict(
        source_revision_id="revision",
        source_revision_digest="digest",
        context_window_tokens=1_000,
        sections=(_section(0, "正文" * 100),),
    )
    first = compile_source_slice_manifest(
        **arguments,
        tokenizer=_tokenizer(),
    )
    second = compile_source_slice_manifest(
        **arguments,
        tokenizer=SourceTokenizer(
            id="test.characters",
            version="2",
            count_kind="exact",
            count=len,
            safety_basis_points=10_000,
        ),
    )

    assert first.slices[0].token_count == second.slices[0].token_count
    assert first.slices[0].id != second.slices[0].id


@pytest.mark.asyncio
async def test_persisted_slice_compiler_caches_versioned_section_metrics(db):
    content = "# 第一章\n甲乙丙\n\n# 第二章\n丁戊己"
    service = NovelSourceService(db)
    preview = service.preview_external_import(
        file_name="source.md", extension=".md", content=content
    )
    revision = await service.confirm_external_import(
        title="来源",
        file_name="source.md",
        extension=".md",
        content=content,
        expected_content_digest=preview["contentDigest"],
        confirm_single_section=False,
        rights_confirmed=True,
        model_data_boundary_confirmed=True,
    )
    tokenizer = _tokenizer()
    manifest = await compile_persisted_source_slice_manifest(
        db,
        source_revision_id=revision["id"],
        context_window_tokens=1_000,
        tokenizer=tokenizer,
    )
    cached = await db.fetch_all(
        "SELECT section_id, tokenizer_id, tokenizer_version, token_count, count_kind "
        "FROM novel_source_section_token_metrics ORDER BY section_id"
    )

    assert manifest.total_character_count == len(content)
    assert len(cached) == 2
    assert {item["tokenizer_id"] for item in cached} == {"test.characters"}
    assert {item["tokenizer_version"] for item in cached} == {"1"}
    assert {item["count_kind"] for item in cached} == {"exact"}


@pytest.mark.asyncio
async def test_schema_backfills_legacy_section_capacity_without_changing_text(db):
    content = "旧来源正文🙂"
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work', '旧来源', 'external_text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, "
        "byte_count, character_count) VALUES (?, ?, 1, ?, 1, ?, ?)",
        ["revision", "work", "revision-digest", len(content.encode()), len(content)],
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest, "
        "byte_count, character_count) VALUES (?, ?, 0, ?, ?, ?, 0, 0)",
        ["section", "revision", "全文", content, "section-digest"],
    )

    await init_continuation_schema(db)
    row = await db.fetch_one(
        "SELECT text_content, byte_count, character_count "
        "FROM novel_source_sections WHERE id = 'section'"
    )

    assert row == {
        "text_content": content,
        "byte_count": len(content.encode("utf-8")),
        "character_count": len(content),
    }
