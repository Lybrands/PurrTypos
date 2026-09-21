from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import SecretStr

from agents.screenplay.composition import (
    create_isolated_screenplay_replacement_composition,
)
from agents.screenplay.conversation_service import (
    ScreenplayReplacementConversationService,
)
from agents.screenplay.conversation_query import (
    ScreenplayReplacementConversationQuery,
)
from agents.screenplay.entry_service import (
    ScreenplayReplacementExecutionService,
    ScreenplayRuntimeBindingUnavailable,
)
from agents.screenplay.executor import ScreenplayReplacementUnitExecutor
from agents.screenplay.recipe import ScreenplayHostRecipeSpec
from agents.screenplay.request_compiler import (
    ScreenplayRequestCompilationError,
    compile_screenplay_host_recipe,
)
from agents.shared.implementation import AgentKind
from agents.shared.implementation_registry import AgentRolloutPolicy
from application.composition_factory import create_versioned_agent_composition
from application.model_runtime import runtime_from_settings
from database.connection import DatabaseConnection
from agents.screenplay.contracts import ScreenplayStageCommand
from purra.json_values import thaw_json_mapping
from purra.contracts import AgentRunResult, RunStatus
from purra.errors import ModelGatewayError
from tests.support.planning_stream import route_planning_stream


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_book_id, source_scope_json) "
        "VALUES ('project-entry', '入口剧本', 'book', 'book-entry', ?)",
        [json.dumps({"schemaVersion": 1, "mode": "whole_book"})],
    )
    await db.execute("INSERT INTO books (id, title) VALUES ('book-entry', '原作')")
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing-entry', '正文', 'writing', 'book-entry')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-entry', 'writing-entry', '第一章', 1)"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-entry", "旧城在暴雨中停电。"],
    )
    try:
        yield db
    finally:
        await db.close()


def _command(target="sourceAnalysis", action="create"):
    return ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": action,
        "targetRole": target,
        "scope": {
            "kind": "current_stage",
            "count": None,
            "episodeNumbers": [],
        },
    })


@pytest.mark.asyncio
async def test_http_shaped_ordinary_turn_is_admitted_without_formal_operation(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (1, 'ordinary replacement', 'screenplay', 'project-entry')"
    )
    runtime = await _runtime(temp_db)
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="http-boundary-test"
        )
        turn = await service.submit_turn(
            command_id="ordinary-http-command",
            project_id="project-entry",
            request=SimpleNamespace(
                sessionId=1,
                content="聊聊这个项目",
                runtime=runtime,
                stageCommand=None,
            ),
        )
    finally:
        await composition.shutdown()

    assert turn["status"] == "queued"
    assert turn["stageCommand"] is None
    assert turn["operationId"] is None
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    ) is None


