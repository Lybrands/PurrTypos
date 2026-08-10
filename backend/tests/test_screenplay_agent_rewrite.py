from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import pytest_asyncio

import application.screenplay_structured_call as screenplay_structured_call
from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelStream,
    ModelStreamChunk,
    ReasoningMode,
)
from purra.model_execution import ManagedModelExecutor, ManagedModelStream
from purra.errors import ModelGatewayError
from purra.output_budget import OutputBudgetPolicy
from application.screenplay_agent_service import (
    PlannedScreenplayIntent,
    ScreenplayAgentService,
    _task_failure,
)
from application.model_runtime import model_request_from_runtime
from application.screenplay_agent_stream import ScreenplayAgentChunkStore
from application.screenplay_agent_task_executor import (
    ScreenplayTaskModelCalls,
    ScreenplayTaskUnitExecutor,
)
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_agent_planner import SqliteScreenplayTaskResolver
from application.screenplay_structured_call import (
    StreamedModelText,
    StructuredModelResult,
)
from application.screenplay_progress_stream import JsonStringFieldProjector
from application.screenplay_tool_calling import ScreenplayCandidateRunResult
from application.screenplay_progress_stream import VISIBLE_STREAM_CHUNK_CHARS
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from database.screenplay_agent_schema import init_screenplay_agent_schema
from domains.screenplay_agent import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayIntentScope,
)
from domains.screenplay_agent.contracts import ScreenplayScopeKind
from exceptions import AppError
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest


pytestmark = pytest.mark.asyncio


async def test_execution_progress_is_projected_before_the_json_is_complete():
    projector = JsonStringFieldProjector({
        "executionSummary": "第 4 集创作推演：",
        "processSummary": "",
    })

    first = projector.feed(
        '{"executionSummary":"承接上一集，先建立人物目标'
    )
    second = projector.feed(
        '，再升级冲突。","scenes":[{"sceneId":"s1",'
        '"processSummary":"场景 s1 推演：让目标受阻并留下转折。",'
    )
    hidden = projector.feed('"sceneText":"正文不应进入过程区"}')

    assert first == "第 4 集创作推演：承接上一集，先建立人物目标"
    assert second == (
        "，再升级冲突。\n"
        "场景 s1 推演：让目标受阻并留下转折。\n"
    )
    assert hidden == ""


async def test_model_execution_progress_reaches_the_shared_stream_incrementally():
    raw = (
        '{"executionSummary":"先承接上一集的人物选择，再推动本集核心冲突。",'
        '"scenes":[{"processSummary":"场景 s1 推演：人物目标受阻并产生转折。",'
        '"sceneText":"这里是不会进入执行过程的完整正文"}]}'
    )

    class Gateway:
        async def stream(self, *_args):
            async def chunks():
                for start in range(0, len(raw), 7):
                    yield ModelStreamChunk(
                        content_delta=raw[start:start + 7],
                    )

            return ModelStream(chunks=chunks(), model="test-model")

    recorded_events: list[tuple[object, object]] = []

    class Controller:
        async def record_event(self, event_type, payload):
            recorded_events.append((event_type, payload))

    projected: list[str] = []
    raw_stream = await Gateway().stream()
    result = await screenplay_structured_call._stream_text(
        ManagedModelStream(
            chunks=raw_stream.chunks,
            model=raw_stream.model,
            output_budget=None,  # type: ignore[arg-type]
            call_parameters=(),
        ),
        Controller(),
        execution_progress_fields={
            "executionSummary": "本集创作推演：",
            "processSummary": "",
        },
        emit_execution_progress=lambda delta: _append_async(projected, delta),
    )

    assert len(projected) >= 3
    assert max(map(len, projected)) <= VISIBLE_STREAM_CHUNK_CHARS
    assert "".join(projected) == (
        "本集创作推演：先承接上一集的人物选择，再推动本集核心冲突。\n"
        "场景 s1 推演：人物目标受阻并产生转折。\n"
    )
    assert result.projected_progress is True
    assert result.content == raw
    assert recorded_events == []


async def test_raw_reasoning_is_diagnostic_only():
    reasoning = "先确认当前已完成的集数，再把范围限定为全部剩余剧集。"
    content = '{"executionSummary":"确定创作第 7 至 8 集。"}'

    class Gateway:
        async def stream(self, *_args):
            async def chunks():
                for start in range(0, len(reasoning), 6):
                    yield ModelStreamChunk(
                        reasoning_delta=reasoning[start:start + 6],
                    )
                for start in range(0, len(content), 6):
                    yield ModelStreamChunk(content_delta=content[start:start + 6])

            return ModelStream(chunks=chunks(), model="test-model")

    recorded_events: list[tuple[object, object]] = []

    class Controller:
        async def record_event(self, event_type, payload):
            recorded_events.append((event_type, payload))

    projected: list[str] = []
    diagnostics: list[dict[str, str]] = []
    raw_stream = await Gateway().stream()
    await screenplay_structured_call._stream_text(
        ManagedModelStream(
            chunks=raw_stream.chunks,
            model=raw_stream.model,
            output_budget=None,  # type: ignore[arg-type]
            call_parameters=(),
        ),
        Controller(),
        execution_progress_fields={"executionSummary": "请求理解："},
        emit_execution_progress=lambda delta: _append_async(projected, delta),
        emit_model_diagnostic=lambda chunk: _append_async(diagnostics, chunk),
    )

    visible = "".join(projected)
    assert reasoning not in visible
    assert "请求理解：确定创作第 7 至 8 集。" in visible
    assert "executionSummary" not in visible
    assert "{" not in visible
    assert recorded_events == []
    assert "".join(
        chunk.get("reasoningDelta", "") for chunk in diagnostics
    ) == reasoning


