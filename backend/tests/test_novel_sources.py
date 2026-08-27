from __future__ import annotations

import json

import pytest

from application.novel_source_service import NovelSourceService
from database.connection import DatabaseConnection
from domains.novel_sources import NovelSourceConflictError, parse_source_sections


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


def test_parser_is_deterministic_and_keeps_single_section_fallback():
    markdown = "前言\n\n# 第一章 起点\n正文 A\n\n第二章 变化\n正文 B"
    first = parse_source_sections(markdown)
    second = parse_source_sections(markdown)
    assert first == second
    assert [item.title for item in first] == ["前言", "第一章 起点", "第二章 变化"]
    assert "正文 B" in first[-1].text
    assert parse_source_sections("没有章节标题的全文")[0].title == "全文"


async def test_preview_writes_nothing_and_confirmation_creates_one_immutable_revision(db):
    service = NovelSourceService(db)
    content = "# 第一章\n正文一\n\n# 第二章\n正文二"
    preview = service.preview_external_import(
        file_name="原作.md", extension=".md", content=content
    )
    assert preview["sectionCount"] == 2
    assert preview["byteCount"] == len(content.encode("utf-8"))
    assert "外部模型" in preview["modelDataBoundaryNotice"]
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_revisions"
    ) == {"count": 0}

    revision = await service.confirm_external_import(
        title="原作",
        file_name="原作.md",
        extension=".md",
        content=content,
        expected_content_digest=preview["contentDigest"],
        confirm_single_section=False,
        rights_confirmed=True,
        model_data_boundary_confirmed=True,
    )
    assert revision["version_no"] == 1
    assert [item["title"] for item in revision["sections"]] == ["第一章", "第二章"]
    assert all("text_content" not in item for item in revision["sections"])

    section = await service.get_section(revision["id"], revision["sections"][1]["id"])
    assert section["text_content"].endswith("正文二")
    matches = await service.search_sections(revision["id"], "正文二")
    assert matches[0]["id"] == section["id"]


async def test_single_section_and_changed_content_require_explicit_reconfirmation(db):
    service = NovelSourceService(db)
    preview = service.preview_external_import(
        file_name="source.txt", extension=".txt", content="完整单节正文"
    )
    with pytest.raises(NovelSourceConflictError, match="按单节"):
        await service.confirm_external_import(
            title="单节", file_name="source.txt", extension=".txt",
            content="完整单节正文", expected_content_digest=preview["contentDigest"],
            confirm_single_section=False, rights_confirmed=True,
            model_data_boundary_confirmed=True,
        )
    with pytest.raises(NovelSourceConflictError, match="已变化"):
        await service.confirm_external_import(
            title="单节", file_name="source.txt", extension=".txt",
            content="已经变化", expected_content_digest=preview["contentDigest"],
            confirm_single_section=True, rights_confirmed=True,
            model_data_boundary_confirmed=True,
        )


async def test_explicit_reimport_creates_version_two_without_automatic_snapshots(db):
    service = NovelSourceService(db)
    first = service.preview_external_import(
        file_name="source.txt", extension=".txt", content="第一版"
    )
    revision1 = await service.confirm_external_import(
        title="来源", file_name="source.txt", extension=".txt", content="第一版",
        expected_content_digest=first["contentDigest"], confirm_single_section=True,
        rights_confirmed=True, model_data_boundary_confirmed=True,
    )
    second = service.preview_external_import(
        file_name="source.txt", extension=".txt", content="第二版"
    )
    revision2 = await service.confirm_external_import(
        title="来源", file_name="source.txt", extension=".txt", content="第二版",
        expected_content_digest=second["contentDigest"], confirm_single_section=True,
        rights_confirmed=True, model_data_boundary_confirmed=True,
        work_id=revision1["work_id"],
    )
    assert (revision1["version_no"], revision2["version_no"]) == (1, 2)
    assert len((await service.get_work(revision1["work_id"]))["revisions"]) == 2


async def test_book_freeze_is_complete_and_survives_origin_deletion(db):
    await db.execute("INSERT INTO books (id, title) VALUES ('book-1', '原作书')")
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing-1', '写作目录', 'writing', 'book-1')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-1', 'writing-1', '第一章', 1)"
    )
    lexical = json.dumps({
        "root": {"type": "root", "children": [{
            "type": "paragraph", "children": [{"type": "text", "text": "冻结正文"}]
        }]}
    }, ensure_ascii=False)
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES ('chapter-1', ?)", [lexical]
    )

    revision = await NovelSourceService(db).freeze_book("book-1")
    await db.execute("DELETE FROM articles WHERE chapter_id = 'chapter-1'")
    await db.execute("DELETE FROM outline_chapters WHERE id = 'chapter-1'")
    await db.execute("DELETE FROM outlines WHERE id = 'writing-1'")
    await db.execute("DELETE FROM books WHERE id = 'book-1'")

    section = await NovelSourceService(db).get_section(
        revision["id"], revision["sections"][0]["id"]
    )
    assert section["text_content"] == "冻结正文"


