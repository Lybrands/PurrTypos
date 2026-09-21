from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from agents.writing.material_write_model import (
    SqliteWritingMaterialRepository,
    WritingMaterialMutationError,
)
from agents.writing.profile import WritingReplacementProfile
from agents.writing.read_model import SqliteWritingReadRepository, WritingReadScope
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from database.connection import DatabaseConnection
from purra.contracts import (
    ApprovalStatus,
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
)
from purra.tools.approval import InMemoryApprovalGateway
from purra.tools.executor import CoreToolExecutor


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:
        self.events.append(event)


@pytest_asyncio.fixture
async def material_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books (id, title) VALUES ('book-1', '甲'), ('book-2', '乙')")
    await db.execute(
        "INSERT INTO story_background (book_id, content) VALUES ('book-1', '旧背景')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES ('global-1', '总纲', 'global', 'book-1', '旧总纲')"
    )
    await db.execute(
        "INSERT INTO characters (book_id, name, tags, profile_md) VALUES "
        "('book-1', '阿澈', '主角', '旧档案'), ('book-2', '越界人物', '', '')"
    )
    await db.execute(
        "INSERT INTO setting_entities (book_id, entity_type, name, tags, profile_md) "
        "VALUES ('book-1', 'location', '灯塔', '地标', '旧设定')"
    )
    try:
        yield db
    finally:
        await db.close()


def _state() -> ExecutionState:
    return ExecutionState(domain={
        WRITING_READ_SCOPE_STATE_KEY: {"bookId": "book-1"},
    })


@pytest.mark.asyncio
async def test_material_reads_expose_revisions_for_every_editable_target(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    scope = WritingReadScope("book-1")

    background = await reads.story_background(scope)
    outline = await reads.global_outline(scope)
    characters = await reads.characters(scope, include_profile=True)
    entities = await reads.setting_entities(scope, include_profile=True)

    assert background["background"]["baseRevision"].startswith("sha256:")
    assert outline["outline"]["baseRevision"].startswith("sha256:")
    assert characters["items"][0]["baseRevision"].startswith("sha256:")
    assert entities["items"][0]["baseRevision"].startswith("sha256:")


def test_material_update_tools_are_confirm_only_and_read_before_write(material_db) -> None:
    catalog = WritingReplacementProfile(material_db).adapter.tool_catalog
    expected = {
        "editStoryBackground": "getStoryBackground",
        "updateCharacter": "getBookCharacters",
        "updateSettingEntity": "getSettingEntities",
        "editGlobalOutline": "readWritingOutlines",
    }

    for name, prerequisite in expected.items():
        registration = catalog.get(name)
        assert registration.policy.mode.value == "confirm"
        assert registration.cancellation_linearizable is True
        assert registration.context_contract.prerequisite_tools == (prerequisite,)
        assert "baseRevision" in registration.schema.parameters["required"]


@pytest.mark.asyncio
async def test_all_material_updates_commit_with_history_and_partial_patch(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")
    background = await reads.story_background(scope)
    outline = await reads.global_outline(scope)
    character = (await reads.characters(scope, include_profile=True))["items"][0]
    entity = (await reads.setting_entities(scope, include_profile=True))["items"][0]

    await writes.commit_story_background(
        scope, content="新背景", base_revision=background["background"]["baseRevision"],
        clear_content=False,
    )
    await writes.commit_global_outline(
        scope, markdown="新总纲", base_revision=outline["outline"]["baseRevision"],
        clear_content=False,
    )
    await writes.commit_character(
        scope, character_id=character["id"], base_revision=character["baseRevision"],
        patch={"profileMd": "新档案"},
    )
    await writes.commit_setting_entity(
        scope, entity_id=entity["id"], base_revision=entity["baseRevision"],
        patch={"name": "新灯塔"},
    )

    assert await material_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "新背景"}
    assert await material_db.fetch_one(
        "SELECT markdown_content FROM outlines WHERE id = 'global-1'"
    ) == {"markdown_content": "新总纲"}
    assert await material_db.fetch_one(
        "SELECT name, tags, profile_md FROM characters WHERE id = 1"
    ) == {"name": "阿澈", "tags": "主角", "profile_md": "新档案"}
    assert await material_db.fetch_one(
        "SELECT name, entity_type FROM setting_entities WHERE id = 1"
    ) == {"name": "新灯塔", "entity_type": "location"}
    assert (await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM story_background_history"
    ))["count"] == 1
    assert (await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM outline_history"
    ))["count"] == 1
    assert (await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM character_history"
    ))["count"] == 1
    assert (await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entity_history"
    ))["count"] == 1