async def _append_async(target: list, value) -> None:
    target.append(value)


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _project_and_session(db):
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id="create-rewritten-agent-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Rewritten Agent",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    return projects, workspace, session


async def test_planning_context_contains_state_not_artifact_bodies(
    temp_db: DatabaseConnection,
):
    _, workspace, _ = await _project_and_session(temp_db)
    workspace["candidates"] = [{
        "id": "sprev-large-candidate",
        "role": "screenplayDraft",
        "revisionNo": 2,
        "summary": {"body": "候选正文" * 10_000},
    }]

    context = await ScreenplayAgentContextQuery(temp_db).planning_context(workspace)
    serialized = json.dumps(context, ensure_ascii=False)

    assert len(serialized) < 12_000
    assert "contentText" not in serialized
    assert '"content"' not in serialized
    assert context["project"]["title"] == "Rewritten Agent"
    assert "episodeState" in context


async def test_create_draft_continues_from_head_instead_of_older_candidate():
    workspace = {
        "workflow": {
            "heads": {"screenplayDraft": {"id": "head-draft-1-to-6"}},
        },
        "candidates": [{
            "id": "candidate-draft-1-to-3",
            "role": "screenplayDraft",
            "applicability": "current",
        }],
    }
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="继续创作全部剩余正文",
        requested_deliverable="screenplayDraft",
    )

    assert SqliteScreenplayTaskResolver._base_revision(
        workspace,
        "screenplayDraft",
        intent,
    ) == "head-draft-1-to-6"


async def test_revise_draft_uses_accepted_head_instead_of_older_candidate():
    workspace = {
        "workflow": {
            "heads": {"screenplayDraft": {"id": "head-draft-1-to-8"}},
        },
        "candidates": [{
            "id": "candidate-draft-1-to-3",
            "role": "screenplayDraft",
            "applicability": "current",
        }],
    }
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.REVISE,
        instruction="根据审阅报告修订完整剧本",
        requested_deliverable="screenplayDraft",
    )

    assert SqliteScreenplayTaskResolver._base_revision(
        workspace,
        "screenplayDraft",
        intent,
    ) == "head-draft-1-to-8"


async def test_revise_current_stage_covers_every_existing_draft_episode():
    class Context:
        async def available_episode_numbers(self, project_id, **kwargs):
            assert project_id == "project-1"
            assert kwargs["draft_revision_id"] == "head-draft-1-to-8"
            return {
                "sceneList": tuple(range(1, 9)),
                "draft": tuple(range(1, 9)),
                "remaining": (),
            }

    resolver = object.__new__(SqliteScreenplayTaskResolver)
    resolver._context = Context()
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.REVISE,
        instruction="逐项解决审阅报告中的十个问题并修订完整剧本",
        scope=ScreenplayIntentScope(kind=ScreenplayScopeKind.CURRENT_STAGE),
        requested_deliverable="screenplayDraft",
    )

    assert await resolver._resolve_episodes(
        "project-1",
        intent,
        draft_revision_id="head-draft-1-to-8",
    ) == tuple(range(1, 9))


def _request(session_id: int, content: str):
    return SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session_id,
        "content": content,
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "planner-model"},
            "contextWindow": "128k",
        },
    })


async def test_screenplay_structured_calls_enable_supported_provider_json_mode():
    runtime = _request(1, "测试 JSON mode").runtime
    runtime.options.update({
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash",
        "response_format": {"type": "text"},
    })
    runtime.baseURL = "https://api.deepseek.com"

    structured = model_request_from_runtime(runtime, json_object_output=True)
    ordinary = model_request_from_runtime(runtime)

    assert structured.options["response_format"] == {"type": "json_object"}
    assert "response_format" not in ordinary.options


async def test_screenplay_task_preserves_managed_model_failure_code():
    code, message = _task_failure(ModelGatewayError(
        "model output is incomplete",
        code="model_output_truncated",
        retryable=False,
    ))

    assert code == "model_output_truncated"
    assert "未形成完整候选稿" in message
    assert "不完整结果未被保存" in message


