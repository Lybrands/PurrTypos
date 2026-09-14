from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agents.writing.read_model import (
    SqliteWritingReadRepository,
    WritingReadScope,
    WritingReadScopeError,
)
from agents.writing.read_tools import (
    WRITING_READ_SCOPE_STATE_KEY,
    WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
    build_writing_read_tool_catalog,
)
from agents.writing.context_contract import (
    WRITING_CONTEXT_SELECTION_STATE_KEY,
)
from agents.writing.profile import (
    WRITING_REPLACEMENT_PROFILE_ID,
    WritingReplacementProfile,
    writing_replacement_implementation_profile,
)
from agents.novel_analysis.profile import (
    NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,
)
from agents.screenplay.profile import SCREENPLAY_REPLACEMENT_PROFILE_ID
from agents.writing.composition import (
    create_isolated_writing_replacement_composition,
)
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.composition_routing import RUNTIME_PROFILE_METADATA_KEY
from agents.shared.implementation import legacy_implementation
from agents.shared.implementation_registry import AgentRolloutPolicy
from application.agent_profile_registry import AgentProfileRegistry
from application.agent_run_service import AgentRunService
from application.composition_factory import create_versioned_agent_composition
from agents.shared.implementation_store import SqliteAgentImplementationStore
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.memory import (
    MemoryComponentResource,
    MemoryResourceConfiguration,
)
from application.memory_operations import MemoryApplicationService, memory_metadata
from application.writing_technique_access import WritingTechniqueAccess
from purra_mem0 import EmbeddingResult
from purra.contracts import (
    AgentMessage,
    AgentRunResult,
    AgentRunRequest,
    ContextBudget,
    DomainContext,
    ExecutionState,
    MessageRole,
    ModelRequest,
    RunBinding,
    RunStatus,
)
from purra.api import AgentCoreRunOptions
from purra.model_protocol import generic_capability_snapshot
from purra.tools.contracts import inspect_tool_contract


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await _seed(db)
        yield db
    finally:
        await db.close()


async def _seed(db) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '第一本'), ('book-2', '第二本')"
    )
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id, title) "
        "VALUES (11, 'book-1', 'chapter-1', '写作会话'), "
        "(12, 'book-2', NULL, '其他会话')"
    )
    await db.execute(
        "INSERT INTO story_background (book_id, content) "
        "VALUES ('book-1', '群岛被永夜覆盖。')"
    )
    await db.execute(
        "INSERT INTO characters (book_id, name, tags, profile_md) VALUES "
        "('book-1', '阿澈', '主角', '寻找失踪的姐姐'), "
        "('book-1', '白\n砚', '导师', '守护旧灯塔'), "
        "('book-1', '迟雨', '反派', '试图打开潮汐门'), "
        "('book-2', '外书人物', '', '不能进入本书统计')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id, markdown_content) "
        "VALUES ('writing-1', '写作目录', 'writing', 0, 'book-1', ''), "
        "('global-1', '总纲', 'global', 0, 'book-1', ?), "
        "('global-unowned', '旧总纲', 'global', 0, NULL, '不能回退')",
        ["潮汐门必须在终章关闭。" + "海" * 70_000],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, progress, sort, parent_id) VALUES "
        "('chapter-1', 'writing-1', '第一章', 1, 'done', 1, NULL), "
        "('chapter-2', 'writing-1', '第二章', 1, 'todo', 2, NULL)"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES ('chapter-1', '正文')"
    )
    await db.execute(
        "INSERT INTO setting_entities "
        "(book_id, entity_type, name, tags, profile_md) VALUES "
        "('book-1', 'location', '旧灯塔', '地标', '位于北岛'), "
        "('book-1', 'item', '潮汐钥匙', '遗物', '可以打开潮汐门'), "
        "('book-2', 'faction', '外书势力', '', '不能越界读取')"
    )