@pytest.mark.asyncio
async def test_stale_revision_and_cross_book_id_fail_before_effect(material_db) -> None:
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")

    with pytest.raises(WritingMaterialMutationError) as stale:
        await writes.validate_character(
            scope, character_id=1, base_revision="sha256:stale", patch={"name": "新名"}
        )
    with pytest.raises(WritingMaterialMutationError) as cross_book:
        await writes.validate_character(
            scope, character_id=2, base_revision="sha256:any", patch={"name": "新名"}
        )

    assert stale.value.code == "writing_character_revision_conflict"
    assert cross_book.value.code == "writing_character_scope_conflict"


@pytest.mark.asyncio
async def test_empty_text_and_empty_record_patch_are_rejected_before_approval(material_db) -> None:
    catalog = WritingReplacementProfile(material_db).adapter.tool_catalog
    reads = SqliteWritingReadRepository(material_db)
    scope = WritingReadScope("book-1")
    background = await reads.story_background(scope)
    character = (await reads.characters(scope, include_profile=True))["items"][0]

    background_error = await catalog.get("editStoryBackground").scope_validator(
        _state(), {"content": "", "baseRevision": background["background"]["baseRevision"]}
    )
    patch_error = await catalog.get("updateCharacter").scope_validator(
        _state(), {"characterId": character["id"], "baseRevision": character["baseRevision"]}
    )

    assert background_error == "writing_background_clear_requires_explicit_intent"
    assert patch_error == "tool_input_invalid"


@pytest.mark.asyncio
async def test_material_write_waits_for_approval_and_rejection_keeps_database(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    current = await reads.story_background(WritingReadScope("book-1"))
    approvals = InMemoryApprovalGateway()
    sink = _EventSink()
    request = ToolBatchRequest(
        run_id="run-background", allowed_tool_names=frozenset({"editStoryBackground"}),
        state=_state(), calls=(ToolCall(
            id="call-background", name="editStoryBackground",
            arguments_json=json.dumps({
                "content": "不应写入",
                "baseRevision": current["background"]["baseRevision"],
            }, ensure_ascii=False),
        ),),
    )
    task = asyncio.create_task(CoreToolExecutor(
        WritingReplacementProfile(material_db).adapter.tool_catalog,
        approval_gateway=approvals,
    ).execute_batch(request, sink))

    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    before = await material_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    )
    assert before == {"content": "旧背景"}
    assert await approvals.resolve(
        "run-background", sink.events[0].payload["approvalId"], "reject"
    ) is ApprovalStatus.REJECTED
    result = await task
    after = await material_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    )

    assert result.outcome is ToolBatchOutcome.DECLINED
    assert after == before


@pytest.mark.asyncio
async def test_exact_material_replay_is_noop_with_same_intent(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")
    current = await reads.story_background(scope)
    kwargs = {
        "content": "重放背景",
        "base_revision": current["background"]["baseRevision"],
        "clear_content": False,
    }

    first = await writes.commit_story_background(scope, **kwargs)
    replay = await writes.commit_story_background(scope, **kwargs)

    assert first["noop"] is False
    assert replay["noop"] is True
    assert replay["intentDigest"] == first["intentDigest"]
    assert (await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM story_background_history"
    ))["count"] == 1