async def test_screenplay_task_retries_only_the_uncommitted_truncated_fragment():
    executor = object.__new__(ScreenplayTaskUnitExecutor)

    assert executor.is_retryable(ModelGatewayError(
        "provider stream reached its output limit",
        code="model_output_truncated",
        retryable=True,
    )) is True
    assert executor.is_retryable(ModelGatewayError(
        "provider truncated one uncommitted tool call",
        code="tool_call_truncated",
        retryable=True,
    )) is True
    assert executor.is_retryable(ModelGatewayError(
        "model selected an input outside the scoped read contract",
        code="tool_input_invalid",
        retryable=True,
    )) is True
    assert executor.is_retryable(ModelGatewayError(
        "model emitted malformed function arguments",
        code="invalid_tool_arguments_json",
        retryable=True,
    )) is True
    assert executor.is_retryable(ModelGatewayError(
        "model exhausted a bounded research loop",
        code="max_model_rounds",
        retryable=True,
    )) is True
    assert executor.is_retryable(ModelGatewayError(
        "provider stream disconnected",
        code="upstream_stream_interrupted",
        retryable=True,
    )) is True


class _Planner:
    def __init__(self, intent: ScreenplayIntent) -> None:
        self.intent = intent
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return PlannedScreenplayIntent(self.intent, "run-semantic-planner")


class _ModelGateway:
    def __init__(self, api_key: str) -> None:
        assert api_key == "secret"
        self.invocations = []
        self.calls = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "model": invocation.request.model,
        }

    async def stream(self, messages, invocation, signal=None):
        del signal
        self.calls.append((tuple(messages), invocation))
        self.invocations.append(invocation)

        async def chunks():
            yield ModelStreamChunk(finish_reason=ModelFinishReason.STOP)

        return ModelStream(chunks=chunks(), model=invocation.request.model)

    async def complete(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="{}"),
            model=invocation.request.model,
            finish_reason=ModelFinishReason.STOP,
        )


_TEST_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_test",
    base_tokens=200,
    per_work_unit_tokens=0,
    safety_factor=1,
    hard_cap_tokens=200,
)


def _model_executor_factory(api_key: str) -> ManagedModelExecutor:
    return ManagedModelExecutor(_ModelGateway(api_key))


async def test_structured_model_repair_is_persisted_as_one_diagnostic_run(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)
    malformed = '{"contentText":"角色说"少亲自来"。"}'
    responses = iter((malformed, '```json\n{"answer":"ok"}\n```'))

    async def fake_stream(*args, **kwargs):
        del args, kwargs
        return StreamedModelText(content=next(responses))

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", fake_stream)
    runtime = _request(session["id"], "测试结构化输出").runtime
    runtime.options["thinking"] = {"type": "enabled"}
    gateway = _ModelGateway("secret")
    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        model_executor_factory=lambda _api_key: ManagedModelExecutor(gateway),
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试结构化输出",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="repair-test",
        phase="screenplay_test",
        output_policy=_TEST_OUTPUT_POLICY,
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    assert result.value == {"answer": "ok"}
    assert [invocation.reasoning_mode for invocation in gateway.invocations] == [
        ReasoningMode.DEFAULT,
        ReasoningMode.DISABLED,
    ]
    assert [[message.role.value for message in messages] for messages, _ in gateway.calls] == [
        ["system", "user"],
        ["system", "user"],
    ]
    assert gateway.calls[1][0][1].content == malformed
    assert "question" not in str(gateway.calls[1][0][1].content)
    assert await temp_db.fetch_one(
        "SELECT status, binding_namespace FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    ) == {
        "status": "done",
        "binding_namespace": "screenplay.agent.test",
    }
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'model.call_recorded'",
        [result.run_id],
    ) == {"count": 2}
    assert await temp_db.fetch_all(
        "SELECT json_extract(payload_json, '$.parameters.messageCount') AS count "
        "FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'model.call_recorded' ORDER BY id",
        [result.run_id],
    ) == [{"count": 2}, {"count": 2}]


async def test_truncated_structured_output_is_never_repaired_or_replayed(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)

    class TruncatedGateway(_ModelGateway):
        async def stream(self, messages, invocation, signal=None):
            del messages, signal
            self.invocations.append(invocation)

            async def chunks():
                yield ModelStreamChunk(content_delta='{"answer":"unfinished')
                yield ModelStreamChunk(finish_reason=ModelFinishReason.LENGTH)

            return ModelStream(chunks=chunks(), model=invocation.request.model)

    gateway = TruncatedGateway("secret")
    with pytest.raises(ModelGatewayError) as captured:
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            model_executor_factory=lambda _api_key: ManagedModelExecutor(gateway),
        ).run_json(
            runtime=_request(session["id"], "测试截断输出").runtime,
            session_id=session["id"],
            prompt="测试截断输出",
            system_instruction="只输出 JSON",
            user_payload={"question": "test"},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="truncated-output-test",
            phase="screenplay_test",
            output_policy=_TEST_OUTPUT_POLICY,
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    assert captured.value.code == "model_output_truncated"
    assert len(gateway.invocations) == 1
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE event_type = 'model.call_recorded'"
    ) == {"count": 1}


