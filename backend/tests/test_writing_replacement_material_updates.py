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
        "editGlobalOutline": "getGlobalOutline",
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