@pytest.mark.asyncio
async def test_ordinary_conversation_returns_text_without_task_or_revision(
    temp_db,
    monkeypatch,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (2, 'ordinary execution', 'screenplay', 'project-entry')"
    )
    runtime = await _runtime(temp_db)

    async def no_planner(*_args, **_kwargs):
        raise AssertionError("ordinary conversation must not invoke Planner")

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        tool_results = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tool_results:
                yield _tool_call(
                    "inspect-ordinary-project",
                    "inspectScreenplayProjectV1",
                    {},
                )
                return
            receipt = json.loads(tool_results[-1]["content"])
            assert receipt["project"]["id"] == "project-entry"
            yield {
                "choices": [{
                    "delta": {"content": "当前项目处于来源分析阶段。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        no_planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(no_planner, runtime_stream),
    )
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="ordinary-run-test"
        )
        turn = await service.submit_turn(
            command_id="ordinary-run-command",
            project_id="project-entry",
            request=SimpleNamespace(
                sessionId=2,
                content="现在项目进行到哪一步？",
                runtime=runtime,
                stageCommand=None,
            ),
        )
        result = await service.execute_turn(
            turn["id"], runtime, signal=asyncio.Event()
        )
        completed = await service.load_turn(turn["id"])
    finally:
        await composition.shutdown()

    assert result.status is RunStatus.DONE
    assert completed["status"] == "completed"
    assert completed["assistantContent"] == "当前项目处于来源分析阶段。"
    assert completed["operationId"] is None
    assert completed["taskId"] is None
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE project_id = 'project-entry'"
    ) == {"count": 0}
    persisted = await temp_db.fetch_one(
        "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    )
    assert json.loads(persisted["binding_attributes_json"])[
        "interactionKind"
    ] == "ordinary"


@pytest.mark.asyncio
async def test_queued_ordinary_conversation_cancels_without_operation(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (3, 'ordinary cancel', 'screenplay', 'project-entry')"
    )
    runtime = await _runtime(temp_db)
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="ordinary-cancel-test"
        )
        turn = await service.submit_turn(
            command_id="ordinary-cancel-command",
            project_id="project-entry",
            request=SimpleNamespace(
                sessionId=3,
                content="先不用回答",
                runtime=runtime,
                stageCommand=None,
            ),
        )
        receipt = await service.cancel_turn(
            turn["id"], command_id="ordinary-cancel-receipt"
        )
    finally:
        await composition.shutdown()

    assert receipt["status"] == "canceled"
    assert receipt["operationId"] is None
    assert receipt["operationStatus"] is None


async def _runtime(db):
    config = {
        "id": "screenplay-model",
        "apiProvider": "openai",
        "name": "fixture",
        "apiKey": "fixture-key",
        "baseUrl": "http://example.test/v1",
        "contextWindow": "128k",
        "profileMaxGenerationTokens": 8_192,
        "maxGenerationTokens": 4_096,
        "supportsThinking": False,
        "thinkingOnly": False,
    }
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_model_configs", json.dumps([config])],
    )
    persisted = runtime_from_settings(config)
    return SimpleNamespace(
        apiKey=SecretStr(config["apiKey"]),
        modelConfigId=config["id"],
        apiProvider=persisted.apiProvider,
        baseURL=persisted.baseURL,
        contextWindow=persisted.contextWindow,
        options=persisted.options,
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_compiler_freezes_source_and_round_trips_recipe(temp_db) -> None:
    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="project-entry",
        stage_command=_command(),
    )

    assert ScreenplayHostRecipeSpec.from_mapping(spec.to_mapping()) == spec
    assert [part.kind.value for part in spec.parts] == [
        "document_section",
        "host_projection",
        "validation",
        "final_response",
    ]
    source = spec.parts[0].source_items[0]
    assert source.source_id == "chapter-entry"
    assert source.content_digest.startswith("sha256:")
    assert spec.parts[1].depends_on == ("sourceAnalysis:main",)


@pytest.mark.asyncio
async def test_compiler_allows_original_project_to_start_with_creative_brief(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_scope_json, format, approach, premise) "
        "VALUES ('original-entry', '原创短片', 'original', ?, '短片', "
        "'先推情节', '灯塔管理员收到来自明天的求救信号')",
        [json.dumps({"schemaVersion": 1, "mode": "whole_book"})],
    )

    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="original-entry",
        stage_command=_command("creativeBrief"),
    )

    assert spec.target_role == "creativeBrief"
    assert spec.parts[0].deliverable_revision_scope == {}
    assert spec.parts[0].source_revision_refs == ()


@pytest.mark.asyncio
async def test_compiler_builds_episode_shaped_scene_list_recipe(temp_db) -> None:
    await _accept_episode_revision(
        temp_db,
        role="structure",
        revision_id="revision-structure",
        episodes=(
            {"episodeNumber": 1, "title": "第一集"},
            {"episodeNumber": 2, "title": "第二集"},
        ),
    )

    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="project-entry",
        stage_command=_command("sceneList"),
    )

    assert [part.kind.value for part in spec.parts] == [
        "document_section",
        "document_section",
        "host_projection",
        "validation",
        "final_response",
    ]
    assert [part.episode_number for part in spec.parts[:2]] == [1, 2]
    assert dict(spec.parts[0].deliverable_revision_scope) == {
        "structure": "revision-structure",
    }
    assert spec.parts[2].depends_on == (
        "scene-list:episode:1",
        "scene-list:episode:2",
    )
    assert spec.max_parallelism == 1


