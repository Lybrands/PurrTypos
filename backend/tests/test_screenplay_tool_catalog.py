from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

import application.agent_composition as agent_composition_module
from application.agent_composition import AgentComposition
from application.composition_factory import create_agent_composition
from application.screenplay_tool_calling import (
    ScreenplayToolCallingService,
)
from database.connection import DatabaseConnection
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from infrastructure.screenplay import (
    ScreenplayCandidateArtifacts,
    build_screenplay_tool_catalog,
)
from purra.contracts import (
    AgentRunRequest,
    ExecutionState,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ReasoningMode,
    RunLineage,
    RunStatus,
    ToolCallDelta,
)
from purra.errors import ModelGatewayError
from purra.artifacts.errors import ArtifactValidationError
from purra.events import CoreEventType
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


@pytest_asyncio.fixture
async def screenplay_tool_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _context(
    *,
    source_book_id: str | None = None,
    source_scope: dict | None = None,
    target_role: str = "creativeBrief",
    expected_part_type: str = "document",
    expected_part_key: str = "main",
    tool_access: str = "all",
):
    return ScreenplayAgentDomainContext(
        project_id="screenplay-project",
        task_id="screenplay-task",
        unit_id="generate-deliverable",
        target_role=target_role,
        expected_part_type=expected_part_type,
        expected_part_key=expected_part_key,
        tool_access=tool_access,
        source_book_id=source_book_id,
        source_scope=source_scope or {"mode": "whole_book"},
    )


def _part_lineage(root_run_id: str) -> RunLineage:
    return RunLineage(
        parent_run_id=root_run_id,
        root_run_id=root_run_id,
        delegation_id=None,
        agent_role="screenplay-part",
        depth=1,
    )


def _request(context: ScreenplayAgentDomainContext) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(),
        model=ModelRequest(provider="openai", model="fixture-model"),
        domain_context=context.to_core_context(),
        mode="agent",
        tools_enabled=True,
    )


def _tool_handler(catalog, name: str):
    return next(
        item.handler for item in catalog.registrations()
        if item.schema.name == name
    )


async def _canonical_public_events(db, run_id: str):
    return await db.fetch_all(
        "SELECT kind, payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_id IS NOT NULL "
        "AND visibility = 'public' ORDER BY id",
        [run_id],
    )


def _source_state(*, target_role: str = "creativeBrief") -> ExecutionState:
    return ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "source-read",
            "targetRole": target_role,
            "expectedPartType": "document",
            "expectedPartKey": "main",
            "sourceBookId": "source-book",
        },
        run_id="run-source-contract",
    )


async def _install_source_project(db, *, restricted: bool) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('source-book', '来源作品')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing-source', '写作目录', 'writing', 'source-book')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-1', 'writing-source', '第一章', 1)"
    )
    await db.execute(
        "INSERT INTO outlines "
        "(id, title, type, sort, book_id, writing_chapter_id, markdown_content) "
        "VALUES ('outline-1', '第 1 章大纲', 'chapter', 1, "
        "'source-book', 'chapter-1', '人物在雨夜重逢。')"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [
            "chapter-1",
            json.dumps({
                "root": {
                    "children": [{
                        "type": "paragraph",
                        "children": [{"type": "text", "text": "第一章正文"}],
                    }],
                },
            }, ensure_ascii=False),
        ],
    )
    source_scope = (
        {
            "mode": "first_chapters",
            "chapterIds": ["chapter-1"],
            "chapters": [{
                "id": "chapter-1",
                "title": "第一章",
                "index": 1,
            }],
        }
        if restricted
        else {"mode": "whole_book"}
    )
    await db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_book_id, source_scope_json, "
        "source_snapshot_json) VALUES (?, '改编项目', 'book', ?, ?, '{}')",
        ["screenplay-project", "source-book", json.dumps(source_scope)],
    )


