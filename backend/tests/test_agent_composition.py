from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import (
    AgentRunResult,
    ApprovalRequest,
    ApprovalStatus,
    ResponseConstraints,
    RunStatus,
    ToolCall,
    ToolExecutionMode,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.tools import InMemoryApprovalGateway
from application.agent_composition import (
    AgentComposition,
    set_agent_composition,
)
from application.request_mapping import (
    context_window_tokens,
    to_writing_agent_request,
    writing_run_options,
)
from application.sse_mapping import core_update_to_sse_chunk
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.contracts import WritingDomainContext
from routers.ai import (
    _stream_composed_agent,
    chat_stream,
    resolve_pending_tool_approval,
)
from schemas.ai import ChatStreamRequest, ResolveToolApprovalRequest


BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        set_agent_composition(None)
        await db.close()


async def _collect(response) -> list[dict]:
    events: list[dict] = []
    async for raw in response.body_iterator:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        stripped = text.strip()
        if stripped.startswith("{"):
            events.append(json.loads(stripped))
        else:
            for line in text.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
        if any(event.get("done") for event in events):
            break
    return events


def test_request_mapping_supports_kimi_256k_context_window():
    assert context_window_tokens("256k") == 256_000


def test_request_mapping_hides_writing_fields_inside_domain_context():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model", "model_profile": "minimax:MiniMax-M3"},
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        selectedMemoryIds=[1],
        contextWindow="64k",
    )
    request = to_writing_agent_request(
        body,
        {"model": "model", "baseURL": "https://example.test/v1"},
    )
    domain = WritingDomainContext.from_core_context(request.domain_context)
    options = writing_run_options(request, {"max_tokens": 2048})

    assert request.context_window == 64_000
    assert request.model.options["baseURL"] == "https://example.test/v1"
    assert request.model.profile_id == "minimax:MiniMax-M3"
    assert "model_profile" not in request.model.options
    assert domain.book_id == "book-1"
    assert domain.chapter_id == "chapter-1"
    assert domain.selected_memory_ids == (1,)
    assert options.output_reserve_tokens == 2048
    assert options.context_claims[0].name == "writing_retrieval"


@pytest.mark.parametrize(
    ("user_text", "expected_count"),
    [
        (
            "读取当前章节，给出一个 150 字以内的摘要和两个可改进之处。"
            "只分析，不要修改。",
            2,
        ),
        (
            "对照当前章节与关联大纲，找出两处不一致，并给出最小修改建议。",
            None,
        ),
        ("请继续分析剧情走向。", None),
    ],
)
def test_writing_run_options_carries_host_response_constraints(
    user_text: str,
    expected_count: int | None,
):
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": user_text}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        associatedOutlineIds=["outline-1"],
    )
    request = to_writing_agent_request(body, {"model": "model"})

    options = writing_run_options(request, {"max_tokens": 2_048})

    assert isinstance(options.response_constraints, ResponseConstraints)
    assert (
        options.response_constraints.exact_top_level_item_count
        == expected_count
    )


@pytest.mark.parametrize(
    (
        "book_id",
        "enable_agent_tools",
        "chapter_id",
        "associated_outline_ids",
    ),
    [
        (None, True, None, None),
        ("book-1", False, "chapter-1", ["outline-1"]),
        ("book-1", True, None, ["outline-1"]),
        ("book-1", True, "chapter-1", None),
        ("book-1", True, "   ", ["outline-1"]),
        ("book-1", True, "chapter-1", ["   "]),
        ("book-1", True, "   ", ["   "]),
    ],
)
def test_unavailable_host_material_does_not_force_refusal_into_items(
    book_id,
    enable_agent_tools,
    chapter_id,
    associated_outline_ids,
):
    body = ChatStreamRequest(
        messages=[{
            "role": "user",
            "content": (
                "对照当前章节与关联大纲，找出两处不一致并给出最小修改建议。"
            ),
        }],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=enable_agent_tools,
        bookId=book_id,
        chapterId=chapter_id,
        associatedOutlineIds=associated_outline_ids,
    )
    request = to_writing_agent_request(body, {"model": "model"})

    options = writing_run_options(request, {"max_tokens": 2_048})

    assert options.response_constraints.exact_top_level_item_count is None
    assert options.response_validators == ()