class _EmbeddingGateway:
    async def embed(self, texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_character_total_is_host_computed_and_paginated(temp_db) -> None:
    repository = SqliteWritingReadRepository(temp_db)
    scope = WritingReadScope("book-1", session_id=11, chapter_id="chapter-1")

    first = await repository.characters(scope, offset=0, limit=2)
    second = await repository.characters(scope, offset=2, limit=2)

    assert first["countScope"] == "current_book_owned_characters"
    assert first["total"] == 3
    assert first["nextOffset"] == 2
    assert [item["name"] for item in first["items"]] == ["阿澈", "白 砚"]
    assert second["total"] == 3
    assert second["nextOffset"] is None
    assert [item["name"] for item in second["items"]] == ["迟雨"]


@pytest.mark.asyncio
async def test_character_details_and_setting_entities_are_book_scoped(temp_db) -> None:
    repository = SqliteWritingReadRepository(temp_db)
    scope = WritingReadScope("book-1")

    characters = await repository.characters(
        scope,
        names=("迟雨",),
        include_profile=True,
    )
    settings = await repository.setting_entities(
        scope,
        entity_type="location",
        include_profile=True,
    )

    assert characters["total"] == 1
    assert characters["items"][0]["profileMd"] == "试图打开潮汐门"
    assert settings["total"] == 1
    assert settings["items"][0] == {
        "id": 1,
        "entityType": "location",
        "name": "旧灯塔",
        "tags": "地标",
        "profileMd": "位于北岛",
        "baseRevision": settings["items"][0]["baseRevision"],
    }
    assert settings["items"][0]["baseRevision"].startswith("sha256:")


@pytest.mark.asyncio
async def test_background_chapters_and_outline_return_complete_json_shapes(temp_db) -> None:
    repository = SqliteWritingReadRepository(temp_db)
    scope = WritingReadScope("book-1")

    background = await repository.story_background(scope)
    chapters = await repository.writing_chapters(scope, limit=1)
    outline = await repository.global_outline(scope, max_text_length=64_000)

    assert background["background"]["content"] == "群岛被永夜覆盖。"
    assert chapters["total"] == 2
    assert chapters["items"][0]["articleExists"] is True
    assert chapters["items"][0]["storedContentNonempty"] is True
    assert chapters["nextOffset"] == 1
    assert outline["outline"]["truncated"] is True
    assert outline["outline"]["returnedCharacters"] == 64_000
    assert outline["outline"]["totalCharacters"] > 64_000
    json.loads(json.dumps(outline, ensure_ascii=False))


@pytest.mark.asyncio
async def test_global_outline_never_falls_back_to_unowned_legacy_row(temp_db) -> None:
    repository = SqliteWritingReadRepository(temp_db)

    result = await repository.global_outline(WritingReadScope("book-2"))

    assert result["outline"] is None


@pytest.mark.asyncio
async def test_scope_rejects_cross_book_session_and_chapter(temp_db) -> None:
    repository = SqliteWritingReadRepository(temp_db)

    with pytest.raises(WritingReadScopeError) as session_error:
        await repository.validate_scope(WritingReadScope("book-1", session_id=12))
    assert session_error.value.code == "writing_session_scope_conflict"

    with pytest.raises(WritingReadScopeError) as chapter_error:
        await repository.validate_scope(
            WritingReadScope("book-2", chapter_id="chapter-1")
        )
    assert chapter_error.value.code == "writing_chapter_scope_conflict"


def test_read_catalog_is_static_and_not_removed_by_knowledge_purpose(temp_db) -> None:
    catalog = build_writing_read_tool_catalog(temp_db)
    report = inspect_tool_contract(catalog.registrations())
    request = SimpleNamespace(domain_context=SimpleNamespace(
        namespace=WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
        payload={"novelKnowledgeScope": {"purpose": "prose"}},
    ))

    assert report.is_valid, report.describe_violations()
    assert catalog.enabled_names(request) == catalog.names
    assert catalog.names == {
        "getStoryBackground",
        "listBookCharacters",
        "getBookCharacters",
        "listWritingChapters",
        "getGlobalOutline",
        "listSettingEntities",
        "getSettingEntities",
    }


@pytest.mark.asyncio
async def test_list_character_tool_returns_authoritative_total(temp_db) -> None:
    catalog = build_writing_read_tool_catalog(temp_db)
    state = ExecutionState(domain={
        WRITING_READ_SCOPE_STATE_KEY: {
            "bookId": "book-1",
            "sessionId": 11,
            "chapterId": "chapter-1",
        },
    })

    result = await catalog.get("listBookCharacters").handler(
        state,
        {"offset": 0, "limit": 1},
    )
    payload = json.loads(result.content)

    assert result.error_code is None
    assert payload["total"] == 3
    assert payload["countScope"] == "current_book_owned_characters"
    assert len(payload["items"]) == 1


@pytest.mark.asyncio
async def test_tool_scope_failure_has_stable_diagnostic_code(temp_db) -> None:
    catalog = build_writing_read_tool_catalog(temp_db)
    state = ExecutionState(domain={
        WRITING_READ_SCOPE_STATE_KEY: {
            "bookId": "book-1",
            "sessionId": 12,
        },
    })

    result = await catalog.get("getStoryBackground").handler(state, {})
    payload = json.loads(result.content)

    assert result.error_code == "writing_session_scope_conflict"
    assert payload["code"] == "writing_session_scope_conflict"


def _request(
    *,
    book_id="book-1",
    session_id=11,
    chapter_id="chapter-1",
    include_context_selection=True,
):
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "本书共有多少个人物？"),),
        model=ModelRequest(
            provider="openai",
            model="fixture",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="openai:fixture",
                max_generation_tokens=4_096,
            ),
            max_generation_tokens=512,
        ),
        domain_context=DomainContext(
            namespace=WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "book_id": book_id,
                "chapter_id": chapter_id,
                **({
                "associated_chapter_ids": ["chapter-1"],
                "associated_outline_ids": ["global-1"],
                "selected_memory_ids": ["1"],
                "selected_long_term_memory_ids": ["memory-1"],
                "selected_foreshadowing_ids": ["1"],
                } if include_context_selection else {}),
            },
        ),
        session_id=session_id,
        mode="agent",
        tools_enabled=True,
    )