async def test_screenplay_catalog_uses_writing_catalog_naming_and_stage_scope(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    registrations = {item.schema.name: item for item in catalog.registrations()}
    original_enabled = catalog.enabled_names(_request(_context()))
    adapted_enabled = catalog.enabled_names(
        _request(_context(source_book_id="source-book"))
    )
    restricted_enabled = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        source_scope={
            "mode": "first_chapters",
            "chapterIds": ["chapter-1"],
        },
    )))

    assert "writeScreenplayCandidatePart" in original_enabled
    assert "inspectScreenplayProject" in original_enabled
    assert "readSourceChapters" not in original_enabled
    assert "readSourceChapters" in adapted_enabled
    assert "readSourceChapters" in restricted_enabled
    assert "querySourceStoryFacts" in restricted_enabled
    assert "readSourceCharacters" not in restricted_enabled
    assert "listSourceCharacters" not in restricted_enabled
    assert "readSourceWorldEntities" not in restricted_enabled
    assert "listSourceWorldEntities" not in restricted_enabled
    assert "readSourceBackground" not in restricted_enabled
    assert "projectId" not in registrations[
        "writeScreenplayCandidatePart"
    ].schema.parameters["properties"]
    assert registrations[
        "writeScreenplayCandidatePart"
    ].schema.parameters["required"] == ("candidate",)


async def test_host_captured_scene_exposes_no_model_writer(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    enabled = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="screenplayDraft",
        expected_part_type="scene",
        expected_part_key="ep01_s04",
    )))

    assert enabled == set()


async def test_formal_stage_tool_access_never_mixes_reads_and_candidate_writes(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    evidence = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        tool_access="evidence_read",
    )))
    candidate = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        tool_access="candidate_write",
    )))

    assert evidence
    assert "writeScreenplayCandidatePart" not in evidence
    assert "inspectScreenplayCandidate" not in evidence
    assert candidate == {
        "writeScreenplayCandidatePart",
        "inspectScreenplayCandidate",
    }


async def test_candidate_artifact_stores_long_content_only_once(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = next(
        item.handler for item in catalog.registrations()
        if item.schema.name == "writeScreenplayCandidatePart"
    )
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "episode-4",
            "targetRole": "screenplayDraft",
            "expectedPartType": "episode",
            "expectedPartKey": "4",
        },
        run_id="run-screenplay-candidate",
    )
    candidate = {
        "episodeNumber": 4,
        "title": "第四集",
        "executionSummary": "推进冲突。",
        "continuitySummary": "人物作出选择。",
        "scenes": [
            {"sceneId": "scene-1", "processSummary": "建立目标。", "sceneText": "正文一"},
            {"sceneId": "scene-2", "processSummary": "升级冲突。", "sceneText": "正文二"},
        ],
    }

    await write(state, {"candidate": candidate})
    finalized = await ScreenplayCandidateArtifacts(
        screenplay_tool_db
    ).finalize_run(state.run_id)

    assert finalized["payload"] == candidate
    assert finalized["contentText"] == "正文一\n\n正文二"
    stored = await screenplay_tool_db.fetch_one(
        "SELECT items_json FROM ai_agent_artifact_batches WHERE artifact_id = ?",
        [finalized["artifactId"]],
    )
    assert str(stored["items_json"]).count("正文一") == 1


async def test_candidate_artifact_accepts_one_incremental_scene(
    screenplay_tool_db,
):
    artifacts = ScreenplayCandidateArtifacts(screenplay_tool_db)
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "episode-4:scene-1",
            "targetRole": "screenplayDraft",
            "expectedPartType": "scene",
            "expectedPartKey": "scene-1",
        },
        run_id="run-incremental-scene",
    )
    scene = {
        "sceneId": "scene-1",
        "processSummary": "建立人物目标并引出阻力。",
        "sceneText": "只写入当前场景正文。",
    }

    await artifacts.write(state, {"candidate": scene})
    finalized = await artifacts.finalize_run(state.run_id)

    assert finalized["partType"] == "scene"
    assert finalized["payload"] == scene
    assert finalized["contentText"] == scene["sceneText"]