@pytest.mark.asyncio
async def test_compiler_treats_projected_short_film_structure_as_episode_one(
    temp_db,
) -> None:
    await _accept_episode_revision(
        temp_db,
        role="structure",
        revision_id="revision-short-film-structure",
        episodes=(),
        document_payload={
            "schemaVersion": 1,
            "role": "structure",
            "parts": [{
                "partKey": "structure:main",
                "payload": {
                    "targetRole": "structure",
                    "content": {"beats": [{"beat": "停电后收到求救信号"}]},
                },
            }],
        },
    )

    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="project-entry",
        stage_command=_command("sceneList"),
    )

    assert [part.episode_number for part in spec.parts[:1]] == [1]
    assert spec.parts[0].id == "scene-list:episode:1"


@pytest.mark.asyncio
async def test_compiler_builds_draft_scene_and_episode_metadata_parts(temp_db) -> None:
    await _accept_episode_revision(
        temp_db,
        role="sceneList",
        revision_id="revision-scene-list",
        episodes=({
            "episodeNumber": 1,
            "title": "第一集",
            "scenes": [{"id": "ep01-s01"}, {"id": "ep01-s02"}],
        },),
    )

    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="project-entry",
        stage_command=_command("screenplayDraft"),
    )

    assert [part.kind.value for part in spec.parts] == [
        "draft_scene",
        "draft_scene",
        "episode_metadata",
        "host_projection",
        "validation",
        "final_response",
    ]
    assert [part.scene_id for part in spec.parts[:2]] == [
        "ep01-s01",
        "ep01-s02",
    ]
    assert spec.parts[2].depends_on == (
        "draft-scene:ep01-s01",
        "draft-scene:ep01-s02",
    )
    assert spec.max_parallelism == 1


@pytest.mark.asyncio
async def test_review_compiler_freezes_required_revision_heads(temp_db) -> None:
    for role in ("sceneList", "screenplayDraft"):
        deliverable = await temp_db.fetch_one(
            "SELECT id FROM screenplay_deliverables "
            "WHERE project_id = 'project-entry' AND role = ?",
            [role],
        )
        revision_id = f"revision-{role}"
        await temp_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, "
            "created_by) VALUES (?, 'project-entry', ?, 1, ?, 'user')",
            [revision_id, deliverable["id"], f"digest:{role}"],
        )
        await temp_db.execute(
            "INSERT INTO screenplay_project_heads "
            "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
            ["project-entry", deliverable["id"], revision_id],
        )

    spec = await compile_screenplay_host_recipe(
        temp_db,
        project_id="project-entry",
        stage_command=_command("review", "review"),
    )

    assert spec.parts[0].kind.value == "review_dimension"
    assert dict(spec.parts[0].deliverable_revision_scope) == {
        "sceneList": "revision-sceneList",
        "screenplayDraft": "revision-screenplayDraft",
    }
    assert spec.parts[0].source_items == ()


async def _accept_episode_revision(
    db,
    *,
    role: str,
    revision_id: str,
    episodes: tuple[dict[str, object], ...],
    document_payload: dict[str, object] | None = None,
) -> None:
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = 'project-entry' AND role = ?",
        [role],
    )
    await db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES (?, 'project-entry', ?, 1, ?, 'user')",
        [revision_id, deliverable["id"], f"digest:{role}"],
    )
    await db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, content_text, "
        "content_digest) VALUES (?, 'document', 'main', 0, ?, '', ?)",
        [
            revision_id,
            json.dumps(document_payload or {"episodes": list(episodes)}),
            f"document:{role}",
        ],
    )
    for position, episode in enumerate(episodes, start=1):
        await db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, content_text, "
            "content_digest) VALUES (?, 'episode', ?, ?, ?, '', ?)",
            [
                revision_id,
                str(episode["episodeNumber"]),
                position,
                json.dumps(episode),
                f"episode:{role}:{position}",
            ],
        )
    await db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES ('project-entry', ?, ?)",
        [deliverable["id"], revision_id],
    )


@pytest.mark.asyncio
async def test_partial_policy_cannot_reopen_retired_screenplay_runtime(temp_db) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.WRITING})),
    )
    try:
        service = ScreenplayReplacementExecutionService(temp_db, composition)
        assert service._composition.profile(
            "screenplay.purra-native.v1"
        ).id == "screenplay.purra-native.v1"
    finally:
        await composition.shutdown()


class _CapturingRuns:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        yield "fixture-update"