@pytest.mark.asyncio
async def test_replacement_profile_binds_scope_and_implementation(temp_db) -> None:
    profile = WritingReplacementProfile(temp_db)
    request = _request()

    prepared = await profile.prepare_request(request)
    state = profile.adapter.execution_state_factory.create(prepared)
    attributes = profile.run_binding_attributes(prepared)

    assert state.domain[WRITING_READ_SCOPE_STATE_KEY] == {
        "bookId": "book-1",
        "sessionId": 11,
        "chapterId": "chapter-1",
    }
    assert state.domain[WRITING_CONTEXT_SELECTION_STATE_KEY] == {
        "associatedChapterIds": ["chapter-1"],
        "associatedOutlineIds": ["global-1"],
        "selectedSparkIds": ["1"],
        "selectedLongTermMemoryIds": ["memory-1"],
        "selectedForeshadowingIds": ["1"],
    }
    assert state.domain["writingContextSnapshot"]["longTermMemory"] == {
        "state": "memory_component_unavailable",
        "refs": [],
        "missingIds": ["memory-1"],
    }
    assert attributes["agentImplementation"] == replacement_implementation(
        AgentKind.WRITING
    ).to_mapping()
    assert attributes["bookId"] == "book-1"
    assert attributes["contextSelection"] == state.domain[
        WRITING_CONTEXT_SELECTION_STATE_KEY
    ]


@pytest.mark.asyncio
async def test_replacement_context_contains_policy_not_book_facts(temp_db) -> None:
    profile = WritingReplacementProfile(temp_db)
    request = _request()
    budget = ContextBudget(
        window_tokens=4096,
        output_reserve_tokens=512,
        safety_reserve_tokens=128,
        runtime_reserve_tokens=128,
        provider_input_tokens=2048,
        minimum_message_tokens=512,
        context_allocations={"writing_replacement_read_policy": 512},
    )

    bundle = await profile.adapter.context_provider.build_context(request, budget)

    assert len(bundle.blocks) == 1
    content = json.loads(bundle.blocks[0].content)
    assert content["policy"]["characterCountField"] == "total"
    assert content["contextSelection"]["selectionCounts"][
        "associatedChapters"
    ] == 1
    assert content["contextSelection"]["policy"][
        "sourceBodiesRequireTools"
    ] is True
    assert "群岛被永夜覆盖" not in bundle.blocks[0].content
    assert "阿澈" not in bundle.blocks[0].content
    assert "正文" not in bundle.blocks[0].content