async def test_structured_model_renews_its_core_run_lease_during_slow_generation(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)

    async def slow_stream(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(0.65)
        return StreamedModelText(content='{"answer":"ok"}')

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", slow_stream)
    runtime = _request(session["id"], "测试慢速结构化输出").runtime
    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        model_executor_factory=_model_executor_factory,
        lease_duration_ms=300,
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试慢速结构化输出",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="slow-lease-test",
        phase="screenplay_test",
        output_policy=_TEST_OUTPUT_POLICY,
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    assert result.value == {"answer": "ok"}
    assert await temp_db.fetch_one(
        "SELECT status, execution_owner_id, lease_expires_at_ms "
        "FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    ) == {
        "status": "done",
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }


async def test_structured_model_preserves_the_frontend_thinking_option(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)
    reasoning_modes = []

    gateway = _ModelGateway("secret")

    async def capture_stream(_stream, _controller, **_kwargs):
        return StreamedModelText(content='{"answer":"ok"}')

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", capture_stream)
    runtime = _request(session["id"], "测试思考配置").runtime
    runtime.options["thinking"] = {"type": "enabled"}

    await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        model_executor_factory=lambda _api_key: ManagedModelExecutor(gateway),
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试思考配置",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="thinking-option-test",
        phase="screenplay_test",
        output_policy=_TEST_OUTPUT_POLICY,
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    reasoning_modes.extend(item.reasoning_mode for item in gateway.invocations)
    assert reasoning_modes == [ReasoningMode.DEFAULT]


async def test_screenplay_policies_reserve_output_for_visible_content_after_reasoning():
    from application.output_budget_policies import (
        SCREENPLAY_DELIVERABLE_OUTPUT_POLICY,
        SCREENPLAY_EPISODE_OUTPUT_POLICY,
        SCREENPLAY_INTENT_OUTPUT_POLICY,
        SCREENPLAY_REVIEW_OUTPUT_POLICY,
    )
    from purra.output_budget import (
        ModelOutputCapabilities,
        ThinkingTokenAccounting,
        resolve_output_budget,
    )

    assert SCREENPLAY_INTENT_OUTPUT_POLICY.reasoning_reserve_tokens == 1_024
    assert SCREENPLAY_EPISODE_OUTPUT_POLICY.reasoning_reserve_tokens == 12_000
    assert SCREENPLAY_DELIVERABLE_OUTPUT_POLICY.reasoning_reserve_tokens == 8_000
    assert SCREENPLAY_REVIEW_OUTPUT_POLICY.reasoning_reserve_tokens == 24_000
    assert SCREENPLAY_REVIEW_OUTPUT_POLICY.hard_cap_tokens == 40_000

    budget = resolve_output_budget(
        policy=SCREENPLAY_REVIEW_OUTPUT_POLICY,
        capabilities=ModelOutputCapabilities(
            max_output_tokens=393_216,
            thinking_token_accounting=ThinkingTokenAccounting.INCLUDED,
        ),
        context_window_tokens=1_000_000,
        thinking_enabled=True,
    )

    assert budget.target_tokens == 8_000
    assert budget.reasoning_reserve_tokens == 24_000
    assert budget.effective_tokens == 33_600


async def test_model_failure_keeps_its_run_id_in_the_shared_diagnostic_stream(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)

    async def fail_stream(*_args, **_kwargs):
        raise RuntimeError("provider disconnected")

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", fail_stream)
    runtime = _request(session["id"], "测试模型失败诊断").runtime

    with pytest.raises(RuntimeError, match="provider disconnected"):
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            model_executor_factory=_model_executor_factory,
        ).run_json(
            runtime=runtime,
            session_id=session["id"],
            prompt="测试模型失败诊断",
            system_instruction="只输出 JSON",
            user_payload={"question": "test"},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="failure-diagnostic-test",
            phase="screenplay_test",
            output_policy=_TEST_OUTPUT_POLICY,
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    rows = await temp_db.fetch_all(
        "SELECT run_id, chunk_json FROM screenplay_agent_chunks ORDER BY id"
    )
    assert [json.loads(row["chunk_json"]) for row in rows] == [
        {"model": "planner-model"},
        {"error": "provider disconnected"},
    ]
    assert rows[0]["run_id"] == rows[1]["run_id"]


async def test_reasoning_only_output_is_not_accepted_as_business_output(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)

    async def reasoning_stream(*_args, **_kwargs):
        return StreamedModelText(content="")

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", reasoning_stream)
    with pytest.raises(ValueError, match="complete JSON object"):
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            model_executor_factory=_model_executor_factory,
        ).run_json(
            runtime=_request(session["id"], "测试 reasoning JSON").runtime,
            session_id=session["id"],
            prompt="测试 reasoning JSON",
            system_instruction="只输出 JSON",
            user_payload={},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="reasoning-json-test",
            phase="screenplay_intent_planning",
            output_policy=_TEST_OUTPUT_POLICY,
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )
    rows = await temp_db.fetch_all(
        "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
    )
    chunks = [json.loads(row["chunk_json"]) for row in rows]
    assert chunks[0] == {"model": "planner-model"}
    assert not any("delta" in chunk for chunk in chunks)