@pytest.mark.asyncio
async def test_composition_injects_model_judge_only_for_atomic_continuity(
    temp_db: DatabaseConnection,
):
    def _request(user_text: str):
        body = ChatStreamRequest(
            messages=[{"role": "user", "content": user_text}],
            apiKey="key",
            apiProvider="openai",
            options={"model": "model"},
            enableAgentTools=True,
            bookId="book-1",
            chapterId="chapter-1",
            associatedOutlineIds=["outline-1"],
        )
        return to_writing_agent_request(body, {"model": "model"})

    composition = AgentComposition(temp_db)
    p5_request = _request(
        "对照当前章节与关联大纲，找出两处不一致，并给出最小修改建议。"
    )
    p3_request = _request("读取当前章节，给出一个150字以内的摘要。")
    p5_judges = composition.create_response_judges("key", p5_request)
    p3_judges = composition.create_response_judges("key", p3_request)
    core = composition.create_core("key")
    p5_options = writing_run_options(
        p5_request,
        {"max_tokens": 2_048},
        response_judges=p5_judges,
    )
    p3_options = writing_run_options(
        p3_request,
        {"max_tokens": 2_048},
        response_judges=p3_judges,
    )

    assert len(p5_options.response_judges) == 1
    assert p3_options.response_judges == ()
    assert not hasattr(core, "_response_judges")


@pytest.mark.asyncio
async def test_composed_core_consumes_configured_approval_timeout(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    import application.agent_composition as composition_module

    monkeypatch.setattr(
        composition_module,
        "AGENT_APPROVAL_TIMEOUT_SECONDS",
        3_600,
    )
    composition = AgentComposition(temp_db)
    core = composition.create_core("key")

    assert core._tool_executor._limits.approval_timeout_seconds == 3_600


@pytest.mark.asyncio
async def test_composition_filters_child_tools_from_business_role_policy(
    temp_db: DatabaseConnection,
):
    composition = AgentComposition(temp_db)
    role = composition.agent_role_registry.require("researcher")
    request = to_writing_agent_request(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "读取当前章节"}],
            apiKey="key",
            apiProvider="openai",
            options={"model": "model"},
            enableAgentTools=True,
            bookId="book-1",
            chapterId="chapter-1",
            chatAgentMode="agent",
        ),
        {"model": "model"},
    )
    core = composition.create_core(
        "key",
        allowed_tool_modes=role.allowed_tool_modes,
    )
    enabled = core._tool_catalog.enabled_names(request)
    registration_by_name = {
        item.schema.name: item
        for item in core._tool_catalog.registrations()
    }

    assert enabled
    assert "getChapterContent" in enabled
    assert "editChapterContent" not in enabled
    assert all(
        registration_by_name[name].policy.mode is ToolExecutionMode.READ
        for name in enabled
    )


def test_request_mapping_rejects_caller_owned_tool_contract():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
    )

    with pytest.raises(ValueError, match="caller-owned tools"):
        to_writing_agent_request(
            body,
            {
                "model": "model",
                "tools": [{"type": "function", "function": {"name": "external"}}],
            },
        )


@pytest.mark.parametrize(
    ("body_tools", "body_options"),
    [
        ([{"type": "function", "function": {"name": "external"}}], {}),
        (None, {"tools": [{"type": "function"}]}),
        (None, {"tool_choice": "none"}),
    ],
)
def test_request_mapping_rejects_caller_contract_from_original_body(
    body_tools,
    body_options,
):
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model", **body_options},
        tools=body_tools,
        enableAgentTools=True,
        bookId="book-1",
    )

    with pytest.raises(ValueError, match="caller-owned tools"):
        to_writing_agent_request(body, {"model": "model"})


@pytest.mark.parametrize("caller_tools", [{}, "", False])
def test_request_mapping_does_not_silently_drop_invalid_caller_tool_shapes(
    caller_tools,
):
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
    )

    with pytest.raises(ValueError, match="caller-owned tools"):
        to_writing_agent_request(
            body,
            {"model": "model", "tools": caller_tools},
        )


def test_anthropic_thinking_output_reserve_matches_provider_adjustment():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="anthropic",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
    )
    provider_options = {
        "model": "model",
        "max_tokens": 1_000,
        "thinking": {"type": "enabled"},
    }
    request = to_writing_agent_request(body, provider_options)

    options = writing_run_options(request, provider_options)

    assert options.output_reserve_tokens == 3_072


def test_sse_mapping_preserves_public_run_and_domain_event_names():
    started = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            run_id="run-1",
            payload={"status": "running"},
        ),
        model="model",
    )
    effect = core_update_to_sse_chunk(
        AgentEvent(
            type="writing.proposed_setting_diff",
            run_id="run-1",
            payload={"kind": "character"},
        ),
        model="model",
    )
    done = core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-1",
            status=RunStatus.DONE,
            model="provider-resolved-model",
        ),
        model="model",
    )
    cached = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.TOOL_CALL_COMPLETED,
            run_id="run-1",
            payload={"index": 2, "fromCache": True},
        ),
        model="model",
    )
    delegation_created = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.DELEGATION_CREATED,
            run_id="run-1",
            payload={
                "delegationId": "delegation-1",
                "agentRole": "researcher",
                "status": "queued",
            },
        ),
        model="model",
    )
    delegation_updated = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.DELEGATION_COMPLETED,
            run_id="run-1",
            payload={
                "delegationId": "delegation-1",
                "agentRole": "researcher",
                "status": "done",
                "resultSummary": "verified",
            },
        ),
        model="model",
    )

    assert started == {
        "agentRunStarted": {"runId": "run-1", "status": "running"},
    }
    assert effect == {"proposedSettingDiff": {"kind": "character"}}
    assert done == {"done": True, "model": "provider-resolved-model"}
    assert cached == {"toolIndexCompleted": 2, "toolFromCache": True}
    assert delegation_created == {
        "agentDelegationCreated": {
            "runId": "run-1",
            "delegationId": "delegation-1",
            "agentRole": "researcher",
            "status": "queued",
        },
    }
    assert delegation_updated == {
        "agentDelegationUpdated": {
            "runId": "run-1",
            "delegationId": "delegation-1",
            "agentRole": "researcher",
            "status": "done",
            "resultSummary": "verified",
        },
    }


