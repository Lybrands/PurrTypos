from __future__ import annotations

import pytest
import sqlite3

from application.writing_method_service import WritingMethodService
from database.connection import DatabaseConnection
from database.writing_method_schema import init_writing_method_schema
from dependencies import clear_db, set_db
from domains.writing.methods import (
    WritingMethodConflictError,
    WritingMethodReferenceError,
)
from infrastructure.persistence.writing.sqlite_writing_method_repository import (
    SqliteWritingMethodRepository,
)


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _create_book(db, book_id: str = "book-1") -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)", [book_id, "测试作品"]
    )


async def _create_method(service, *, name="方法 A", method_type="primary", markdown="# A"):
    return await service.create_method(
        name=name,
        description="说明",
        method_type=method_type,
        tags=["测试"],
        markdown=markdown,
        metadata={"schemaVersion": 1},
    )


async def test_schema_is_idempotent_and_builtins_are_immutable_but_copyable(db):
    await init_writing_method_schema(db)
    await init_writing_method_schema(db)
    service = WritingMethodService(db)
    methods = await service.list_methods()
    schemes = await service.list_schemes()

    builtin = next(item for item in methods if item["is_builtin"])
    assert sum(bool(item["is_builtin"]) for item in methods) == 2
    assert sum(bool(item["is_builtin"]) for item in schemes) == 1
    with pytest.raises(WritingMethodConflictError, match="不可编辑"):
        await service.update_method(
            builtin["id"],
            expected_draft_revision=builtin["draft_revision"],
            name=builtin["name"],
            description=builtin["description"],
            method_type=builtin["method_type"],
            tags=builtin["tags"],
            markdown=builtin["draft_markdown"],
            metadata=builtin["draft_metadata"],
        )

    copied = await service.copy_method(builtin["id"])
    assert copied["is_builtin"] == 0
    assert copied["current_published_revision_id"] is None
    assert copied["draft_markdown"] == builtin["draft_markdown"]


async def test_startup_drops_legacy_book_style_rows_without_migration(tmp_path):
    path = tmp_path / "purrtypos.db"
    legacy = sqlite3.connect(path)
    try:
        legacy.execute("CREATE TABLE book_style (book_id TEXT PRIMARY KEY, tone TEXT)")
        legacy.execute("INSERT INTO book_style (book_id, tone) VALUES ('old', 'legacy')")
        legacy.commit()
    finally:
        legacy.close()

    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        table = await connection.fetch_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'book_style'"
        )
        assert table is None
        assert await connection.fetch_one(
            "SELECT COUNT(*) AS count FROM writing_methods"
        ) == {"count": 2}
    finally:
        await connection.close()


async def test_draft_autosave_is_optimistic_and_publish_is_immutable(db):
    service = WritingMethodService(db)
    method = await _create_method(service)
    assert method["draft_revision"] == 0
    assert method["revisions"] == []

    updated = await service.update_method(
        method["id"],
        expected_draft_revision=0,
        name="方法 A",
        description="第二稿",
        method_type="primary",
        tags=["测试", "主风格"],
        markdown="# A\n\n第二稿",
        metadata={"schemaVersion": 1},
    )
    assert updated["draft_revision"] == 1
    assert updated["revisions"] == []
    with pytest.raises(WritingMethodConflictError, match="当前修订为 1"):
        await service.update_method(
            method["id"],
            expected_draft_revision=0,
            name="过期覆盖",
            description="",
            method_type="primary",
            tags=[],
            markdown="# stale",
            metadata={},
        )

    v1 = await service.publish_method(method["id"])
    await service.update_method(
        method["id"],
        expected_draft_revision=1,
        name="方法 A",
        description="第三稿",
        method_type="primary",
        tags=["测试"],
        markdown="# A\n\n第三稿",
        metadata={"schemaVersion": 1},
    )
    v2 = await service.publish_method(method["id"])
    assert (v1["version_no"], v2["version_no"]) == (1, 2)
    assert v1["markdown_body"] == "# A\n\n第二稿"
    assert v2["markdown_body"] == "# A\n\n第三稿"


async def test_scheme_freezes_normalized_members_and_book_binding_stays_exact(db):
    await _create_book(db)
    service = WritingMethodService(db)
    first = await _create_method(service, name="主方法")
    second = await _create_method(service, name="专项方法", method_type="technique")
    first_v1 = await service.publish_method(first["id"])
    second_v1 = await service.publish_method(second["id"])
    scheme = await service.create_scheme(
        name="组合",
        description="",
        member_revision_ids=[first_v1["id"], second_v1["id"]],
    )
    scheme_v1 = await service.publish_scheme(scheme["id"])
    assert [item["method_revision_id"] for item in scheme_v1["members"]] == [
        first_v1["id"], second_v1["id"]
    ]

    binding = await service.bind_book_revision(
        book_id="book-1", binding_type="scheme", revision_id=scheme_v1["id"]
    )
    await service.update_scheme(
        scheme["id"],
        expected_draft_revision=0,
        name="组合",
        description="新版",
        member_revision_ids=[second_v1["id"]],
    )
    scheme_v2 = await service.publish_scheme(scheme["id"])
    bindings = await service.list_book_bindings("book-1")
    assert bindings[0]["scheme_revision_id"] == scheme_v1["id"]
    assert [item["method_revision_id"] for item in bindings[0]["revision"]["members"]] == [
        first_v1["id"], second_v1["id"]
    ]
    upgraded = await service.upgrade_book_binding(
        "book-1", binding["id"], scheme_v2["id"]
    )
    assert upgraded["scheme_revision_id"] == scheme_v2["id"]