async def test_generated_screenplay_body_never_becomes_a_chat_delta(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    _, _, session = await _project_and_session(temp_db)

    async def generation_stream(*_args, **_kwargs):
        return StreamedModelText(
            content=(
                '{"executionSummary":"先核对前集连续性，再按场景目标推进冲突。",'
                '"sceneText":"这里是完整剧本正文"}'
            ),
        )

    monkeypatch.setattr(screenplay_structured_call, "_stream_text", generation_stream)
    await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        model_executor_factory=_model_executor_factory,
    ).run_json(
        runtime=_request(session["id"], "测试正文隔离").runtime,
        session_id=session["id"],
        prompt="测试正文隔离",
        system_instruction="只输出 JSON",
        user_payload={},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="body-isolation-test",
        phase="screenplay_episode_generation",
        output_policy=_TEST_OUTPUT_POLICY,
        repair_instruction="修复 JSON",
        validate=lambda value: value,
        project_execution=lambda value: (
            value["executionSummary"],
            f"sceneText: {value['sceneText']}",
        ),
    )

    rows = await temp_db.fetch_all(
        "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
    )
    chunks = [json.loads(row["chunk_json"]) for row in rows]
    assert chunks[0] == {"model": "planner-model"}
    commentary = [
        chunk["commentaryDelta"]
        for chunk in chunks[1:]
        if "commentaryDelta" in chunk
    ]
    assert "".join(commentary) == "先核对前集连续性，再按场景目标推进冲突。\n"
    assert max(map(len, commentary)) <= VISIBLE_STREAM_CHUNK_CHARS


async def test_screenplay_stream_replays_materialized_shared_chunks(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-stream-test",
    )
    turn = await repository.begin_turn(
        command_id="stream-turn",
        project_id=project_id,
        session_id=session["id"],
        content="创作下一集",
        runtime_profile={"provider": "openai", "model": "test"},
    )
    chunks = ScreenplayAgentChunkStore(temp_db)
    await chunks.append(
        project_id=project_id,
        session_id=session["id"],
        turn_id=turn["id"],
        run_id="run-screenplay-stream",
        chunk={"commentaryDelta": "先分析现有剧情"},
    )
    await chunks.append(
        project_id=project_id,
        session_id=session["id"],
        turn_id=turn["id"],
        run_id="run-screenplay-stream",
        chunk={"delta": "正文增量"},
    )

    page = await chunks.list_chunks(
        project_id=project_id,
        session_id=session["id"],
    )

    assert page["hasMore"] is False
    assert page["nextCursor"] > 0
    assert page["chunks"][0] == {
        "cursor": page["chunks"][0]["cursor"],
        "runId": "run-screenplay-stream",
        "turnId": turn["id"],
        "taskId": None,
        "userContent": "创作下一集",
        "model": "test",
        "turnCreatedAt": page["chunks"][0]["turnCreatedAt"],
        "chunk": {"commentaryDelta": "先分析现有剧情"},
        "createdAt": page["chunks"][0]["createdAt"],
    }
    assert page["chunks"][1] == {
        "cursor": page["nextCursor"],
        "runId": "run-screenplay-stream",
        "turnId": turn["id"],
        "taskId": None,
        "userContent": "创作下一集",
        "model": "test",
        "turnCreatedAt": page["chunks"][1]["turnCreatedAt"],
        "chunk": {"delta": "正文增量"},
        "createdAt": page["chunks"][1]["createdAt"],
    }


async def test_legacy_chunks_that_leaked_model_json_are_discarded(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-chunk-migration-test",
    )
    turn = await repository.begin_turn(
        command_id="legacy-chunk-turn",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="生成候选稿",
        runtime_profile={"provider": "openai", "model": "test"},
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_chunks "
        "(project_id, session_id, turn_id, protocol_version, chunk_json) "
        "VALUES (?, ?, ?, 1, ?)",
        [
            workspace["project"]["id"],
            session["id"],
            turn["id"],
            '{"commentaryDelta":"{\\\"sceneText\\\":\\\"正文\\\"}"}',
        ],
    )

    await init_screenplay_agent_schema(temp_db)

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_chunks "
        "WHERE protocol_version < 2"
    ) == {"count": 0}


async def test_restart_exposes_an_abandoned_turn_as_a_terminal_failure(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-agent-before-turn-restart",
    )
    turn = await repository.begin_turn(
        command_id="queued-before-restart",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="分析一下当前剧本",
        runtime_profile={"provider": "openai", "model": "test"},
    )

    recovered = await repository.recover_after_restart()
    snapshot = await repository.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )

    assert recovered == (turn["id"],)
    assert snapshot["turns"][0]["status"] == "failed"
    assert snapshot["turns"][0]["error"]["code"] == "screenplay_agent_restarted"