@pytest.mark.asyncio
async def test_entry_builds_host_recipe_and_uses_replacement_executor(temp_db) -> None:
    runtime = await _runtime(temp_db)
    runs = _CapturingRuns()
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementExecutionService(
            temp_db,
            composition,
            runs=runs,
        )
        request = await service.build_request(
            project_id="project-entry",
            session_id=7,
            turn_id="turn-entry",
            command_id="command-entry",
            prompt="分析原作并形成来源分析",
            stage_command=_command(),
            runtime=runtime,
        )
        updates = [update async for update in service.run(
            project_id="project-entry",
            session_id=7,
            turn_id="turn-entry",
            command_id="command-entry",
            prompt="分析原作并形成来源分析",
            stage_command=_command(),
            runtime=runtime,
            signal=asyncio.Event(),
        )]
    finally:
        await composition.shutdown()

    assert request.metadata["screenplayRecipe"]["targetRole"] == "sourceAnalysis"
    assert request.messages[0].content == "分析原作并形成来源分析"
    assert request.metadata["runtimeBinding"]["modelConfigId"] == "screenplay-model"
    assert "apiKey" not in json.dumps(thaw_json_mapping(
        request.metadata["runtimeBinding"]
    ))
    assert updates == ["fixture-update"]
    call = runs.calls[0]
    assert isinstance(call["long_task_executor"], ScreenplayReplacementUnitExecutor)
    assert call["options"].binding.namespace == (
        "purrtypos.screenplay.conversation_turn.v1"
    )
    assert call["options"].binding.command_id == "command-entry"
    assert call["options"].response_transaction_policy.public_presentation.value == "none"
    assert call["options"].provenance.execution_intent.output_contract == (
        "screenplay_revision_v1"
    )


@pytest.mark.asyncio
async def test_entry_resolves_unique_saved_binding_without_wire_config_id(temp_db) -> None:
    runtime = await _runtime(temp_db)
    delattr(runtime, "modelConfigId")
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        request = await ScreenplayReplacementExecutionService(
            temp_db,
            composition,
        ).build_request(
            project_id="project-entry",
            session_id=7,
            turn_id="turn-without-config-id",
            command_id="command-without-config-id",
            prompt="生成来源分析",
            stage_command=_command(),
            runtime=runtime,
        )
    finally:
        await composition.shutdown()

    assert request.metadata["runtimeBinding"]["modelConfigId"] == (
        "screenplay-model"
    )


@pytest.mark.asyncio
async def test_entry_rejects_ambiguous_identity_only_model_binding(temp_db) -> None:
    runtime = await _runtime(temp_db)
    delattr(runtime, "modelConfigId")
    row = await temp_db.fetch_one(
        "SELECT value FROM settings WHERE key = 'ai_model_configs'"
    )
    configs = json.loads(row["value"])
    configs.append({**configs[0], "id": "screenplay-model-copy"})
    await temp_db.execute(
        "UPDATE settings SET value = ? WHERE key = 'ai_model_configs'",
        [json.dumps(configs)],
    )
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        with pytest.raises(ScreenplayRuntimeBindingUnavailable):
            await ScreenplayReplacementExecutionService(
                temp_db,
                composition,
            ).build_request(
                project_id="project-entry",
                session_id=7,
                turn_id="turn-ambiguous-config",
                command_id="command-ambiguous-config",
                prompt="生成来源分析",
                stage_command=_command(),
                runtime=runtime,
            )
    finally:
        await composition.shutdown()


def _tool_call(call_id, name, arguments):
    return {
        "choices": [{
            "delta": {"tool_calls": [{
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }]},
            "finish_reason": "tool_calls",
        }],
    }