async def test_invalid_candidate_part_cannot_be_finalized(
    screenplay_tool_db,
):
    artifacts = ScreenplayCandidateArtifacts(screenplay_tool_db)
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "draft:4:scene-1",
            "targetRole": "screenplayDraft",
            "expectedPartType": "scene",
            "expectedPartKey": "scene-1",
        },
        run_id="run-invalid-scene",
    )

    with pytest.raises(ArtifactValidationError):
        await artifacts.write(state, {"candidate": {
            "sceneId": "another-scene",
            "processSummary": "错误场景。",
            "sceneText": "不应提交。",
        }})

    artifact = await screenplay_tool_db.fetch_one(
        "SELECT id, status FROM ai_agent_artifacts WHERE run_id = ?",
        [state.run_id],
    )
    assert artifact is not None
    assert artifact["status"] == "open"
    assert await screenplay_tool_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_batches "
        "WHERE artifact_id = ?",
        [artifact["id"]],
    ) == {"count": 0}


async def test_candidate_artifact_has_no_hidden_character_limit(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = _tool_handler(catalog, "writeScreenplayCandidatePart")
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "section:creativeBrief:premise",
            "targetRole": "creativeBrief",
            "expectedPartType": "document_section",
            "expectedPartKey": "premise",
        },
        run_id="run-long-document-section",
    )
    section = {
        "sectionKey": "premise",
        "title": "故事前提",
        "executionSummary": "完成当前最小业务章节。",
        "contentText": "正文" * 50_000,
        "contentJson": {"fields": {"premise": "人物重新相遇。"}},
    }

    await write(state, {"candidate": section})
    finalized = await ScreenplayCandidateArtifacts(
        screenplay_tool_db
    ).finalize_run(state.run_id)

    assert finalized["contentText"] == section["contentText"]


async def test_source_tool_persists_a_run_bound_evidence_receipt(
    screenplay_tool_db,
):
    await screenplay_tool_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_book_id, source_snapshot_json) "
        "VALUES (?, '改编项目', 'book', ?, '{}')",
        ["screenplay-project", "source-book"],
    )
    await screenplay_tool_db.execute(
        "INSERT INTO story_background (book_id, content) VALUES (?, ?)",
        ["source-book", "城市停电后，旧友被迫共同寻找失踪者。"],
    )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read_background = next(
        item.handler for item in catalog.registrations()
        if item.schema.name == "readSourceBackground"
    )
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "source-analysis",
            "targetRole": "sourceAnalysis",
            "expectedPartType": "document",
            "expectedPartKey": "main",
            "sourceBookId": "source-book",
        },
        run_id="run-source-read",
    )

    result = await read_background(state, {})

    assert "城市停电" in result.content
    receipt = await screenplay_tool_db.fetch_one(
        "SELECT agent_run_id, tool_name, source_type, source_id, coverage_mode "
        "FROM screenplay_source_receipts"
    )
    assert receipt == {
        "agent_run_id": "run-source-read",
        "tool_name": "readSourceBackground",
        "source_type": "story_background",
        "source_id": "main",
        "coverage_mode": "full",
    }


async def test_outline_and_chapter_tools_expose_typed_ids_and_recover_mismatch(
    screenplay_tool_db,
):
    await _install_source_project(screenplay_tool_db, restricted=True)
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    state = _source_state()

    outline_result = await _tool_handler(
        catalog,
        "readSourceOutline",
    )(state, {"limit": 1})
    outline_payload = json.loads(outline_result.content)
    outline = outline_payload["outlines"][0]

    assert outline == {
        "outlineId": "outline-1",
        "chapterId": "chapter-1",
        "title": "第 1 章大纲",
        "type": "chapter",
        "content": "人物在雨夜重逢。",
    }
    assert outline_payload["hasMore"] is False

    mismatch = await _tool_handler(
        catalog,
        "readSourceChapters",
    )(state, {"chapterIds": ["outline-1"]})
    mismatch_payload = json.loads(mismatch.content)

    assert mismatch.error_code == "tool_input_invalid"
    assert mismatch.effect_state.value == "not_started"
    assert mismatch_payload["outlineIdMappings"] == [{
        "outlineId": "outline-1",
        "chapterId": "chapter-1",
    }]

    corrected = await _tool_handler(
        catalog,
        "readSourceChapters",
    )(state, {"chapterIds": ["chapter-1"]})
    chapter = json.loads(corrected.content)["chapters"][0]

    assert corrected.error_code is None
    assert chapter["chapterId"] == "chapter-1"
    assert "id" not in chapter
    assert chapter["content"] == "第一章正文"