@pytest.mark.asyncio
async def test_custom_tools_fail_closed_without_calling_the_model(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    model_called = False
    custom_tools = [{
        "type": "function",
        "function": {
            "name": "externalSearch",
            "description": "Search externally",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    async def _unexpected_model_call(*_args, **_kwargs):
        nonlocal model_called
        model_called = True
        raise AssertionError("unsupported caller tools must fail before model I/O")

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _unexpected_model_call,
    )
    set_agent_composition(AgentComposition(temp_db))

    response = await chat_stream(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "搜索"}],
            apiKey="key",
            apiProvider="openai",
            options={"model": "model"},
            tools=custom_tools,
            enableAgentTools=True,
            bookId="book-1",
            chatAgentMode="agent",
            contextWindow="200k",
        ),
    )
    events = await _collect(response)

    assert model_called is False
    assert events == [{
        "error": (
            "当前 Agent 不支持调用方自定义 tools 或 tool_choice；"
            "请求已停止，未调用模型或执行工具。"
        ),
    }]
    assert not any("agentRunStarted" in event for event in events)


@pytest.mark.asyncio
async def test_composed_route_reuses_observed_required_tool_choice_capability():
    from infrastructure.models.provider_capabilities import ProviderCapabilityCache

    class _FakeCore:
        def __init__(self, owner):
            self._owner = owner

        async def run(self, _request, *, options, signal):
            assert signal is not None
            self._owner.options.append(options)
            if len(self._owner.options) == 1:
                self._owner.unsupported_callback()
            yield AgentEvent(
                type=CoreEventType.RUN_STARTED,
                run_id=f"run-{len(self._owner.options)}",
                payload={"status": "running"},
            )
            yield AgentRunResult(
                run_id=f"run-{len(self._owner.options)}",
                status=RunStatus.DONE,
                model="resolved-model",
            )

    class _FakeComposition:
        def __init__(self):
            self.options = []
            self.released: list[str] = []
            self.unsupported_callback = lambda: None
            self.provider_capabilities = ProviderCapabilityCache()

        def create_core(
            self,
            _api_key,
            *,
            on_required_tool_choice_unsupported=None,
        ):
            self.unsupported_callback = on_required_tool_choice_unsupported or (lambda: None)
            return _FakeCore(self)

        def create_response_judges(self, _api_key, _request):
            return ()

        def observe_event(self, _event):
            return None

        async def release_run(self, run_id):
            self.released.append(run_id)

    composition = _FakeComposition()
    set_agent_composition(composition)  # type: ignore[arg-type]
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "执行一个足够长的写作任务"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
    )
    provider_options = {
        "model": "model",
        "baseURL": "https://example.test/v1",
    }

    first = [chunk async for chunk in _stream_composed_agent(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
    )]
    second = [chunk async for chunk in _stream_composed_agent(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
    )]

    capability_key = composition.provider_capabilities.key(
        api_provider="openai",
        base_url="https://example.test/v1",
        model="model",
        thinking_enabled=False,
    )
    assert composition.provider_capabilities.required_tool_choice_is_unsupported(
        capability_key
    )
    assert composition.options[0].force_planned_tool_choice is True
    assert composition.options[1].force_planned_tool_choice is False
    assert first[-1] == {"done": True, "model": "resolved-model"}
    assert second[-1] == {"done": True, "model": "resolved-model"}
    assert composition.released == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_composed_route_uses_complete_agent_core(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _create_chat_stream(
        key,
        messages,
        options,
        api_provider,
        signal=None,
    ):
        assert key == "key"
        assert api_provider == "openai"
        assert options["model"] == "model"
        assert options.get("tools")
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _create_chat_stream,
    )
    set_agent_composition(AgentComposition(temp_db))

    response = await chat_stream(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "简短"}],
            apiKey="key",
            apiProvider="openai",
            options={"model": "model"},
            tools=[],
            enableAgentTools=True,
            bookId="book-1",
            chatAgentMode="agent",
            contextWindow="200k",
        ),
    )
    events = await _collect(response)

    assert any("agentRunStarted" in event for event in events)
    assert any("contextBudget" in event for event in events)
    assert "".join(event.get("delta", "") for event in events) == "完成"
    assert any("agentRunCompleted" in event for event in events)
    assert events[-1] == {"done": True, "model": "model"}