async def test_referenced_revision_is_delete_protected(db):
    service = NovelSourceService(db)
    preview = service.preview_external_import(
        file_name="source.txt", extension=".txt", content="原文"
    )
    revision = await service.confirm_external_import(
        title="来源", file_name="source.txt", extension=".txt", content="原文",
        expected_content_digest=preview["contentDigest"], confirm_single_section=True,
        rights_confirmed=True, model_data_boundary_confirmed=True,
    )
    await db.execute(
        "INSERT INTO books (id, title, creation_mode) VALUES ('target', '续写', 'continuation')"
    )
    await db.execute(
        "INSERT INTO continuation_bindings "
        "(id, target_book_id, source_work_id, source_revision_id, fork_section_id, "
        "fork_ordinal, canon_snapshot_id, binding_digest) VALUES "
        "('binding', 'target', ?, ?, ?, 0, 'snapshot', 'digest')",
        [revision["work_id"], revision["id"], revision["sections"][0]["id"]],
    )
    with pytest.raises(NovelSourceConflictError, match="不能删除"):
        await service.delete_revision(revision["id"])
    with pytest.raises(NovelSourceConflictError, match="不能删除"):
        await service.delete_work(revision["work_id"])


async def test_unused_source_work_can_be_deleted_with_all_revisions(db):
    service = NovelSourceService(db)
    first = service.preview_external_import(
        file_name="delete.md", extension=".md", content="# 第一章\n第一版"
    )
    revision1 = await service.confirm_external_import(
        title="待删除来源", file_name="delete.md", extension=".md",
        content="# 第一章\n第一版", expected_content_digest=first["contentDigest"],
        confirm_single_section=True, rights_confirmed=True,
        model_data_boundary_confirmed=True,
    )
    second = service.preview_external_import(
        file_name="delete.md", extension=".md", content="# 第一章\n第二版"
    )
    await service.confirm_external_import(
        title="待删除来源", file_name="delete.md", extension=".md",
        content="# 第一章\n第二版", expected_content_digest=second["contentDigest"],
        confirm_single_section=True, rights_confirmed=True,
        model_data_boundary_confirmed=True, work_id=revision1["work_id"],
    )

    await service.delete_work(revision1["work_id"])

    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_works WHERE id = ?",
        [revision1["work_id"]],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_revisions WHERE work_id = ?",
        [revision1["work_id"]],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_sections"
    ) == {"count": 0}


async def test_historical_books_are_idempotently_original(db):
    await db.execute("INSERT INTO books (id, title) VALUES ('legacy', '历史作品')")
    row = await db.fetch_one("SELECT creation_mode FROM books WHERE id = 'legacy'")
    assert row == {"creation_mode": "original"}


async def test_source_http_contract_previews_then_confirms_without_model_call(db):
    from dependencies import clear_db, set_db
    from routers.novel_sources import confirm_import, preview_import
    from schemas.novel_sources import ConfirmSourceImportRequest, SourceFilePayload

    set_db(db)
    try:
        preview_response = await preview_import(SourceFilePayload(
            fileName="wire.md", extension=".md", content="# 第一章\n接口正文",
            importKind="folder", documentCount=2, skippedFileCount=1,
        ))
        preview = preview_response["data"]
        confirmed = await confirm_import(ConfirmSourceImportRequest(
            fileName="wire.md",
            extension=".md",
            content="# 第一章\n接口正文",
            importKind="folder",
            documentCount=2,
            skippedFileCount=1,
            title="接口来源",
            expectedContentDigest=preview["contentDigest"],
            confirmSingleSection=True,
            rightsConfirmed=True,
            modelDataBoundaryConfirmed=True,
        ))
    finally:
        clear_db(db)

    assert confirmed["data"]["content_digest"] == preview["contentDigest"]
    assert confirmed["data"]["sections"][0]["title"] == "第一章"
    assert preview["documentCount"] == 2
    assert confirmed["data"]["source_metadata"]["importKind"] == "folder"
    assert confirmed["data"]["source_metadata"]["skippedFileCount"] == 1


async def test_source_revision_recovers_after_database_restart(tmp_path):
    first_db = DatabaseConnection(tmp_path)
    await first_db.init()
    service = NovelSourceService(first_db)
    preview = service.preview_external_import(
        file_name="restart.txt", extension=".txt", content="重启后仍存在的正文"
    )
    revision = await service.confirm_external_import(
        title="重启来源", file_name="restart.txt", extension=".txt",
        content="重启后仍存在的正文",
        expected_content_digest=preview["contentDigest"],
        confirm_single_section=True, rights_confirmed=True,
        model_data_boundary_confirmed=True,
    )
    await first_db.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        recovered = await NovelSourceService(reopened).get_section(
            revision["id"], revision["sections"][0]["id"]
        )
        assert recovered["text_content"] == "重启后仍存在的正文"
    finally:
        await reopened.close()