@pytest.mark.asyncio
async def test_selected_context_bodies_enter_only_through_explicit_tools(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_memories (book_id, layer, content) "
        "VALUES ('book-1', 'plot', '潮汐钥匙曾经断裂')"
    )
    await temp_db.execute(
        "INSERT INTO ai_foreshadowing "
        "(book_id, chapter_id, content, type, status) "
        "VALUES ('book-1', 'chapter-1', '灯塔将在第三夜熄灭', '悬念', '未回收')"
    )
    profile = WritingReplacementProfile(temp_db)
    request = _request()
    state = profile.adapter.execution_state_factory.create(request)

    associated = await profile.adapter.tool_catalog.get(
        "readAssociatedWritingContext"
    ).handler(state, {"maxTextLength": 100})
    selected = await profile.adapter.tool_catalog.get(
        "readSelectedWritingContext"
    ).handler(state, {})
    associated_payload = json.loads(associated.content)
    selected_payload = json.loads(selected.content)

    assert associated_payload["chapters"][0]["text"] == "正文"
    assert associated_payload["outlines"][0]["text"].startswith(
        "潮汐门必须在终章关闭"
    )
    assert selected_payload["sparks"][0]["content"] == "潮汐钥匙曾经断裂"
    assert selected_payload["foreshadowing"][0]["content"] == (
        "灯塔将在第三夜熄灭"
    )
    assert selected_payload["longTermMemory"] == {
        "requestedIds": ["memory-1"],
        "available": False,
        "items": [],
        "missingIds": ["memory-1"],
        "reason": "memory_component_unavailable",
    }


@pytest.mark.asyncio
async def test_selected_long_term_memory_uses_configured_component(
    temp_db, tmp_path
) -> None:
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(tmp_path / "memory", 4),
        embedding_gateway=_EmbeddingGateway(),
    )
    try:
        created = await MemoryApplicationService(
            temp_db, resource
        ).create_manual(
            book_id="book-1",
            operation_key="selected-memory",
            text="旧灯塔只在无月夜开启。",
            metadata=memory_metadata(kind="world"),
        )
        base = _request(include_context_selection=False)
        request = replace(base, domain_context=DomainContext(
            namespace=WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "book_id": "book-1",
                "chapter_id": "chapter-1",
                "selected_long_term_memory_ids": [created["id"]],
            },
        ))
        profile = WritingReplacementProfile(
            temp_db, memory_resource=resource
        )
        prepared = await profile.prepare_request(request)
        prepared_state = profile.adapter.execution_state_factory.create(
            prepared
        )
        snapshot = prepared_state.domain["writingContextSnapshot"]
        await create_run(
            temp_db,
            run_id="writing-context-snapshot-run",
            session_id=11,
            prompt="读取记忆",
            mode="agent",
            binding=RunBinding(
                namespace="writing.chat.request",
                aggregate_id="11",
                command_id="writing-context-snapshot",
                attributes={"writingContextSnapshot": snapshot},
            ),
        )
        state = ExecutionState(
            domain={
                WRITING_READ_SCOPE_STATE_KEY: prepared_state.domain[
                    WRITING_READ_SCOPE_STATE_KEY
                ],
                WRITING_CONTEXT_SELECTION_STATE_KEY: prepared_state.domain[
                    WRITING_CONTEXT_SELECTION_STATE_KEY
                ],
            },
            run_id="writing-context-snapshot-run",
        )

        result = await profile.adapter.tool_catalog.get(
            "readSelectedWritingContext"
        ).handler(state, {})
        payload = json.loads(result.content)

        assert payload["longTermMemory"]["available"] is True
        assert payload["longTermMemory"]["missingIds"] == []
        assert payload["longTermMemory"]["items"][0]["text"] == (
            "旧灯塔只在无月夜开启。"
        )

        await MemoryApplicationService(temp_db, resource).update(
            book_id="book-1",
            item_id=created["id"],
            version=created["version"],
            operation_key="selected-memory-update",
            text="旧灯塔改为只在满月夜开启。",
            metadata=None,
        )
        changed = await profile.adapter.tool_catalog.get(
            "readSelectedWritingContext"
        ).handler(state, {})
        changed_payload = json.loads(changed.content)
        assert changed_payload["longTermMemory"]["available"] is False
        assert changed_payload["longTermMemory"]["reason"] == (
            "long_term_memory_snapshot_changed"
        )
    finally:
        await resource.close()