@pytest.mark.asyncio
async def test_setting_entity_create_persists_and_guards_duplicates(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")

    receipt = await writes.commit_create_setting_entity(
        scope, name="雾港区", entity_type="location",
        tags="港口,雾", profile_md="终年锁在雾里的旧港区。",
    )

    assert receipt["success"] is True
    assert receipt["noop"] is False
    assert receipt["targetId"] == 2
    assert receipt["name"] == "雾港区"
    assert receipt["entityType"] == "location"
    assert await material_db.fetch_one(
        "SELECT entity_type, name, tags, profile_md FROM setting_entities WHERE id = 2"
    ) == {
        "entity_type": "location", "name": "雾港区",
        "tags": "港口,雾", "profile_md": "终年锁在雾里的旧港区。",
    }
    # 回执的 committedRevision 与读取侧 baseRevision 同组成，可直接用于后续更新。
    entities = await reads.setting_entities(scope, include_profile=True)
    created = next(item for item in entities["items"] if item["id"] == 2)
    assert created["baseRevision"] == receipt["committedRevision"]

    with pytest.raises(WritingMaterialMutationError) as duplicate:
        await writes.validate_create_setting_entity(
            scope, name="雾港区", entity_type="location"
        )
    with pytest.raises(WritingMaterialMutationError) as blank:
        await writes.validate_create_setting_entity(
            scope, name="   ", entity_type="location"
        )
    with pytest.raises(WritingMaterialMutationError) as bad_type:
        await writes.validate_create_setting_entity(
            scope, name="新区", entity_type="city"
        )
    # 跨书查重互不影响：book-2 建同名设定不应被 book-1 的存量拦截。
    book2 = WritingReadScope("book-2")
    await writes.commit_create_setting_entity(
        book2, name="灯塔", entity_type="location"
    )

    assert duplicate.value.code == "writing_setting_entity_duplicate_name"
    assert blank.value.code == "tool_input_invalid"
    assert bad_type.value.code == "tool_input_invalid"


@pytest.mark.asyncio
async def test_setting_entity_delete_requires_fresh_read_and_removes_history(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")
    await material_db.execute(
        "INSERT INTO setting_entity_history (entity_id, before_name, after_name, "
        "source) VALUES (1, '旧名', '灯塔', 'user')"
    )
    await material_db.execute(
        "INSERT INTO setting_entities (book_id, entity_type, name) "
        "VALUES ('book-2', 'item', '他书之物')"
    )
    entity = (await reads.setting_entities(scope, include_profile=True))["items"][0]

    with pytest.raises(WritingMaterialMutationError) as stale:
        await writes.validate_delete_setting_entity(
            scope, entity_id=entity["id"], base_revision="sha256:stale"
        )
    with pytest.raises(WritingMaterialMutationError) as missing:
        await writes.validate_delete_setting_entity(
            scope, entity_id=999, base_revision="sha256:any"
        )
    with pytest.raises(WritingMaterialMutationError) as cross_book:
        await writes.validate_delete_setting_entity(
            scope, entity_id=2, base_revision="sha256:any"
        )
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities WHERE id = 1"
    ) == {"count": 1}

    receipt = await writes.commit_delete_setting_entity(
        scope, entity_id=entity["id"], base_revision=entity["baseRevision"]
    )

    assert receipt["success"] is True
    assert receipt["noop"] is False
    assert receipt["name"] == "灯塔"
    assert receipt["committedRevision"] is None
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities WHERE id = 1"
    ) == {"count": 0}
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entity_history"
    ) == {"count": 0}

    assert stale.value.code == "writing_setting_entity_revision_conflict"
    assert missing.value.code == "writing_setting_entity_not_found"
    assert cross_book.value.code == "writing_setting_entity_not_found"


def test_setting_entity_create_delete_tool_contracts(material_db) -> None:
    catalog = WritingReplacementProfile(material_db).adapter.tool_catalog
    create = catalog.get("createSettingEntity")
    delete = catalog.get("deleteSettingEntity")

    assert create.policy.mode.value == "confirm"
    assert create.policy.risk_level.value == "write"
    assert create.cancellation_linearizable is True
    assert create.context_contract.prerequisite_tools == ("listSettingEntities",)
    assert set(create.schema.parameters["required"]) == {"name", "entityType"}
    assert "baseRevision" not in create.schema.parameters["properties"]

    assert delete.policy.mode.value == "confirm"
    # DESTRUCTIVE：auto_approve 模式也不会自动放行，必须用户确认。
    assert delete.policy.risk_level.value == "destructive"
    assert delete.cancellation_linearizable is True
    assert delete.context_contract.prerequisite_tools == ("getSettingEntities",)
    assert set(delete.schema.parameters["required"]) == {"entityId", "baseRevision"}


@pytest.mark.asyncio
async def test_setting_entity_delete_waits_for_approval_and_rejection_keeps_database(
    material_db,
) -> None:
    reads = SqliteWritingReadRepository(material_db)
    entity = (
        await reads.setting_entities(WritingReadScope("book-1"), include_profile=True)
    )["items"][0]
    approvals = InMemoryApprovalGateway()
    sink = _EventSink()
    request = ToolBatchRequest(
        run_id="run-delete-entity",
        allowed_tool_names=frozenset({"deleteSettingEntity"}),
        state=_state(), calls=(ToolCall(
            id="call-delete-entity", name="deleteSettingEntity",
            arguments_json=json.dumps({
                "entityId": entity["id"],
                "baseRevision": entity["baseRevision"],
            }),
        ),),
    )
    task = asyncio.create_task(CoreToolExecutor(
        WritingReplacementProfile(material_db).adapter.tool_catalog,
        approval_gateway=approvals,
    ).execute_batch(request, sink))

    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities WHERE id = 1"
    ) == {"count": 1}
    assert await approvals.resolve(
        "run-delete-entity", sink.events[0].payload["approvalId"], "reject"
    ) is ApprovalStatus.REJECTED
    result = await task

    assert result.outcome is ToolBatchOutcome.DECLINED
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities WHERE id = 1"
    ) == {"count": 1}