async def _install_head(db, project_id: str, role: str, content: dict) -> str:
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables WHERE project_id = ? AND role = ?",
        [project_id, role],
    )
    assert deliverable is not None
    revision_id = f"head-{role}"
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    await db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "summary_json, created_by) VALUES (?, ?, ?, 1, ?, '{}', 'test')",
        [revision_id, project_id, deliverable["id"], digest],
    )
    await db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, "
        "content_text, content_digest) VALUES (?, 'document', 'main', 0, ?, '', ?)",
        [revision_id, encoded, digest],
    )
    episode_payloads = (
        content.get("episodes", [])
        if role == "structure"
        else [
            {"episodeNumber": number, "scenes": scenes}
            for number, scenes in sorted({
                int(scene["episodeNumber"]): [
                    item for item in content.get("scenes", [])
                    if int(item["episodeNumber"]) == int(scene["episodeNumber"])
                ]
                for scene in content.get("scenes", [])
            }.items())
        ] if role == "sceneList" else []
    )
    for position, episode in enumerate(episode_payloads, start=1):
        number = int(episode.get("episodeNumber") or episode.get("number"))
        payload = json.dumps(episode, ensure_ascii=False, sort_keys=True)
        await db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES (?, 'episode', ?, ?, ?, '', ?)",
            [
                revision_id,
                str(number),
                position,
                payload,
                hashlib.sha256(payload.encode()).hexdigest(),
            ],
        )
    await db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
        [project_id, deliverable["id"], revision_id],
    )
    return revision_id


class _StructuredDraftModels:
    async def run_json(self, **kwargs):
        payload = kwargs["user_payload"]
        number = int(payload["episodeNumber"])
        return StructuredModelResult({
            "episodeNumber": number,
            "title": f"第 {number} 集",
            "continuitySummary": f"第 {number} 集连续性",
            "scenes": [{
                "sceneId": scene["id"],
                "sceneText": f"{scene['heading']}\n\n第 {number} 集正文",
            } for scene in payload["scenePlan"]["scenes"]],
        }, f"run-draft-{number}")


class _CheckpointingToolCalls:
    def __init__(self, *, fail_once_key: str | None = None) -> None:
        self.fail_once_key = fail_once_key
        self.failed = False
        self.calls: list[tuple[str, str, ReasoningMode]] = []
        self.user_payloads: list[dict] = []

    async def run_candidate(self, **kwargs):
        context = kwargs["domain_context"]
        part_type = context.expected_part_type
        part_key = context.expected_part_key
        self.calls.append((part_type, part_key, kwargs["reasoning_mode"]))
        self.user_payloads.append(dict(kwargs["user_payload"]))
        if part_key == self.fail_once_key and not self.failed:
            self.failed = True
            raise ModelGatewayError(
                "fragment reached its output limit",
                code="model_output_truncated",
                retryable=True,
            )
        if part_type == "scene":
            template = kwargs.get("host_candidate_template")
            assert isinstance(template, dict)
            raw_scene_text = (
                f'{part_key} 的完整正文，保留英文引号 "台词" 和花括号 {{线索}}'
            )
            hosted = {**template, "sceneText": raw_scene_text}
            candidate = {
                "artifactId": f"artifact-{part_key}",
                "payload": hosted,
                "contentText": raw_scene_text,
            }
        elif part_type == "episode_metadata":
            assert kwargs.get("host_candidate_template") is None
            candidate = {
                "artifactId": f"artifact-metadata-{part_key}",
                "payload": {
                    "episodeNumber": int(part_key),
                    "title": f"第 {part_key} 集",
                    "executionSummary": "按场景顺序完成本集并保留连续性。",
                    "continuitySummary": f"第 {part_key} 集连续性",
                },
                "contentText": "",
            }
        elif part_type == "review_episode":
            episode_number = int(part_key)
            candidate = {
                "artifactId": f"artifact-review-{part_key}",
                "payload": {
                    "title": f"第 {part_key} 集审阅",
                    "executionSummary": "核对本集场景目标、冲突和连续性。",
                    "contentJson": {
                        "verdict": "revise" if episode_number == 1 else "ready",
                        "issues": ([{
                            "id": "issue-1",
                            "severity": "major",
                            "description": "场景转折需要更明确。",
                            "sceneIds": [f"scene-{episode_number}"],
                        }] if episode_number == 1 else []),
                    },
                },
                "contentText": f"第 {part_key} 集审阅正文",
            }
        elif part_type == "scene_list_episode":
            episode_number = int(part_key)
            candidate = {
                "artifactId": f"artifact-scene-list-{part_key}",
                "payload": {
                    "title": f"第 {part_key} 集场景表",
                    "executionSummary": "按本集结构目标规划场景推进。",
                    "contentJson": {"scenes": [{
                        "id": f"scene-{episode_number}",
                        "episodeNumber": episode_number,
                        "heading": "咖啡馆·夜",
                        "objective": "确认对方来意",
                        "conflict": "双方互不信任",
                        "turn": "旧证物出现",
                        "synopsis": "人物在试探中发现共同线索。",
                    }]},
                },
                "contentText": f"第 {part_key} 集场景表正文",
            }
        else:
            raise AssertionError(f"unexpected part type: {part_type}")
        validator = kwargs.get("validate_candidate")
        if validator is not None:
            candidate = dict(validator(candidate))
        return ScreenplayCandidateRunResult(
            run_id=f"run-{part_type}-{part_key}-{len(self.calls)}",
            candidate=candidate,
        )