@pytest.mark.asyncio
async def test_original_book_has_empty_continuation_source_directory(
    temp_db,
) -> None:
    profile = WritingReplacementProfile(temp_db)
    state = profile.adapter.execution_state_factory.create(_request())

    result = await profile.adapter.tool_catalog.get(
        "listContinuationSourceSections"
    ).handler(state, {})
    payload = json.loads(result.content)

    assert payload["items"] == []
    assert payload["total"] == 0


@pytest.mark.asyncio
async def test_writing_technique_tool_reads_frozen_input_and_detects_change(
    temp_db,
) -> None:
    access = WritingTechniqueAccess(temp_db)
    draft = await access.library.create_draft(operation_id="w3-technique")
    draft = await access.library.apply_changes(
        draft["techniqueId"],
        draft["draftId"],
        expected_revision=0,
        operation_id="w3-technique-content",
        changes=[{
            "action": "put",
            "path": "SKILL.md",
            "content": (
                "---\nname: 行动留白\ndescription: 用动作承载判断\n---\n"
                "先写动作，再揭示人物判断。"
            ),
        }],
    )
    sealed = await access.library.seal(
        "technique",
        draft["techniqueId"],
        draft["draftId"],
        expected_revision=draft["draftRevision"],
        expected_tree_digest=draft["treeDigest"],
        operation_id="w3-technique-seal",
    )
    ref = sealed["sealedRef"]
    await access.library.publish(
        "technique",
        ref["id"],
        ref=ref,
        expected_published_head=None,
        operation_id="w3-technique-publish",
    )
    await access.library.set_selection("session", "11", [ref])
    reserved = await access.reserve_input(
        operation_id="w3-technique-input",
        book_id="book-1",
        session_id="11",
        mode="manual",
        manual=None,
    )
    base = _request(include_context_selection=False)
    request = replace(base, domain_context=DomainContext(
        namespace=WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
        payload={
            "book_id": "book-1",
            "chapter_id": "chapter-1",
            "writing_technique_input_id": reserved["inputId"],
        },
    ))
    profile = WritingReplacementProfile(temp_db)
    prepared = await profile.prepare_request(request)
    state = profile.adapter.execution_state_factory.create(prepared)

    result = await profile.adapter.tool_catalog.get(
        "readWritingTechniqueContext"
    ).handler(state, {})
    payload = json.loads(result.content)

    assert payload["selected"] is True
    assert "行动留白" in payload["entries"][0]["content"]

    await access.grant("book-1", ref)
    automatic = await access.reserve_input(
        operation_id="w3-technique-auto-input",
        book_id="book-1",
        session_id="11",
        mode="auto",
        manual=[],
    )
    auto_request = replace(request, domain_context=DomainContext(
        namespace=WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
        payload={
            "book_id": "book-1",
            "chapter_id": "chapter-1",
            "writing_technique_input_id": automatic["inputId"],
        },
    ))
    auto_prepared = await profile.prepare_request(auto_request)
    auto_state = profile.adapter.execution_state_factory.create(auto_prepared)
    candidates = await profile.adapter.tool_catalog.get(
        "listWritingTechniqueCandidates"
    ).handler(auto_state, {})
    candidate_payload = json.loads(candidates.content)
    assert candidate_payload["mode"] == "auto"
    assert candidate_payload["items"][0]["ref"] == ref
    automatic_read = await profile.adapter.tool_catalog.get(
        "readWritingTechniqueContext"
    ).handler(auto_state, {"automaticRefs": [ref]})
    assert "行动留白" in json.loads(automatic_read.content)["entries"][0][
        "content"
    ]
    changed_selection = await profile.adapter.tool_catalog.get(
        "readWritingTechniqueContext"
    ).handler(auto_state, {"automaticRefs": []})
    assert changed_selection.error_code == "tool_input_invalid"
    assert "writing_technique_automatic_selection_changed" in (
        changed_selection.content
    )

    await temp_db.execute(
        "UPDATE writing_technique_request_inputs SET snapshot_json = '{}' "
        "WHERE id = ?",
        [reserved["inputId"]],
    )
    changed = await profile.adapter.tool_catalog.get(
        "readWritingTechniqueContext"
    ).handler(state, {})
    assert changed.error_code == "tool_input_invalid"
    assert "writing_technique_snapshot_changed" in changed.content