async def test_source_catalog_ids_are_typed_and_cross_catalog_ids_are_rejected(
    screenplay_tool_db,
):
    await _install_source_project(screenplay_tool_db, restricted=False)
    await screenplay_tool_db.execute(
        "INSERT INTO characters (id, book_id, name, tags) "
        "VALUES (101, 'source-book', '林月', '主角')"
    )
    await screenplay_tool_db.execute(
        "INSERT INTO setting_entities (id, book_id, entity_type, name, tags) "
        "VALUES (202, 'source-book', 'location', '清河桥', '地点')"
    )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    state = _source_state()

    characters = json.loads((await _tool_handler(
        catalog,
        "listSourceCharacters",
    )(state, {})).content)["characters"]
    entities = json.loads((await _tool_handler(
        catalog,
        "listSourceWorldEntities",
    )(state, {})).content)["entities"]

    assert characters[0]["characterId"] == 101
    assert "id" not in characters[0]
    assert entities[0]["entityId"] == 202
    assert "id" not in entities[0]

    mismatch = await _tool_handler(
        catalog,
        "readSourceCharacters",
    )(state, {"characterIds": [202]})
    assert mismatch.error_code == "tool_input_invalid"
    assert json.loads(mismatch.content)["invalidIds"] == [202]


async def test_deliverable_search_uses_only_accepted_heads_and_typed_revisions(
    screenplay_tool_db,
):
    await screenplay_tool_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('screenplay-project', '原创项目', 'original', '{}')"
    )
    deliverable = await screenplay_tool_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = 'screenplay-project' AND role = 'creativeBrief'"
    )
    assert deliverable is not None
    deliverable_id = str(deliverable["id"])
    for revision_id, number, text in (
        ("revision-old", 1, "已经废弃的冲突"),
        ("revision-head", 2, "当前采用的冲突"),
    ):
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, "
            "summary_json, created_by) VALUES (?, 'screenplay-project', ?, "
            "?, ?, '{}', 'test')",
            [revision_id, deliverable_id, number, f"digest-{number}"],
        )
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES "
            "(?, 'document', 'main', 0, '{}', ?, ?)",
            [revision_id, text, f"part-digest-{number}"],
        )
    await screenplay_tool_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES "
        "('screenplay-project', ?, 'revision-head')",
        [deliverable_id],
    )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "search-current",
            "targetRole": "creativeBrief",
            "expectedPartType": "document",
            "expectedPartKey": "main",
        },
        run_id="run-search-current",
    )
    search = _tool_handler(catalog, "searchScreenplayDeliverables")

    old_matches = json.loads((await search(
        state,
        {"query": "废弃"},
    )).content)["matches"]
    current_matches = json.loads((await search(
        state,
        {"query": "采用"},
    )).content)["matches"]

    assert old_matches == []
    assert current_matches[0]["revisionId"] == "revision-head"

    wrong_role = await _tool_handler(
        catalog,
        "getScreenplayEpisodeContext",
    )(state, {"episodeNumber": 1, "draftRevisionId": "revision-head"})
    assert wrong_role.error_code == "tool_input_invalid"
    assert json.loads(wrong_role.content)["invalidDraftRevisionId"] == (
        "revision-head"
    )


async def test_candidate_input_failure_is_recoverable_in_the_same_run(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = _tool_handler(catalog, "writeScreenplayCandidatePart")
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "episode-4",
            "targetRole": "screenplayDraft",
            "expectedPartType": "episode",
            "expectedPartKey": "4",
        },
        run_id="run-correct-candidate",
    )

    rejected = await write(state, {"candidate": {"title": "第四集"}})
    accepted = await write(state, {"candidate": {
        "title": "第四集",
        "scenes": [{"sceneId": "scene-1", "sceneText": "第四集正文"}],
    }})

    assert rejected.error_code == "tool_input_invalid"
    assert rejected.effect_state.value == "not_started"
    assert accepted.error_code is None
    assert json.loads(accepted.content)["acceptedParts"] == 1