def test_bridge_chunk_maps_setting_entity_changes_for_ui_refresh() -> None:
    from application.sse_mapping import bridge_chunk_for_effect

    chunk = bridge_chunk_for_effect("writing.setting_entities_changed", {
        "bookId": "book-1", "action": "deleted", "id": 3, "name": "灯塔",
    })
    assert chunk == {
        "settingUpdated": {
            "kind": "entity", "action": "deleted", "id": 3, "name": "灯塔",
        }
    }
    assert bridge_chunk_for_effect("writing.setting_entities_changed", "junk") is None
    assert bridge_chunk_for_effect("writing.unknown_effect", {}) is None


@pytest.mark.asyncio
async def test_material_commit_joins_host_idempotency_transaction(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")
    current = await reads.story_background(scope)

    async with material_db.transaction(cancellation_linearizable=True):
        receipt = await writes.commit_story_background(
            scope,
            content="宿主事务内背景",
            base_revision=current["background"]["baseRevision"],
            clear_content=False,
        )

    assert receipt["noop"] is False
    assert await material_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "宿主事务内背景"}


def test_writing_profile_gives_the_model_batch_split_headroom(material_db) -> None:
    """混合批次按 FAILED 回传后，恢复预算必须容许模型拆批重发。"""
    from purra.recovery import RecoveryCause

    policy = WritingReplacementProfile(material_db).adapter.recovery_policy

    assert policy.max_attempts(RecoveryCause.TOOL_INPUT_INVALID) >= 12


@pytest.mark.asyncio
async def test_setting_entity_batch_create_inserts_serially_with_all_or_nothing(material_db) -> None:
    reads = SqliteWritingReadRepository(material_db)
    writes = SqliteWritingMaterialRepository(material_db)
    scope = WritingReadScope("book-1")

    receipt = await writes.commit_create_setting_entities(scope, entities=[
        {"name": "涟漪局", "entityType": "faction", "tags": "官方",
         "profileMd": "# 涟漪局\n官方组织。"},
        {"name": "雾港区", "entityType": "location"},
    ])

    assert receipt["success"] is True
    assert receipt["count"] == 2
    assert [item["name"] for item in receipt["entities"]] == ["涟漪局", "雾港区"]
    rows = await material_db.fetch_all(
        "SELECT id, entity_type, name, tags FROM setting_entities "
        "WHERE book_id = 'book-1' ORDER BY id"
    )
    assert [row["name"] for row in rows] == ["灯塔", "涟漪局", "雾港区"]
    # 回执 revision 与读取侧同组成，可直接作为后续更新的 baseRevision
    entities = await reads.setting_entities(scope, include_profile=True)
    by_name = {item["name"]: item for item in entities["items"]}
    assert (by_name["涟漪局"]["baseRevision"]
            == receipt["entities"][0]["committedRevision"])

    # 批内同名 → 整批拒绝，且不落库
    before = await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities"
    )
    with pytest.raises(WritingMaterialMutationError) as dup:
        await writes.validate_create_setting_entities(scope, entities=[
            {"name": "新区", "entityType": "location"},
            {"name": "新区", "entityType": "faction"},
        ])
    with pytest.raises(WritingMaterialMutationError) as existing:
        await writes.validate_create_setting_entities(scope, entities=[
            {"name": "全新区", "entityType": "location"},
            {"name": "灯塔", "entityType": "location"},  # 已存在
        ])
    assert await material_db.fetch_one(
        "SELECT COUNT(*) AS count FROM setting_entities"
    ) == before

    assert dup.value.code == "writing_setting_entity_duplicate_name"
    assert existing.value.code == "writing_setting_entity_duplicate_name"

    # 超上限 → 输入无效
    with pytest.raises(WritingMaterialMutationError) as over:
        await writes.validate_create_setting_entities(
            scope, entities=[
                {"name": f"条目{index}", "entityType": "other"}
                for index in range(21)
            ],
        )
    assert over.value.code == "tool_input_invalid"


def test_setting_entity_batch_tool_contract(material_db) -> None:
    catalog = WritingReplacementProfile(material_db).adapter.tool_catalog
    registration = catalog.get("createSettingEntities")

    assert registration.policy.mode.value == "confirm"
    assert registration.policy.risk_level.value == "write"
    assert registration.cancellation_linearizable is True
    assert registration.context_contract.prerequisite_tools == ("listSettingEntities",)
    assert registration.schema.parameters["required"] == ["entities"]
    assert registration.schema.parameters["properties"]["entities"]["maxItems"] == 20