@pytest.mark.asyncio
async def test_conversation_runs_formal_source_analysis_through_real_core(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _runtime(temp_db)

    async def planner(_key, _messages, options, _provider, signal=None):
        assert signal is not None
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": json.dumps({
                "needsTodos": True,
                "title": "生成来源分析",
                "goal": "形成来源分析候选",
                "taskSpec": {
                    "goal": "形成来源分析候选",
                    "target": {},
                    "operation": "create",
                    "instruction": "读取原作并形成来源分析",
                    "constraints": [],
                    "preserve": [],
                    "deliverable": "sourceAnalysis",
                },
                "todos": [
                    {
                        "id": "source-read",
                        "title": "核对来源",
                        "type": "analyze",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                    {
                        "id": "source-analysis",
                        "title": "分析来源",
                        "type": "analyze",
                        "executor": "model",
                        "expectedTools": [],
                        "dependsOn": ["source-read"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "source-review",
                        "title": "复核分析",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "dependsOn": ["source-analysis"],
                        "riskLevel": "read",
                    },
                ],
            }, ensure_ascii=False)},
            "model": "fixture",
            "finish_reason": "stop",
        }

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        tools = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tools:
                yield _tool_call(
                    "read-source",
                    "readScreenplaySourceItemV1",
                    {"sourceId": "chapter-entry"},
                )
                return
            receipt = json.loads(tools[-1]["content"])
            if "artifactRef" not in receipt:
                assert receipt["content"] == "旧城在暴雨中停电。"
                yield _tool_call(
                    "submit-source-analysis",
                    "writeScreenplayCandidatePartV1",
                    {"candidate": {
                        "content": "原作以旧城暴雨停电作为主要危机。",
                    }},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "候选已提交。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(planner, runtime_stream),
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (7, 'replacement conversation', 'screenplay', 'project-entry')"
    )
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db,
            composition,
            owner_id="screenplay-conversation-test",
        )
        turn = await service.submit_turn(
            command_id="command-real-entry",
            project_id="project-entry",
            session_id=7,
            content="分析原作并形成来源分析",
            stage_command=_command(),
            runtime=runtime,
        )
        result = await service.execute_turn(
            turn["id"], runtime, signal=asyncio.Event()
        )
        projected_turn = await service.load_turn(turn["id"])
        chunks = await ScreenplayReplacementConversationQuery(
            temp_db,
            output_repository=composition.output_journal,
        ).list_chunks(
            project_id="project-entry",
            session_id=7,
            limit=500,
        )
    finally:
        await composition.shutdown()

    diagnostics = await temp_db.fetch_all(
        "SELECT event_type, payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? ORDER BY id",
        [result.run_id],
    )
    assert result.status is RunStatus.DONE, [
        (item["event_type"], json.loads(item["payload_json"] or "{}"))
        for item in diagnostics
        if item["event_type"] in {
            "planning.failed",
            "planning.result",
            "run.failed",
            "runtime.failed",
            "diagnostic",
        }
        or "invalid" in str(item["payload_json"])
    ]
    assert result.validated_result.startswith("screenplay-host-result-v1://")
    assert projected_turn["status"] == "completed"
    assert projected_turn["rootRunId"] == result.run_id
    assert projected_turn["operationId"]
    assert projected_turn["taskId"]
    assert projected_turn["resultRevisionId"]
    assert projected_turn["assistantContent"] == (
        "原作分析候选版本已生成，等待你审阅和接受。"
    )
    assert chunks["chunks"]
    assert {item["turnId"] for item in chunks["chunks"]} == {turn["id"]}
    assert {item["runRole"] for item in chunks["chunks"]} == {"root"}
    revision = await temp_db.fetch_one(
        "SELECT id, agent_task_id FROM screenplay_revisions "
        "WHERE project_id = 'project-entry'"
    )
    assert revision is not None
    assert revision["agent_task_id"]
    assert await temp_db.fetch_one(
        "SELECT source_id FROM screenplay_revision_source_refs "
        "WHERE revision_id = ?",
        [revision["id"]],
    ) == {"source_id": "chapter-entry"}
    usage = await temp_db.fetch_one(
        "SELECT invocation_count FROM ai_agent_long_task_usage "
        "WHERE task_id = ?",
        [revision["agent_task_id"]],
    )
    assert usage == {"invocation_count": 3}