class _ToolModelGateway:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls = 0
        self.invocations = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "toolNames": [tool.name for tool in invocation.tools],
        }

    async def stream(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        self.calls += 1

        async def chunks():
            if self.calls == 1:
                arguments = json.dumps({
                    "candidate": {
                        "title": "人物驱动简报",
                        "executionSummary": "根据项目目标形成核心取舍。",
                        "contentText": "# 创作简报\n\n人物驱动。",
                        "contentJson": {
                            "fields": {
                                "approach": "人物驱动",
                                "premise": "一次意外重逢",
                            },
                        },
                    },
                }, ensure_ascii=False)
                yield ModelStreamChunk(
                    content_delta="**正在整理候选简报**\n\n已核对项目目标，准备写入候选稿。",
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id="call-write-candidate",
                        type="function",
                        name="writeScreenplayCandidatePart",
                        arguments_fragment=arguments,
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )
            else:
                yield ModelStreamChunk(
                    content_delta="候选稿已写入。",
                    finish_reason=ModelFinishReason.STOP,
                )

        return ModelStream(chunks=chunks(), model="fixture-model")

    async def complete(self, messages, invocation, signal=None):
        del messages, invocation, signal
        return ModelCompletion(
            message={"role": "assistant", "content": "unused"},
            model="fixture-model",
        )


class _ReasoningTruncationToolModelGateway:
    def __init__(self) -> None:
        self.invocations = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "toolNames": [tool.name for tool in invocation.tools],
        }

    async def stream(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        call_number = len(self.invocations)

        async def chunks():
            if call_number == 1:
                yield ModelStreamChunk(
                    reasoning_delta="推理内容耗尽了本轮额度。",
                )
                yield ModelStreamChunk(finish_reason=ModelFinishReason.LENGTH)
                return
            if call_number == 2:
                yield ModelStreamChunk(
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id="call-read-after-reasoning-retry",
                        type="function",
                        name="inspectScreenplayProject",
                        arguments_fragment="{}",
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )
                return
            if call_number == 3:
                arguments = json.dumps({
                    "candidate": {
                        "title": "重试后生成的简报",
                        "executionSummary": "保持用户选择的思考模式并完成结构化写入。",
                        "contentText": "# 创作简报\n\n重试成功。",
                        "contentJson": {
                            "fields": {"approach": "人物驱动"},
                        },
                    },
                }, ensure_ascii=False)
                yield ModelStreamChunk(
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id="call-write-after-reasoning-retry",
                        type="function",
                        name="writeScreenplayCandidatePart",
                        arguments_fragment=arguments,
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )
                return
            yield ModelStreamChunk(
                content_delta="候选稿已写入。",
                finish_reason=ModelFinishReason.STOP,
            )

        return ModelStream(chunks=chunks(), model="fixture-model")

    async def complete(self, messages, invocation, signal=None):
        del messages, invocation, signal
        return ModelCompletion(
            message={"role": "assistant", "content": "unused"},
            model="fixture-model",
        )


class _HostPreparedSceneModelGateway:
    def __init__(self) -> None:
        self.invocations = []
        self.messages = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "toolNames": [tool.name for tool in invocation.tools],
        }

    async def stream(self, messages, invocation, signal=None):
        del signal
        self.invocations.append(invocation)
        self.messages.append(messages)

        async def chunks():
            yield ModelStreamChunk(
                content_delta=(
                    "外景 石墙前-夜\n\n"
                    "场记说：\"开门 {现在}\"。\n\n"
                    "林月冲向石墙。"
                ),
                finish_reason=ModelFinishReason.STOP,
            )

        return ModelStream(chunks=chunks(), model="fixture-model")

    async def complete(self, messages, invocation, signal=None):
        del messages, invocation, signal
        return ModelCompletion(
            message={"role": "assistant", "content": "unused"},
            model="fixture-model",
        )