class _IncrementalEpisodeContext:
    async def episode_manifest(self, project_id, episode_number):
        assert project_id and episode_number == 1
        return {
            "sceneListId": "scene-list-head",
            "sceneIds": ("scene-1", "scene-2"),
        }

    async def episode_writing_context(
        self,
        project_id,
        episode_number,
        *,
        draft_revision_id=None,
    ):
        assert project_id and episode_number == 1
        assert draft_revision_id == "draft-head"
        return {
            "sceneListId": "scene-list-head",
            "scenePlans": {
                "scene-1": {"id": "scene-1", "objective": "建立危机"},
                "scene-2": {"id": "scene-2", "objective": "完成转折"},
            },
            "currentDraftScenes": {
                "scene-1": "scene-1 的旧稿",
                "scene-2": "scene-2 的旧稿",
            },
            "previousEpisodeContinuity": None,
            "reviewRevisionId": "review-head",
            "reviewIssues": [{
                "id": "issue-1",
                "severity": "major",
                "description": "本集需要统一格式并压缩篇幅。",
                "relatedSceneIds": ["scene-1"],
                "crossEpisodeSceneIds": [],
            }],
            "acceptedGuidance": {
                "creativeBrief": {"fields": {"format": "竖屏短剧"}},
                "structureEpisode": {"number": 1, "summary": "危机出现"},
            },
        }


async def test_episode_generation_checkpoints_scenes_and_resumes_after_truncation(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls(fail_once_key="scene-2")
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _IncrementalEpisodeContext()
    task = {
        "id": "task-incremental-episode",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-incremental-episode",
        "targetRole": "screenplayDraft",
    }
    unit = {
        "id": "draft-episode-1",
        "kind": "generate_episode_draft",
        "input": {
            "episodeNumber": 1,
            "instruction": "创作第一集",
            "baseRevisionId": "draft-head",
        },
    }

    with pytest.raises(ModelGatewayError, match="output limit"):
        await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_task_outputs "
        "WHERE task_id = ?",
        [task["id"]],
    ) == {"count": 1}

    # Simulate a backend restart: the second executor has no in-memory state
    # from the interrupted attempt and must discover scene-1 in SQLite.
    resumed_tool_calls = _CheckpointingToolCalls()
    resumed_executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=resumed_tool_calls,  # type: ignore[arg-type]
    )
    resumed_executor._context = _IncrementalEpisodeContext()
    result = await resumed_executor.execute(
        task=task,
        unit=unit,
        runtime=object(),
    )

    assert result["episodeDraft"]["sceneIds"] == ["scene-1", "scene-2"]
    assert result["episodeDraft"]["contentText"] == (
        'scene-1 的完整正文，保留英文引号 "台词" 和花括号 {线索}\n\n'
        'scene-2 的完整正文，保留英文引号 "台词" 和花括号 {线索}'
    )
    assert [key for _, key, _ in tool_calls.calls] == [
        "scene-1",
        "scene-2",
    ]
    assert [key for _, key, _ in resumed_tool_calls.calls] == [
        "scene-2",
        "1",
    ]
    assert all(
        mode is ReasoningMode.DISABLED
        for _, _, mode in tool_calls.calls + resumed_tool_calls.calls
    )
    first_scene_payload = tool_calls.user_payloads[0]
    assert first_scene_payload["scenePlan"]["id"] == "scene-1"
    assert first_scene_payload["currentDraftScene"] == "scene-1 的旧稿"
    assert first_scene_payload["reviewRevisionId"] == "review-head"
    assert first_scene_payload["revisionIssues"][0] == {
        "id": "issue-1",
        "severity": "major",
        "description": "本集需要统一格式并压缩篇幅。",
        "relatedSceneIds": ["scene-1"],
        "crossEpisodeSceneIds": [],
        "directlyReferencesCurrentScene": True,
    }
    assert "acceptedGuidance" in first_scene_payload
    assert len(result["sourceRunIds"]) == 3
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_task_outputs "
        "WHERE task_id = ?",
        [task["id"]],
    ) == {"count": 3}


class _IncrementalReviewContext:
    async def available_episode_numbers(self, project_id, **kwargs):
        assert project_id and kwargs["draft_revision_id"] == "draft-head"
        return {"draft": (1, 2), "sceneList": (1, 2), "remaining": ()}

    async def episode_context(self, project_id, episode_number, **kwargs):
        assert project_id and kwargs["draft_revision_id"] == "draft-head"
        return {"currentDraft": {"sceneIds": [f"scene-{episode_number}"]}}