@pytest.mark.asyncio
async def test_continuation_tool_enforces_run_snapshot(temp_db) -> None:
    await temp_db.execute(
        "INSERT INTO novel_source_works "
        "(id, title, source_type) VALUES ('work-1', '原作', 'external')"
    )
    await temp_db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, "
        "byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'source-digest', 1, 10, 8)"
    )
    await temp_db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) "
        "VALUES ('section-1', 'revision-1', 0, '第一章', "
        "'甲打开红门。', 'section-digest')"
    )
    await temp_db.execute(
        "INSERT INTO continuation_canon_snapshots "
        "(id, source_revision_id, source_analysis_id, fork_section_id, "
        "fork_ordinal, content_digest) "
        "VALUES ('snapshot-1', 'revision-1', 'analysis-1', 'section-1', "
        "0, 'canon-digest')"
    )
    await temp_db.execute(
        "INSERT INTO continuation_bindings "
        "(id, target_book_id, source_work_id, source_revision_id, "
        "fork_section_id, fork_ordinal, canon_snapshot_id, binding_digest) "
        "VALUES ('binding-1', 'book-1', 'work-1', 'revision-1', "
        "'section-1', 0, 'snapshot-1', 'binding-digest')"
    )
    await temp_db.execute(
        "UPDATE books SET creation_mode = 'continuation' WHERE id = 'book-1'"
    )
    profile = WritingReplacementProfile(temp_db)
    prepared = await profile.prepare_request(
        _request(include_context_selection=False)
    )
    state = profile.adapter.execution_state_factory.create(prepared)

    result = await profile.adapter.tool_catalog.get(
        "listContinuationSourceSections"
    ).handler(state, {})
    payload = json.loads(result.content)

    assert payload["items"][0]["sectionId"] == "section-1"
    assert payload["items"][0]["title"] == "第一章"

    await temp_db.execute(
        "UPDATE continuation_bindings SET binding_digest = 'changed' "
        "WHERE id = 'binding-1'"
    )
    changed = await profile.adapter.tool_catalog.get(
        "listContinuationSourceSections"
    ).handler(state, {})
    assert changed.error_code == "continuation_context_unavailable"
    assert "continuation_context_snapshot_changed" in changed.content


@pytest.mark.asyncio
async def test_associated_context_rejects_ids_outside_frozen_selection(
    temp_db,
) -> None:
    profile = WritingReplacementProfile(temp_db)
    state = profile.adapter.execution_state_factory.create(_request())

    result = await profile.adapter.tool_catalog.get(
        "readAssociatedWritingContext"
    ).handler(state, {"chapterIds": ["chapter-2"]})

    assert result.error_code == "tool_input_invalid"
    assert "outside the frozen selection" in result.content


def test_replacement_profile_is_independently_installable(temp_db) -> None:
    profile = WritingReplacementProfile(temp_db)
    registry = AgentProfileRegistry((profile,))
    implementation_profile = writing_replacement_implementation_profile()

    assert registry.require(WRITING_REPLACEMENT_PROFILE_ID) is profile
    assert implementation_profile.identity == replacement_implementation(
        AgentKind.WRITING
    )
    assert implementation_profile.runtime_profile_id == WRITING_REPLACEMENT_PROFILE_ID