@pytest.mark.asyncio
async def test_paused_conversation_resumes_same_task_on_new_root(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _runtime(temp_db)
    provider = {"available": False}

    async def planner(_key, _messages, options, _provider, signal=None):
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": json.dumps({
                "needsTodos": True,
                "title": "生成来源分析",
                "goal": "形成来源分析候选",
                "taskSpec": {
                    "goal": "形成来源分析候选",
                    "target": {},
                    "operation": "create",
                    "instruction": "读取原作并形成来源分析",
                    "constraints": [],
                    "preserve": [],
                    "deliverable": "sourceAnalysis",
                },
                "todos": [
                    {
                        "id": "source-read",
                        "title": "核对来源",
                        "type": "analyze",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                    {
                        "id": "source-analysis",
                        "title": "分析来源",
                        "type": "analyze",
                        "executor": "model",
                        "expectedTools": [],
                        "dependsOn": ["source-read"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "source-review",
                        "title": "复核分析",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "dependsOn": ["source-analysis"],
                        "riskLevel": "read",
                    },
                ],
            }, ensure_ascii=False)},
            "model": "fixture",
            "finish_reason": "stop",
        }

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        if not provider["available"]:
            raise ModelGatewayError(
                "provider is temporarily unavailable",
                code="provider_capacity_limited",
                retryable=True,
            )
        tools = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tools:
                yield _tool_call(
                    "read-source-resume",
                    "readScreenplaySourceItemV1",
                    {"sourceId": "chapter-entry"},
                )
                return
            receipt = json.loads(tools[-1]["content"])
            if "artifactRef" not in receipt:
                yield _tool_call(
                    "submit-source-analysis-resume",
                    "writeScreenplayCandidatePartV1",
                    {"candidate": {
                        "content": "恢复后形成的原作危机分析。",
                    }},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "恢复候选已提交。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(planner, runtime_stream),
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (7, 'replacement resume', 'screenplay', 'project-entry')"
    )
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db,
            composition,
            owner_id="screenplay-resume-test",
        )
        turn = await service.submit_turn(
            command_id="command-resume-entry",
            project_id="project-entry",
            session_id=7,
            content="分析原作并形成来源分析",
            stage_command=_command(),
            runtime=runtime,
        )
        paused_result = await service.execute_turn(
            turn["id"], runtime, signal=asyncio.Event()
        )
        paused_turn = await service.load_turn(turn["id"])
        operation = await temp_db.fetch_one(
            "SELECT id, revision, long_task_id, status "
            "FROM screenplay_agent_operations WHERE turn_id = ?",
            [turn["id"]],
        )
        assert operation["long_task_id"], (
            paused_result,
            paused_turn,
            operation,
            await temp_db.fetch_all(
                "SELECT id, status, created_by_run_id FROM ai_agent_long_tasks"
            ),
        )
        provider["available"] = True
        resumed_result = await service.resume_operation(
            operation["id"],
            command_id="resume-command-entry",
            expected_operation_revision=operation["revision"],
            runtime=runtime,
            signal=asyncio.Event(),
        )
        completed_turn = await service.load_turn(turn["id"])
        resume_receipt = await temp_db.fetch_one(
            "SELECT continuation_status, continuation_root_run_id, response_json "
            "FROM screenplay_agent_operation_commands WHERE command_id = ?",
            ["resume-command-entry"],
        )
        run_count = await temp_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE session_id = 7"
        )
        replay = await service.resume_operation(
            operation["id"],
            command_id="resume-command-entry",
            expected_operation_revision=operation["revision"],
            runtime=runtime,
            signal=asyncio.Event(),
        )
        replay_run_count = await temp_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE session_id = 7"
        )
    finally:
        await composition.shutdown()

    assert paused_result.status is RunStatus.CANCELED
    assert paused_turn["status"] == "paused"
    assert operation["status"] == "paused"
    assert resumed_result.status is RunStatus.DONE
    assert completed_turn["status"] == "completed"
    assert completed_turn["taskId"] == operation["long_task_id"]
    assert completed_turn["rootRunId"] != paused_result.run_id
    assert resume_receipt["continuation_status"] == "succeeded"
    assert resume_receipt["continuation_root_run_id"] == resumed_result.run_id
    assert json.loads(resume_receipt["response_json"])["status"] == "succeeded"
    assert replay["dispatchRequired"] is False
    assert replay["continuationRootRunId"] == resumed_result.run_id
    assert replay_run_count == run_count
    assert await temp_db.fetch_one(
        "SELECT relation FROM ai_agent_long_task_runs "
        "WHERE task_id = ? AND run_id = ?",
        [operation["long_task_id"], resumed_result.run_id],
    ) == {"relation": "continuation"}
