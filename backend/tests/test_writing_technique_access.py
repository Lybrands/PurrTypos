import pytest

from application.writing_technique_access import WritingTechniqueAccess
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from domains.writing.techniques import TechniqueError


@pytest.fixture
async def access(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books(id,title) VALUES ('book','测试小说')")
    try:
        yield WritingTechniqueAccess(db)
    finally:
        await db.close()


async def publish(access, op, existing=None):
    service = access.library
    draft = await service.create_draft(operation_id=op, technique_id=existing)
    draft = await service.apply_changes(draft["techniqueId"], draft["draftId"], expected_revision=0, operation_id="write",
        changes=[{"action": "put", "path": "SKILL.md", "content": f"---\nname: {op}\ndescription: 用于有误会的对白场景\n---\n先展示动作，再给出判断。[细节](细节.md)"},
                 {"action": "put", "path": "细节.md", "content": "按人物可知范围安排信息。"}])
    draft = await service.seal("technique", draft["techniqueId"], draft["draftId"], expected_revision=1, expected_tree_digest=draft["treeDigest"], operation_id="seal")
    record = await service.get_object("technique", draft["techniqueId"])
    await service.publish("technique", record["id"], ref=draft["sealedRef"], expected_published_head=record["publishedHead"], operation_id=op + ":publish")
    return draft["sealedRef"]


async def test_manual_ignores_long_term_grants_and_requires_explicit_selection(access):
    ref = await publish(access, "manual")
    await access.grant("book", ref)
    snapshot = await access.freeze(book_id="book")
    assert snapshot["mode"] == "manual" and not snapshot["manual"] and not snapshot["candidates"]
    with pytest.raises(TechniqueError):
        await access.read(snapshot, ref, "SKILL.md", max_characters=9999)
    manual = await access.freeze(book_id="book", manual=[ref])
    assert (await access.read(manual, ref, "SKILL.md", max_characters=9999))["content"]
    with pytest.raises(TechniqueError, match="先读取"):
        await access.read(manual, ref, "细节.md", max_characters=9999)
    assert (await access.read(manual, ref, "细节.md", entry_refs=[ref], max_characters=9999))["content"]
    with pytest.raises(TechniqueError) as error:
        await access.read(manual, ref, "SKILL.md", max_characters=1)
    assert error.value.code == "file_exceeds_budget"


async def test_automatic_scope_revoke_regrant_does_not_revive_old_run(access):
    ref = await publish(access, "authorized")
    await publish(access, "private")
    grant = await access.grant("book", ref)
    snapshot = await access.freeze(book_id="book", mode="auto")
    assert [c["ref"] for c in snapshot["candidates"]] == [ref]
    selected = access.resolve(snapshot, [ref])["selected"]
    await access.validate(snapshot, selected=selected)
    await access.revoke("book", grant["id"])
    await access.grant("book", ref)
    with pytest.raises(TechniqueError) as error:
        await access.validate(snapshot, selected=selected)
    assert error.value.code == "authorization_revoked"


async def test_manual_conflicts_and_atomic_automatic_scheme_skip(access):
    first = await publish(access, "first")
    second = await publish(access, "second", existing=first["id"])
    with pytest.raises(TechniqueError, match="版本冲突"):
        await access.freeze(book_id="book", manual=[first, second])
    other = await publish(access, "other")
    content = {"schemaVersion": 1, "name": "方案", "description": "信息分配", "composition": "", "members": [second, other]}
    draft = await access.library.create_draft(kind="scheme", content=content, operation_id="scheme")
    sealed = await access.library.seal("scheme", draft["schemeId"], draft["draftId"], expected_revision=0, expected_tree_digest=draft["treeDigest"], operation_id="seal")
    await access.library.publish("scheme", draft["schemeId"], ref=sealed["sealedRef"], expected_published_head=None, operation_id="publish")
    await access.grant("book", sealed["sealedRef"])
    snapshot = await access.freeze(book_id="book", mode="auto", manual=[first])
    assert len(snapshot["candidates"]) == 1
    resolution = access.resolve(snapshot, [sealed["sealedRef"]])
    assert [m["ref"] for m in resolution["members"]] == [first]
    assert resolution["skipped"][0]["ref"] == sealed["sealedRef"]


async def test_archive_and_reenable_invalidates_manual_snapshot(access):
    ref = await publish(access, "archive")
    snapshot = await access.freeze(book_id="book", manual=[ref])
    await access.library.set_status("technique", ref["id"], "archived", operation_id="archive-status")
    await access.library.set_status("technique", ref["id"], "active", operation_id="enable")
    with pytest.raises(TechniqueError) as error:
        await access.validate(snapshot)
    assert error.value.code == "authorization_revoked"