@pytest.mark.asyncio
async def test_composed_approval_is_resolved_through_existing_http_contract(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    round_number = 0

    async def _create_chat_stream(
        _key,
        _messages,
        _options,
        _api_provider,
        signal=None,
    ):
        nonlocal round_number
        round_number += 1
        assert signal is not None

        async def _stream():
            if round_number == 1:
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-delete",
                                "type": "function",
                                "function": {
                                    "name": "deleteCharacter",
                                    "arguments": '{"characterId":7}',
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
            else:
                yield {
                    "choices": [{
                        "delta": {"content": "已保留人物"},
                        "finish_reason": "stop",
                    }],
                }

        return {"stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _create_chat_stream,
    )
    set_agent_composition(AgentComposition(temp_db))
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "删除"}],
        apiKey="key",
        apiProvider="openai",
        options={"model": "model"},
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
        contextWindow="200k",
    )

    chunks: list[dict] = []
    async for chunk in _stream_composed_agent(
        body=body,
        api_key="key",
        provider_options={"model": "model"},
        signal=asyncio.Event(),
    ):
        chunks.append(chunk)
        approval = chunk.get("toolApprovalRequired")
        if approval:
            resolved = await resolve_pending_tool_approval(
                approval["approvalId"],
                ResolveToolApprovalRequest(approved=False),
            )
            assert resolved == {
                "success": True,
                "data": {"status": "rejected"},
            }

    assert round_number == 2
    assert any("toolApprovalRequired" in chunk for chunk in chunks)
    assert any(
        item["content"].find("approval_rejected") >= 0
        for chunk in chunks
        for item in chunk.get("toolResults", [])
    )
    assert "".join(chunk.get("delta", "") for chunk in chunks) == (
        "您已拒绝审批；操作未执行，相关数据仍保留。"
    )
    assert chunks[-1] == {"done": True, "model": "model"}


@pytest.mark.asyncio
async def test_approval_endpoint_resolves_composition_owned_request():
    class _FakeComposition:
        async def resolve_approval(self, approval_id: str, approved: bool):
            assert approval_id == "composition-approval"
            assert approved is False
            return ApprovalStatus.REJECTED

    set_agent_composition(_FakeComposition())  # type: ignore[arg-type]
    try:
        result = await resolve_pending_tool_approval(
            "composition-approval",
            ResolveToolApprovalRequest(approved=False),
        )
    finally:
        set_agent_composition(None)

    assert result == {
        "success": True,
        "data": {"status": "rejected"},
    }


@pytest.mark.asyncio
async def test_composition_shutdown_cancels_all_live_approvals(
    temp_db: DatabaseConnection,
):
    class _RecordingSink:
        def __init__(self):
            self.events: list[AgentEvent] = []

        async def emit(self, event: AgentEvent) -> None:
            self.events.append(event)

    gateway = InMemoryApprovalGateway()
    composition = AgentComposition(
        temp_db,
        writing=object(),  # type: ignore[arg-type]
        approval_gateway=gateway,
    )
    sink = _RecordingSink()
    task = asyncio.create_task(gateway.request(
        "run-shutdown",
        ApprovalRequest(
            tool_call=ToolCall(
                id="call-delete",
                name="deleteCharacter",
                arguments_json='{"characterId":7}',
            ),
            title="删除人物",
            risk_level="destructive",  # type: ignore[arg-type]
            summary='{"characterId":7}',
            timeout_seconds=1,
        ),
        sink,
    ))
    while not sink.events:
        await asyncio.sleep(0)
    approval_id = str(sink.events[0].payload["approvalId"])
    composition.observe_event(sink.events[0])

    await composition.shutdown()
    result = await task

    assert result.status is ApprovalStatus.CANCELED
    assert gateway.pending_count() == 0
    assert await composition.resolve_approval(approval_id, True) is None

    late_sink = _RecordingSink()
    late = await gateway.request(
        "run-after-shutdown",
        ApprovalRequest(
            tool_call=ToolCall(
                id="late-call",
                name="deleteCharacter",
                arguments_json="{}",
            ),
            title="删除人物",
            risk_level="destructive",  # type: ignore[arg-type]
            summary="{}",
            timeout_seconds=1,
        ),
        late_sink,
    )
    assert late.status is ApprovalStatus.CANCELED
    assert late.approval_id is None
    assert late_sink.events == []
    with pytest.raises(RuntimeError, match="shut down"):
        composition.create_core("key")