@pytest.mark.asyncio
async def test_isolated_replacement_composition_has_no_legacy_profile(temp_db) -> None:
    composition = create_isolated_writing_replacement_composition(temp_db)
    try:
        route = composition.agent_implementation_router.for_create(
            AgentKind.WRITING
        )
        assert composition.agent_profile_ids == (WRITING_REPLACEMENT_PROFILE_ID,)
        assert route.runtime_profile_id == WRITING_REPLACEMENT_PROFILE_ID
        assert route.identity == replacement_implementation(AgentKind.WRITING)
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_versioned_composition_selects_writing_replacement_for_new_run(
    temp_db,
) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        prepared = await composition.prepare_request(_request())
        options = composition.bind_run_profile(
            prepared,
            AgentCoreRunOptions(binding=RunBinding(
                namespace="writing.chat.request",
                aggregate_id="11",
                command_id="versioned-writing-create",
            )),
        )
    finally:
        await composition.shutdown()

    assert composition.agent_profile_ids == (
        WRITING_REPLACEMENT_PROFILE_ID,
        NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,
        SCREENPLAY_REPLACEMENT_PROFILE_ID,
    )
    assert prepared.metadata[RUNTIME_PROFILE_METADATA_KEY] == (
        WRITING_REPLACEMENT_PROFILE_ID
    )
    assert options.binding.attributes["agentProfile"] == (
        WRITING_REPLACEMENT_PROFILE_ID
    )
    assert options.binding.attributes["agentImplementation"] == (
        replacement_implementation(AgentKind.WRITING).to_mapping()
    )


@pytest.mark.asyncio
async def test_versioned_composition_refuses_persisted_legacy_execution(
    temp_db,
) -> None:
    run_id = await create_run(
        temp_db,
        run_id="legacy-writing-operation",
        session_id=11,
        prompt="继续写作",
        mode="writing",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="11",
            command_id="legacy-command",
        ),
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        run_id,
        legacy_implementation(AgentKind.WRITING),
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        with pytest.raises(ValueError, match="unsupported Agent profile: writing"):
            await composition.prepare_request(
                _request(include_context_selection=False),
                run_id=run_id,
            )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_versioned_composition_rejects_caller_profile_override(temp_db) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        with pytest.raises(ValueError, match="host-owned"):
            await composition.prepare_request(replace(
                _request(),
                metadata={RUNTIME_PROFILE_METADATA_KEY: "writing"},
            ))
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("composition_mode", ["isolated", "versioned"])
async def test_deterministic_provider_tool_roundtrip_uses_host_total(
    temp_db,
    monkeypatch,
    composition_mode,
) -> None:
    calls = 0
    post_tool_messages = []

    async def fake_stream(_key, messages, options, _provider, signal=None):
        nonlocal calls
        calls += 1
        assert signal is not None

        async def stream():
            if calls == 1:
                names = {
                    item["function"]["name"]
                    for item in options.get("tools", ())
                }
                assert "listBookCharacters" in names
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-character-total",
                                "type": "function",
                                "function": {
                                    "name": "listBookCharacters",
                                    "arguments": '{"limit":1}',
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return
            post_tool_messages.extend(messages)
            yield {
                "choices": [{
                    "delta": {"content": "本书共有 3 个人物。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        fake_stream,
    )
    composition = (
        create_isolated_writing_replacement_composition(temp_db)
        if composition_mode == "isolated"
        else create_versioned_agent_composition(
            temp_db,
            agent_rollout_policy=AgentRolloutPolicy(
                frozenset({AgentKind.WRITING})
            ),
        )
    )
    request = _request()
    results = []
    try:
        async for update in AgentRunService(composition).run(
            request=request,
            api_key="fixture-key",
            options=AgentCoreRunOptions(
                binding=RunBinding(
                    namespace="writing.chat.request",
                    aggregate_id="11",
                    command_id=f"{composition_mode}-character-count",
                ),
                force_planned_tool_choice=False,
            ),
            signal=asyncio.Event(),
        ):
            if isinstance(update, AgentRunResult):
                results.append(update)
    finally:
        await composition.shutdown()

    assert calls >= 2
    assert len(results) == 1
    assert results[0].status is RunStatus.DONE
    assert results[0].final_response == "本书共有 3 个人物。"
    tool_messages = [
        message for message in post_tool_messages
        if message.get("role") == "tool"
    ]
    assert tool_messages
    tool_payload = json.loads(tool_messages[0]["content"])
    assert tool_payload["total"] == 3
    assert tool_payload["items"][0]["id"] == 1
    assert await SqliteAgentImplementationStore(temp_db).load(
        results[0].run_id
    ) == replacement_implementation(AgentKind.WRITING)