async def test_duplicate_scheme_member_and_cross_identity_upgrade_fail_closed(db):
    await _create_book(db)
    service = WritingMethodService(db)
    first = await _create_method(service, name="A")
    second = await _create_method(service, name="B")
    first_v1 = await service.publish_method(first["id"])
    second_v1 = await service.publish_method(second["id"])
    with pytest.raises(WritingMethodConflictError, match="重复"):
        await service.create_scheme(
            name="坏组合", description="",
            member_revision_ids=[first_v1["id"], first_v1["id"]],
        )
    binding = await service.bind_book_revision(
        book_id="book-1", binding_type="method", revision_id=first_v1["id"]
    )
    with pytest.raises(WritingMethodConflictError, match="同一个写作方法"):
        await service.upgrade_book_binding(
            "book-1", binding["id"], second_v1["id"]
        )


async def test_binding_order_is_complete_and_method_reference_is_protected(db):
    await _create_book(db)
    service = WritingMethodService(db)
    first = await _create_method(service, name="A")
    second = await _create_method(service, name="B")
    first_v1 = await service.publish_method(first["id"])
    second_v1 = await service.publish_method(second["id"])
    a = await service.bind_book_revision(
        book_id="book-1", binding_type="method", revision_id=first_v1["id"]
    )
    b = await service.bind_book_revision(
        book_id="book-1", binding_type="method", revision_id=second_v1["id"]
    )
    with pytest.raises(WritingMethodConflictError, match="全部"):
        await service.reorder_book_bindings("book-1", [b["id"]])
    reordered = await service.reorder_book_bindings("book-1", [b["id"], a["id"]])
    assert [item["id"] for item in reordered] == [b["id"], a["id"]]
    with pytest.raises(WritingMethodReferenceError, match="只能归档"):
        await service.delete_method(first["id"])
    await service.unbind_book_revision("book-1", a["id"])
    await service.delete_method(first["id"])


async def test_batch_publish_rolls_back_every_revision_on_failure(db):
    service = WritingMethodService(db)
    method = await _create_method(service)
    empty_scheme = await service.create_scheme(
        name="空方案", description="", member_revision_ids=[]
    )
    with pytest.raises(WritingMethodConflictError, match="至少需要"):
        await service.publish_batch(
            method_ids=[method["id"]], scheme_ids=[empty_scheme["id"]]
        )
    detail = await service.get_method(method["id"])
    assert detail["revisions"] == []
    assert detail["current_published_revision_id"] is None


async def test_recommendation_catalog_is_read_only_metadata_without_binding_side_effect(db):
    await _create_book(db)
    service = WritingMethodService(db)
    method = await _create_method(
        service,
        name="冲突升级",
        method_type="technique",
        markdown="# 私有方法正文\n只用于正式上下文注入",
    )
    revision = await service.publish_method(method["id"])

    rows = await SqliteWritingMethodRepository(db).search_published_methods("冲突", limit=4)

    matched = next(item for item in rows if item["id"] == revision["id"])
    assert "markdown_body" not in matched
    assert await service.list_book_bindings("book-1") == []


async def test_wire_api_creates_publishes_and_binds_exact_revision(db):
    from routers.writing_methods import (
        bind_book_revision,
        create_method,
        list_book_bindings,
        publish_method,
    )
    from schemas.writing_methods import (
        CreateBookWritingMethodBindingRequest,
        MethodDraftRequest,
    )

    await _create_book(db)
    set_db(db)
    try:
        created = await create_method(MethodDraftRequest(
            name="接口方法",
            methodType="primary",
            markdown="# 接口方法",
        ))
        published = await publish_method(created["data"]["id"])
        revision_id = published["data"]["id"]
        bound = await bind_book_revision(
            "book-1",
            CreateBookWritingMethodBindingRequest(
                bindingType="method", revisionId=revision_id
            ),
        )
        listed = await list_book_bindings("book-1")
    finally:
        clear_db(db)

    assert bound["data"]["method_revision_id"] == revision_id
    assert listed["data"][0]["revision"]["content_digest"] == published["data"]["content_digest"]