async def test_screenplay_tool_run_publishes_no_host_text_and_redacts_candidate_body(
    screenplay_tool_db,
    monkeypatch,
):
    gateway = _ToolModelGateway()
    monkeypatch.setattr(
        agent_composition_module,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    composition = create_agent_composition(screenplay_tool_db)
    runtime = ScreenplayAgentRuntimeRequest.model_validate({
        "apiKey": "secret",
        "apiProvider": "openai",
        "baseURL": "https://provider.example/v1",
        "options": {
            "model": "fixture-model",
            "model_profile": "deepseek:deepseek-v4-flash",
            "max_tokens": 32_768,
        },
        "contextWindow": "128k",
    })
    try:
        result = await ScreenplayToolCallingService(
            screenplay_tool_db,
            composition=composition,
        ).run_candidate(
            runtime=runtime,
            session_id=1,
            prompt="生成创作简报",
            system_instruction="按需使用工具并写入候选稿。",
            user_payload={"instruction": "生成创作简报"},
            domain_context=_context(),
            conversation_turn_id="turn-screenplay-tools",
            lineage=_part_lineage("root-screenplay-tools"),
            reasoning_mode=ReasoningMode.DISABLED,
        )
    finally:
        await composition.shutdown()

    assert result.candidate["payload"]["title"] == "人物驱动简报"
    events = await _canonical_public_events(screenplay_tool_db, result.run_id)
    assert "人物驱动简报" not in json.dumps(events, ensure_ascii=False)
    assert not any(row["kind"] == "provider.content_delta" for row in events)
    assert gateway.invocations
    assert all(
        invocation.reasoning_mode is ReasoningMode.DISABLED
        for invocation in gateway.invocations
    )


async def test_screenplay_tool_length_fails_without_replaying_reasoning_mode(
    screenplay_tool_db,
    monkeypatch,
):
    await _install_source_project(screenplay_tool_db, restricted=False)
    gateway = _ReasoningTruncationToolModelGateway()
    monkeypatch.setattr(
        agent_composition_module,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    composition = create_agent_composition(screenplay_tool_db)
    runtime = ScreenplayAgentRuntimeRequest.model_validate({
        "apiKey": "secret",
        "apiProvider": "openai",
        "baseURL": "https://provider.example/v1",
        "options": {
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
            "max_tokens": 32_768,
            "thinking": {"type": "enabled"},
        },
        "contextWindow": "128k",
    })
    try:
        with pytest.raises(ModelGatewayError) as captured:
            await ScreenplayToolCallingService(
                screenplay_tool_db,
                composition=composition,
            ).run_candidate(
                runtime=runtime,
                session_id=1,
                prompt="生成创作简报",
                system_instruction="按需使用工具并写入候选稿。",
                user_payload={"instruction": "生成创作简报"},
                domain_context=_context(),
                conversation_turn_id="turn-reasoning-fallback",
                lineage=_part_lineage("root-reasoning-fallback"),
            )
    finally:
        await composition.shutdown()

    assert captured.value.code == "model_output_truncated"
    assert [item.reasoning_mode for item in gateway.invocations] == [
        ReasoningMode.DEFAULT,
    ]
    run = await screenplay_tool_db.fetch_one(
        "SELECT id FROM ai_agent_runs ORDER BY create_time DESC LIMIT 1"
    )
    events = await _canonical_public_events(screenplay_tool_db, str(run["id"]))
    assert "candidate" not in json.dumps(events, ensure_ascii=False)


async def test_host_prepared_scene_is_host_committed_without_tool_json(
    screenplay_tool_db,
    monkeypatch,
):
    gateway = _HostPreparedSceneModelGateway()
    monkeypatch.setattr(
        agent_composition_module,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    composition = create_agent_composition(screenplay_tool_db)
    runtime = ScreenplayAgentRuntimeRequest.model_validate({
        "apiKey": "secret",
        "apiProvider": "openai",
        "baseURL": "https://provider.example/v1",
        "options": {
            "model": "fixture-model",
            "model_profile": "deepseek:deepseek-v4-flash",
            "max_tokens": 32_768,
        },
        "contextWindow": "128k",
    })
    try:
        result = await ScreenplayToolCallingService(
            screenplay_tool_db,
            composition=composition,
        ).run_candidate(
            runtime=runtime,
            session_id=1,
            prompt="重写第一集第四场",
            system_instruction="使用宿主提供的上下文写入当前场景。",
            user_payload={
                "sceneId": "ep01_s04",
                "scenePlan": {"objective": "完成穿墙钩子"},
                "currentDraftScene": "旧稿",
                "revisionIssues": [],
            },
            domain_context=_context(
                source_book_id="source-book",
                target_role="screenplayDraft",
                expected_part_type="scene",
                expected_part_key="ep01_s04",
            ),
            conversation_turn_id="turn-host-prepared-scene",
            lineage=_part_lineage("root-host-prepared-scene"),
            reasoning_mode=ReasoningMode.DISABLED,
            host_candidate_template={
                "sceneId": "ep01_s04",
                "processSummary": "落实追逐目标并完成穿墙钩子。",
            },
        )
    finally:
        await composition.shutdown()

    assert result.candidate["payload"]["sceneId"] == "ep01_s04"
    assert result.candidate["payload"]["sceneText"] == (
        "外景 石墙前-夜\n\n"
        "场记说：\"开门 {现在}\"。\n\n"
        "林月冲向石墙。"
    )
    assert len(gateway.invocations) == 1
    assert gateway.invocations[0].tools == ()
    events = await _canonical_public_events(screenplay_tool_db, result.run_id)
    assert "外景 石墙前" not in json.dumps(events, ensure_ascii=False)
    completed = next(
        json.loads(str(row["payload_json"])) for row in events
        if row["kind"] == "run.lifecycle"
        and json.loads(str(row["payload_json"])).get("status") == "done"
    )
    assert "finalResponse" not in completed
    stored_run = await screenplay_tool_db.fetch_one(
        "SELECT status, final_response FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    )
    stored_artifact = await screenplay_tool_db.fetch_one(
        "SELECT status FROM ai_agent_artifacts WHERE run_id = ?",
        [result.run_id],
    )
    assert stored_run == {"status": "done", "final_response": ""}
    assert stored_artifact == {"status": "finalized"}


async def test_candidate_and_run_completion_roll_back_as_one_commit(
    screenplay_tool_db,
    monkeypatch,
):
    class RejectAfterCandidateProjection:
        async def project(self, run_id, commit):
            del run_id
            if commit.terminal_status is RunStatus.DONE:
                raise RuntimeError("reject terminal projection")
            return None

    gateway = _HostPreparedSceneModelGateway()
    monkeypatch.setattr(
        agent_composition_module,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    composition = create_agent_composition(
        screenplay_tool_db,
        run_commit_projector=RejectAfterCandidateProjection(),
    )
    runtime = ScreenplayAgentRuntimeRequest.model_validate({
        "apiKey": "secret",
        "apiProvider": "openai",
        "baseURL": "https://provider.example/v1",
        "options": {
            "model": "fixture-model",
            "model_profile": "deepseek:deepseek-v4-flash",
            "max_tokens": 32_768,
        },
        "contextWindow": "128k",
    })
    try:
        with pytest.raises(ModelGatewayError) as raised:
            await ScreenplayToolCallingService(
                screenplay_tool_db,
                composition=composition,
            ).run_candidate(
                runtime=runtime,
                session_id=1,
                prompt="重写第一集第四场",
                system_instruction="只输出当前场景正文。",
                user_payload={"sceneId": "ep01_s04"},
                domain_context=_context(
                    target_role="screenplayDraft",
                    expected_part_type="scene",
                    expected_part_key="ep01_s04",
                ),
                conversation_turn_id="turn-atomic-rollback",
                lineage=_part_lineage("root-atomic-rollback"),
                reasoning_mode=ReasoningMode.DISABLED,
                host_candidate_template={
                    "sceneId": "ep01_s04",
                    "processSummary": "完成当前场景。",
                },
            )
    finally:
        await composition.shutdown()

    assert raised.value.code == "completion_projection_failed"
    stored_run = await screenplay_tool_db.fetch_one(
        "SELECT id, status, final_response FROM ai_agent_runs "
        "ORDER BY create_time DESC LIMIT 1"
    )
    artifact_count = await screenplay_tool_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts WHERE run_id = ?",
        [stored_run["id"]],
    )
    assert stored_run["status"] == "failed"
    assert stored_run["final_response"] == "completion_projection_failed"
    assert artifact_count == {"count": 0}