async def test_review_generation_checkpoints_each_episode_and_aggregates_host_side(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _IncrementalReviewContext()
    task = {
        "id": "task-incremental-review",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-incremental-review",
        "targetRole": "review",
    }
    unit = {"id": "generate-deliverable", "input": {"instruction": "审阅全剧"}}

    result = await executor._generate_review_incrementally(
        task=task,
        unit=unit,
        runtime=object(),
        signal=None,
        reviewed_draft_id="draft-head",
    )

    assert result["contentJson"]["reviewedEpisodes"] == [1, 2]
    assert result["contentJson"]["verdict"] == "revise"
    assert result["contentJson"]["issues"][0]["id"] == "episode-1:issue-1"
    assert len(result["sourceRunIds"]) == 2


async def test_scene_list_generation_checkpoints_each_structure_episode(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    task = {
        "id": "task-incremental-scene-list",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-incremental-scene-list",
        "targetRole": "sceneList",
    }
    unit = {"id": "generate-deliverable", "input": {"instruction": "生成场景表"}}

    result = await executor._generate_scene_list_incrementally(
        task=task,
        unit=unit,
        runtime=object(),
        signal=None,
        structure_id="structure-head",
        episode_numbers=(1, 2),
    )

    assert result["contentJson"]["structureId"] == "structure-head"
    assert [scene["episodeNumber"] for scene in result["contentJson"]["scenes"]] == [
        1,
        2,
    ]
    assert len(result["sourceRunIds"]) == 2


async def test_production_resolver_and_executor_publish_one_native_candidate(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    await _install_head(temp_db, project_id, "creativeBrief", {
        "documentKind": "creative_brief",
        "fields": {"approach": "人物驱动", "premise": "意外重逢"},
    })
    await _install_head(temp_db, project_id, "structure", {
        "documentKind": "episode_outline",
        "episodes": [
            {"number": 1, "id": "episode-1", "title": "重逢"},
            {"number": 2, "id": "episode-2", "title": "追问"},
            {"number": 3, "id": "episode-3", "title": "选择"},
        ],
    })
    scene_list_id = await _install_head(temp_db, project_id, "sceneList", {
        "documentKind": "scene_list",
        "scenes": [
            {"id": "scene-1", "episodeNumber": 1, "heading": "咖啡馆·夜"},
            {"id": "scene-2", "episodeNumber": 2, "heading": "车站·晨"},
            {"id": "scene-3", "episodeNumber": 3, "heading": "码头·黄昏"},
        ],
    })
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="把接下来两集写完，每集结尾留下新的问题",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=2,
        ),
    )
    executor = ScreenplayTaskModelCalls(
        temp_db,
        model_executor_factory=_model_executor_factory,
    )
    executor._models = _StructuredDraftModels()

    def unit_executor_factory(runtime):
        unit = ScreenplayTaskUnitExecutor(
            temp_db,
            runtime=runtime,
            model_executor_factory=_model_executor_factory,
        )
        unit._delegate = executor
        return unit

    service = ScreenplayAgentService(
        temp_db,
        owner_id="production-screenplay-task-test",
        planner=_Planner(intent),
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=unit_executor_factory,
        projects=projects,
    )
    request = _request(session["id"], "把接下来两集写完，每集结尾留下新的问题")
    turn = await service.submit_turn(
        command_id="production-next-two",
        project_id=project_id,
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=project_id,
        session_id=session["id"],
    )
    task = snapshot["tasks"][0]
    assert task["status"] == "completed"
    revision = await projects.get_revision(task["resultRevisionId"], view="full")
    assert revision["createdBy"] == "agent"
    assert revision["parts"][0]["payload"]["sceneListId"] == scene_list_id
    assert [part["key"] for part in revision["parts"]] == ["main", "1", "2"]
    assert await temp_db.fetch_one(
        "SELECT agent_task_id FROM screenplay_revisions WHERE id = ?",
        [revision["id"]],
    ) == {"agent_task_id": task["id"]}
    continuation = await SqliteScreenplayTaskResolver(temp_db).resolve(
        workspace=await projects.get_workspace(project_id),
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="继续创作下一集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=1,
            ),
        ),
    )
    assert continuation.base_revision_id == revision["id"]
    assert continuation.episode_numbers == (3,)

    continuation_service = ScreenplayAgentService(
        temp_db,
        owner_id="production-screenplay-continuation-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="继续创作下一集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=1,
            ),
        )),
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=unit_executor_factory,
        projects=projects,
    )
    continuation_turn = await continuation_service.submit_turn(
        command_id="production-next-one-after-candidate",
        project_id=project_id,
        request=_request(session["id"], "继续创作下一集"),
    )
    await continuation_service.execute_turn(
        continuation_turn["id"],
        _request(session["id"], "继续创作下一集").runtime,
    )
    continued_snapshot = await continuation_service.get_snapshot(
        project_id=project_id,
        session_id=session["id"],
    )
    continued_revision = await projects.get_revision(
        continued_snapshot["tasks"][1]["resultRevisionId"],
        view="full",
    )
    assert continued_revision["parentRevisionId"] == revision["id"]
    assert [part["key"] for part in continued_revision["parts"]] == [
        "main",
        "1",
        "2",
        "3",
    ]

    await service.truncate_from_turn(turn["id"])
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_revisions WHERE id = ?",
        [revision["id"]],
    ) is None
