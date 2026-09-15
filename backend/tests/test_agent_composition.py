from __future__ import annotations
from application.model_runtime import context_window_tokens

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from purra.context_strategies import ContextStrategy
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    ApprovalRequest,
    ApprovalStatus,
    DomainContext,
    ModelRequest,
    ReasoningMode,
    ResponseConstraints,
    RunBinding,
    RuntimeLimits,
    RunStatus,
    ToolCall,
)
from purra.events import AgentEvent, CoreEventType
from purra.api import (
    AgentCoreRunOptions,
    AgentModelTaskRunner,
    PlanningMode,
)
from purra.model_invocation import ModelInvocationContext
from purra.model_protocol import (
    FeatureSupport,
    generic_capability_snapshot,
)
from purra.tools import InMemoryApprovalGateway
from purra.tools import InMemoryToolCatalog
from purra.recovery import RecoveryPolicy
from application.agent_composition import (
    AgentComposition,
    set_agent_composition,
)
from application.composition_factory import create_agent_composition
from application.agent_profile_registry import (
    AgentProfile,
    AgentProfileRegistry,
    StaticAgentProfile,
)
from application.agent_run_service import AgentRunService
from application.model_runtime import with_adapter_public_progress
from application.shared_agent_context import SharedAgentContextProvider
from application.conversation_compaction import ConversationCompactionService
from application.request_mapping import (
    to_writing_agent_request,
    writing_run_options,
)
from application.sse_mapping import core_update_to_sse_chunk
from database.connection import DatabaseConnection
from dependencies import set_db
from agents.writing.request_contract import WritingRequestContext
from agents.writing.profile import WRITING_REPLACEMENT_PROFILE_ID
from routers.ai import (
    _stream_composed_agent,
    chat_stream,
    resolve_pending_tool_approval,
)
from schemas.ai import ChatStreamRequest, ResolveToolApprovalRequest
from tests.support.canonical_wire import (
    assert_raw_canonical_wire,
    provider_text,
    runtime_events,
)
from tests.support.planning_stream import route_planning_stream


BACKEND_DIR = Path(__file__).resolve().parent.parent


class _FakeAgentProfile:
    id = "fake"
    domain_namespace = "test.fake"
    requires_agent_tree = False
    max_delegated_agents = 0

    def __init__(self, factory_dependencies: dict[str, object]):
        self.factory_dependencies = factory_dependencies
        self.prepare_input: AgentRunRequest | None = None
        self.context_provider = object()
        self.context_factory = lambda _model_tasks: self.context_provider
        self.judge_policy = object()
        self.admission = object()
        self.dispatcher = object()
        self.dispatcher_dependencies: dict[str, object] | None = None
        self.adapter = SimpleNamespace(
            planning_policy=None,
            execution_state_factory=None,
            tool_catalog=InMemoryToolCatalog(()),
            context_provider=None,
            runtime_limits=RuntimeLimits(max_run_generation_tokens=None),
            recovery_policy=RecoveryPolicy(),
        )

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest:
        self.prepare_input = request
        return replace(request, metadata={"prepared": True})

    def context_provider_factory(self):
        return self.context_factory

    def response_judge_policies(self, _request: AgentRunRequest):
        return (self.judge_policy,)

    def task_admission(self):
        return self.admission

    def create_long_task_dispatcher(self, **dependencies):
        self.dispatcher_dependencies = dependencies
        return self.dispatcher

    def clear_active_executions(self) -> None:
        return None


def _fake_request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="test"),),
        model=ModelRequest(
            provider="openai",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="openai:test-model",
                max_generation_tokens=4_096,
            ),
        ),
        domain_context=DomainContext(namespace="test.fake"),
    )


def _writing_composition(
    db: DatabaseConnection,
    **kwargs,
) -> AgentComposition:
    return create_agent_composition(db, **kwargs)


def _fixture_model_options() -> dict[str, object]:
    return {
        "model": "model",
        "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
        "max_generation_tokens": 2_048,
    }


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    }, ensure_ascii=False)


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


@pytest.mark.asyncio
async def test_composition_consumes_explicit_profile_capabilities(
    temp_db: DatabaseConnection,
):
    created: list[_FakeAgentProfile] = []

    def factory(**dependencies):
        profile = _FakeAgentProfile(dependencies)
        created.append(profile)
        return profile

    composition = AgentComposition(
        temp_db,
        profile_factories=(factory,),
    )
    request = _fake_request()

    prepared = await composition.prepare_request(request)
    policies = composition.create_response_judge_policies(request)
    core = composition.create_core(
        "key",
        agent_profile="fake",
        long_task_executor=object(),
    )

    profile = created[0]
    assert composition.agent_profile_ids == ("fake",)
    assert prepared.metadata == {"prepared": True}
    assert policies == (profile.judge_policy,)
    assert core._preset is not None
    assert core._preset.id == "fake"
    assert core._context_provider_factory is not profile.context_factory
    shared_provider = core._context_provider_factory(object())
    assert isinstance(shared_provider, SharedAgentContextProvider)
    assert shared_provider.provider is profile.context_provider
    assert core._execution_profile.task_admission_evaluator is profile.admission
    assert core._execution_profile.long_task_dispatcher is profile.dispatcher
    assert core._task_admission_evaluator is profile.admission
    assert core._long_task_dispatcher is profile.dispatcher
    assert core._task_orchestration is not None
    assert set(profile.factory_dependencies) == {
        "db",
        "artifact_continuity",
            "long_task_repository",
            "execution_lease_store",
            "memory_resource",
        }
    assert set(profile.dispatcher_dependencies or {}) == {
        "long_task_repository",
        "executor",
    }


@pytest.mark.asyncio
async def test_composition_uses_a_profile_request_runtime_envelope(
    temp_db: DatabaseConnection,
):
    profile = _FakeAgentProfile({})

    def runtime_limits_for_request(_request):
        return replace(
            profile.adapter.runtime_limits,
            max_model_invocation_attempts=137,
        )

    profile.runtime_limits_for_request = runtime_limits_for_request
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **_dependencies: profile,),
    )

    core = composition.create_core_for_request(_fake_request(), "key")

    assert core._preset.runtime_limits.max_model_invocation_attempts == 137
    await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_create_core_requires_an_explicit_profile(
    temp_db: DatabaseConnection,
):
    composition = AgentComposition(
        temp_db,
        profile_factories=(
            lambda **dependencies: _FakeAgentProfile(dependencies),
        ),
    )
    try:
        with pytest.raises(TypeError, match="agent_profile"):
            composition.create_core("key")
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_explicit_planned_run_without_planner_fails_closed_before_provider(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    provider_calls = 0

    async def provider_must_not_run(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("planning_unavailable must fail before Provider")

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        provider_must_not_run,
    )
    context_calls = 0

    class ContextProvider:
        async def build_context(self, request, budget, signal=None):
            nonlocal context_calls
            del request, budget, signal
            context_calls += 1
            raise AssertionError("planning_unavailable must fail before context")

    profile = _FakeAgentProfile({})
    profile.context_factory = lambda _model_tasks: ContextProvider()
    composition = AgentComposition(
        temp_db,
        profile_factories=(
            lambda **_dependencies: profile,
        ),
    )
    core = composition.create_core("key", agent_profile="fake")
    try:
        request = replace(
            _fake_request(),
            planning_mode=PlanningMode.PLANNED,
        )

        result = await (await core.submit(
            request,
            options=AgentCoreRunOptions(
                result_capacity_target_tokens=256,
            ),
        )).wait()

        assert result.status is RunStatus.FAILED
        assert result.error == "planning_unavailable"
        assert provider_calls == 0
        assert context_calls == 0
        assert core._planning_enabled is False
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_closes_the_lifespan_memory_resource_after_cores(
    temp_db: DatabaseConnection,
):
    closed = []

    class MemoryResource:
        async def close(self):
            closed.append("memory")

    composition = AgentComposition(
        temp_db,
        profile_factories=(
            lambda **dependencies: _FakeAgentProfile(dependencies),
        ),
        memory_resource=MemoryResource(),
    )
    core = composition.create_core("key", agent_profile="fake")
    original_close = core.close

    async def close_core():
        closed.append("core")
        await original_close()

    core.close = close_core
    await composition.shutdown()

    assert closed == ["core", "memory"]


@pytest.mark.asyncio
async def test_static_profile_defaults_have_no_side_effects(
    temp_db: DatabaseConnection,
):
    profile = StaticAgentProfile(
        id="fake",
        domain_namespace="test.fake",
        adapter=_FakeAgentProfile({}).adapter,
    )
    request = _fake_request()

    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **_kwargs: profile,),
    )

    assert isinstance(profile, AgentProfile)
    assert await composition.prepare_request(request) is request
    assert composition.create_response_judge_policies(request) == ()
    assert profile.context_provider_factory() is None
    assert profile.task_admission() is None
    assert profile.create_long_task_dispatcher() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected"),
    (
        ({}, FeatureSupport.SUPPORTED),
        ({"responseAudience": "internal"}, FeatureSupport.UNKNOWN),
        (
            {"responseAudience": "internal", "progressAudience": "public"},
            FeatureSupport.SUPPORTED,
        ),
    ),
)
async def test_composition_enables_real_provider_progress_only_for_public_scopes(
    temp_db: DatabaseConnection,
    metadata,
    expected,
):
    profile = StaticAgentProfile(
        id="fake",
        domain_namespace="test.fake",
        adapter=_FakeAgentProfile({}).adapter,
    )
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **_kwargs: profile,),
    )
    request = replace(_fake_request(), tools_enabled=True, metadata=metadata)
    try:
        prepared = await composition.prepare_request(request)
    finally:
        await composition.shutdown()

    assert prepared.model.capability_snapshot.protocol.public_progress is expected


@pytest.mark.asyncio
async def test_child_request_does_not_inherit_root_public_output_audience(
    temp_db: DatabaseConnection,
):
    profile = _FakeAgentProfile({})
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **_kwargs: profile,),
    )
    request = replace(
        _fake_request(),
        tools_enabled=True,
        metadata={
            "parentRunId": "root-run",
            "responseAudience": "public",
            "progressAudience": "public",
        },
    )
    try:
        prepared = await composition.prepare_request(request)
    finally:
        await composition.shutdown()

    assert prepared.metadata["responseAudience"] == "internal"
    assert prepared.metadata["progressAudience"] == "internal"
    assert profile.prepare_input is not None
    assert profile.prepare_input.metadata["responseAudience"] == "internal"
    assert profile.prepare_input.metadata["progressAudience"] == "internal"
    assert (
        prepared.model.capability_snapshot.protocol.public_progress
        is FeatureSupport.UNKNOWN
    )


def test_profile_registry_stores_the_profile_as_the_registration():
    profile = StaticAgentProfile(
        id="fake",
        domain_namespace="test.fake",
        adapter=_FakeAgentProfile({}).adapter,
    )
    registry = AgentProfileRegistry((profile,))

    assert registry.require("fake") is profile
    assert registry.for_request(_fake_request()) is profile


@pytest.mark.asyncio
async def test_product_profiles_all_enforce_public_tool_operation_presentation(
    temp_db: DatabaseConnection,
):
    composition = create_agent_composition(temp_db)
    try:
        assert composition.agent_profile_ids == (
            "writing.purra-native.v1",
            "novel_analysis.scalable.v2",
            "screenplay.purra-native.v1",
        )
        for profile_id in composition.agent_profile_ids:
            registrations = tuple(
                composition.profile(profile_id).adapter.tool_catalog.registrations()
            )
            assert registrations
            assert all(
                item.schema.display_names.get("zh-CN")
                and item.operation_display_params is not None
                for item in registrations
            )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_product_composition_registers_product_profiles(
    temp_db: DatabaseConnection,
):
    composition = create_agent_composition(temp_db)
    try:
        assert composition.agent_profile_ids == (
            "writing.purra-native.v1",
            "novel_analysis.scalable.v2",
            "screenplay.purra-native.v1",
        )
        for profile_id in composition.agent_profile_ids:
            core = composition.create_core("key", agent_profile=profile_id)
            provider = core._context_provider
            if core._context_provider_factory is not None:
                provider = core._context_provider_factory(AgentModelTaskRunner(
                    core._model_invocations,
                    ModelInvocationContext(run_id=f"{profile_id}-policy-test"),
                    _fake_request().model,
                ))
            assert isinstance(provider, SharedAgentContextProvider)
            assert (
                core._preset.component_bindings["contextProvider"].revision
                == "2"
            )
    finally:
        await composition.shutdown()


def test_product_composition_rejects_additional_profile_factories(
    temp_db: DatabaseConnection,
):
    with pytest.raises(TypeError, match="profile_factories"):
        create_agent_composition(
            temp_db,
            profile_factories=(),
        )


def test_product_composition_rejects_retired_writing_skills_dir(
    temp_db: DatabaseConnection,
):
    with pytest.raises(TypeError, match="skills_dir"):
        create_agent_composition(temp_db, skills_dir=BACKEND_DIR / "skills")


@pytest.mark.asyncio
async def test_agent_run_service_leaves_delegation_to_composition():
    from infrastructure.models.provider_capabilities import (
        ProviderCapabilityCache,
    )

    class _CoreCreated(Exception):
        pass

    captured: dict[str, object] = {}

    class _Composition:
        provider_capabilities = ProviderCapabilityCache()

        def create_response_judge_policies(self, _request):
            return ()

        async def prepare_request(self, request):
            return request

        def bind_run_profile(self, _request, options):
            return options

        def create_core_for_request(self, request, _api_key, **kwargs):
            captured["request"] = request
            captured["kwargs"] = kwargs
            raise _CoreCreated

    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "研究明确子任务"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
    )
    request = to_writing_agent_request(body, {"model": "model"})
    updates = AgentRunService(_Composition()).run(  # type: ignore[arg-type]
        request=request,
        api_key="key",
        options=writing_run_options(request, {"model": "model"}),
        signal=asyncio.Event(),
    )

    with pytest.raises(_CoreCreated):
        await anext(updates)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "delegation_repository" not in kwargs
    assert "delegation_policy" not in kwargs


def test_request_mapping_supports_kimi_256k_context_window():
    assert context_window_tokens("256k") == 256_000


def test_request_mapping_hides_writing_fields_inside_domain_context():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
            "thinking": {"type": "enabled"},
        },
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        locale="zh-Hans-CN",
        selectedMemoryIds=[1],
        contextWindow="64k",
    )
    request = to_writing_agent_request(
        body,
        {
            "model": "deepseek-v4-flash",
            "baseURL": "https://api.deepseek.com",
        },
    )
    domain = WritingRequestContext.from_core_context(request.domain_context)
    options = writing_run_options(request, {"max_generation_tokens": 2048})

    assert request.context_window == 64_000
    assert request.metadata["locale"] == "zh-Hans-CN"
    assert request.model.options["baseURL"] == "https://api.deepseek.com"
    assert request.model.profile_id == "deepseek:deepseek-v4-flash"
    assert "model_profile" not in request.model.options
    assert domain.book_id == "book-1"
    assert domain.chapter_id == "chapter-1"
    assert domain.selected_memory_ids == (1,)
    assert "max_generation_tokens" not in request.model.options
    assert request.model.max_generation_tokens is None
    assert request.model.capability_snapshot.max_generation_tokens == 393_216
    assert options.result_capacity_target_tokens is None
    assert options.context_claims[0].name == "writing_retrieval"


def test_writing_chat_stream_id_becomes_an_opaque_run_correlation_binding():
    body = ChatStreamRequest(
        streamId="chat-session-7-request-1",
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
        },
        sessionId=7,
        chatAgentMode="agent",
    )

    request = to_writing_agent_request(
        body,
        {
            "model": "deepseek-v4-flash",
            "baseURL": "https://api.deepseek.com",
        },
    )
    options = writing_run_options(request, {"max_generation_tokens": 2048})

    assert options.turn_id == "chat-session-7-request-1"
    assert options.binding is not None
    assert options.binding.namespace == "writing.chat.request"
    assert options.binding.aggregate_id == "7"
    assert options.binding.command_id == "chat-session-7-request-1"


@pytest.mark.asyncio
async def test_composition_binds_profile_identity_into_existing_run_binding(
    temp_db: DatabaseConnection,
):
    composition = _writing_composition(temp_db)
    try:
        body = ChatStreamRequest(
            streamId="chat-session-7-profile-binding",
            messages=[{"role": "user", "content": "hello"}],
            apiKey="key",
            apiProvider="openai",
            options=_fixture_model_options(),
            sessionId=7,
            bookId="book-1",
            chatAgentMode="agent",
        )
        request = to_writing_agent_request(body, {"model": "model"})
        options = writing_run_options(
            request,
            {"max_generation_tokens": 2_048},
        )

        bound = composition.bind_run_profile(request, options)

        assert bound.binding is not None
        attributes = dict(bound.binding.attributes)
        identity = attributes.pop("modelTaskIdentity")
        assert attributes["agentProfile"] == "writing.purra-native.v1"
        assert attributes["domainNamespace"] == "purrtypos.writing"
        assert attributes["bookId"] == "book-1"
        assert attributes["agentImplementation"]["implementationId"] == (
            "purra-native"
        )
        assert identity["schemaVersion"] == 1
        assert len(identity["modelRequestDigest"]) == 64
        assert identity["requestedReasoningMode"] == options.reasoning_mode.value
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_does_not_restore_legacy_eager_book_catalogs(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "测试书籍"],
    )
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-2", "其他书籍"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-1", "写作目录", "book-1"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-2", "其他写作目录", "book-2"],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, parent_id, level, sort) "
        "VALUES (?, ?, ?, NULL, 1, 1)",
        ["volume-1", "writing-1", "第一卷"],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, parent_id, level, sort) "
        "VALUES (?, ?, ?, ?, 2, 2)",
        ["chapter-1", "writing-1", "第一章", "volume-1"],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, parent_id, level, sort) "
        "VALUES (?, ?, ?, NULL, 1, 1)",
        ["chapter-other", "writing-2", "其他章节"],
    )
    await temp_db.execute(
        "INSERT INTO outlines "
        "(id, title, type, book_id, writing_chapter_id) "
        "VALUES (?, ?, 'chapter', ?, ?)",
        ["outline-1", "第一章大纲", "book-1", "chapter-1"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'chapter', ?)",
        ["outline-other", "其他大纲", "book-2"],
    )

    body = ChatStreamRequest.model_validate({
        "messages": [{"role": "user", "content": "读取第一章"}],
        "apiKey": "key",
        "apiProvider": "openai",
        "options": _fixture_model_options(),
        "enableAgentTools": True,
        "bookId": "book-1",
        "chapterId": "chapter-1",
        # Legacy renderer snapshots are ignored even if an old client sends
        # them; the composition always replaces catalogs from SQLite.
        "writingChapters": [{
            "id": "forged-chapter",
            "title": "伪造章节",
        }],
        "availableOutlines": [{
            "id": "forged-outline",
            "title": "伪造大纲",
        }],
    })
    assert "writingChapters" not in body.model_dump()
    assert "availableOutlines" not in body.model_dump()

    request = to_writing_agent_request(
        body,
        {"model": "model", "baseURL": "https://example.test/v1"},
    )
    before = WritingRequestContext.from_core_context(
        request.domain_context
    )
    assert "writing_chapters" not in request.domain_context.payload
    assert "available_outlines" not in request.domain_context.payload

    composition = _writing_composition(temp_db)
    try:
        prepared = await composition.prepare_request(request)
    finally:
        await composition.shutdown()
    domain = WritingRequestContext.from_core_context(
        prepared.domain_context
    )

    assert domain.book_id == "book-1"
    assert domain.chapter_id == "chapter-1"
    assert prepared.domain_context.payload[
        "replacement_context_snapshot"
    ]["schemaVersion"] == 1


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
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        associatedOutlineIds=["outline-1"],
    )
    request = to_writing_agent_request(body, {"model": "model"})

    options = writing_run_options(
        request,
        {"max_generation_tokens": 2_048},
    )

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
        options=_fixture_model_options(),
        enableAgentTools=enable_agent_tools,
        bookId=book_id,
        chapterId=chapter_id,
        associatedOutlineIds=associated_outline_ids,
    )
    request = to_writing_agent_request(body, {"model": "model"})

    options = writing_run_options(
        request,
        {"max_generation_tokens": 2_048},
    )

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
            options=_fixture_model_options(),
            enableAgentTools=True,
            bookId="book-1",
            chapterId="chapter-1",
            associatedOutlineIds=["outline-1"],
        )
        return to_writing_agent_request(body, {"model": "model"})

    composition = _writing_composition(temp_db)
    p5_request = _request(
        "对照当前章节与关联大纲，找出两处不一致，并给出最小修改建议。"
    )
    p3_request = _request("读取当前章节，给出一个150字以内的摘要。")
    p5_judges = composition.create_response_judge_policies(p5_request)
    p3_judges = composition.create_response_judge_policies(p3_request)
    core = composition.create_core(
        "key", agent_profile=WRITING_REPLACEMENT_PROFILE_ID,
    )
    p5_options = writing_run_options(
        p5_request,
        {"max_generation_tokens": 2_048},
        response_judge_policies=p5_judges,
    )
    p3_options = writing_run_options(
        p3_request,
        {"max_generation_tokens": 2_048},
        response_judge_policies=p3_judges,
    )

    assert len(p5_options.response_judge_policies) == 1
    assert p3_options.response_judge_policies == ()
    assert not hasattr(core, "_response_judges")


@pytest.mark.asyncio
@pytest.mark.parametrize("invocation_timeout_ms", [None, 300_000])
async def test_private_model_task_inherits_persisted_run_limits(
    temp_db, monkeypatch, invocation_timeout_ms,
):
    import time
    import application.agent_composition as composition_module
    import purra.model_invocation.manager as invocation_manager
    from infrastructure.persistence.run_store import create_run
    from purra.contracts import ModelStream, ModelStreamChunk, ReasoningMode

    class Gateway:
        def describe_invocation(self, messages, invocation):
            return {}

        async def stream(self, messages, invocation, signal=None):
            async def chunks():
                yield ModelStreamChunk(content_delta="ok", finish_reason="stop")
            return ModelStream(
                chunks=chunks(), model="fixture-model",
                applied_generation_limit=invocation.max_generation_tokens,
            )

        async def complete(self, *args, **kwargs):
            raise AssertionError("streaming expected")

    monkeypatch.setattr(composition_module, "ProviderModelGateway", lambda *a, **kw: Gateway())
    monkeypatch.setattr(invocation_manager, "time", SimpleNamespace(
        time=lambda: time.time() - 360,
        monotonic=time.monotonic,
    ))
    model_request = ModelRequest(
        provider="openai",
        model="fixture-model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            profile_id="openai:fixture-model",
            max_generation_tokens=256,
        ),
    )
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **dependencies: _FakeAgentProfile(dependencies),),
    )
    bound = composition.bind_run_profile(
        replace(_fake_request(), model=model_request),
        AgentCoreRunOptions(
            reasoning_mode=ReasoningMode.DISABLED,
            binding=RunBinding("test.private", "root", "checkpoint"),
        ),
    )
    run_id = await create_run(
        temp_db, session_id=None, prompt="checkpoint", mode="agent",
        binding=bound.binding,
        runtime_limits=RuntimeLimits(
            max_run_generation_tokens=None, max_model_invocation_attempts=1,
            root_run_timeout_ms=None,
            provider_invocation_timeout_ms=invocation_timeout_ms,
        ),
    )
    service = AgentRunService(composition)

    async def invoke():
        return await service.run_model_text(
            run_id=run_id, turn_id="private-task", api_key="fixture-key",
            messages=(AgentMessage(role="user", content="checkpoint"),),
            model_request=model_request,
            reasoning_mode=ReasoningMode.DISABLED, signal=None,
        )

    try:
        if invocation_timeout_ms is not None:
            with pytest.raises(Exception) as failure:
                await invoke()
            assert failure.value.code == "model_invocation_deadline_exceeded"
        else:
            result = await invoke()
            assert result.content == "ok"
            operations = await temp_db.fetch_all(
                "SELECT event_type FROM ai_agent_run_events WHERE run_id = ? "
                "AND event_type IN ('operation.started', 'operation.finished') ORDER BY id",
                [run_id],
            )
            assert [row["event_type"] for row in operations] == [
                "operation.started", "operation.finished",
            ]
            with pytest.raises(Exception) as failure:
                await invoke()
            assert failure.value.code == "runtime_budget_exceeded"
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "user_ceiling_removed", "user_ceiling_lowered", "user_ceiling_raised",
    "provider", "model", "thinking", "reasoning_effort", "reasoning_mode",
    "capability", "endpoint", "temperature", "missing_identity",
])
async def test_private_model_task_rejects_persisted_root_identity_drift(
    temp_db, monkeypatch, change,
):
    import application.agent_composition as composition_module
    from infrastructure.persistence.run_store import create_run
    from purra.errors import ContractViolationError

    gateway_constructions = []
    monkeypatch.setattr(
        composition_module, "ProviderModelGateway",
        lambda *args, **kwargs: gateway_constructions.append((args, kwargs)),
    )
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **dependencies: _FakeAgentProfile(dependencies),),
    )
    root = replace(
        _fake_request().model,
        max_generation_tokens=512,
        options={
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "temperature": 0.2,
            "baseURL": "https://provider.example/v1",
        },
    )
    bound = composition.bind_run_profile(
        replace(_fake_request(), model=root),
        AgentCoreRunOptions(
            reasoning_mode=ReasoningMode.ENABLED,
            binding=RunBinding("test.private", "root", "checkpoint"),
        ),
    )
    run_id = await create_run(
        temp_db, session_id=None, prompt="checkpoint", mode="agent",
        binding=None if change == "missing_identity" else bound.binding,
        requested_user_max_generation_tokens=512,
        selected_context_window_tokens=2_048,
    )
    request = root
    mode = ReasoningMode.ENABLED
    if change.startswith("user_ceiling_"):
        request = replace(root, max_generation_tokens={
            "user_ceiling_removed": None,
            "user_ceiling_lowered": 256,
            "user_ceiling_raised": 1_024,
        }[change])
    elif change in {"provider", "model"}:
        request = replace(root, **{change: "different"})
    elif change == "reasoning_mode":
        mode = ReasoningMode.DISABLED
    elif change == "capability":
        request = replace(root, capability_snapshot=replace(
            root.capability_snapshot, max_generation_tokens=8_192,
        ))
    elif change in {"thinking", "reasoning_effort", "endpoint", "temperature"}:
        key = "baseURL" if change == "endpoint" else change
        request = replace(root, options={
            **dict(root.options),
            key: {
                "thinking": {"type": "disabled"},
                "reasoning_effort": "low",
                "endpoint": "https://different.example/v1",
                "temperature": 0.7,
            }[change],
        })

    try:
        with pytest.raises(ContractViolationError) as failure:
            await AgentRunService(composition).run_model_text(
                run_id=run_id, turn_id="private-task", api_key="fixture-key",
                messages=(AgentMessage(role="user", content="checkpoint"),),
                model_request=request, reasoning_mode=mode, signal=None,
            )
        assert failure.value.code == (
            "model_request_identity_missing"
            if change == "missing_identity"
            else "model_request_identity_conflict"
        )
        assert gateway_constructions == []
        row = await temp_db.fetch_one(
            "SELECT model_attempt_count FROM ai_agent_runs WHERE id = ?", [run_id],
        )
        assert row["model_attempt_count"] == 0
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_private_model_task_preserves_root_identity_across_composition_restart(
    temp_db, monkeypatch,
):
    import application.agent_composition as composition_module
    from infrastructure.persistence.run_store import create_run
    from purra.contracts import ModelStream, ModelStreamChunk

    invocations = []

    class Gateway:
        async def stream(self, messages, invocation, signal=None):
            invocations.append(invocation)

            async def chunks():
                yield ModelStreamChunk(content_delta='{"ok":true}', finish_reason="stop")

            return ModelStream(
                chunks=chunks(), model=invocation.request.model,
                applied_generation_limit=invocation.max_generation_tokens,
            )

        async def complete(self, *args, **kwargs):
            raise AssertionError("streaming expected")

    monkeypatch.setattr(composition_module, "ProviderModelGateway", lambda *_, **_kwargs: Gateway())
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **dependencies: _FakeAgentProfile(dependencies),),
    )
    model = replace(_fake_request().model, options={
        "baseURL": "https://provider.example/v1/",
        "authorization": "Bearer old-credential",
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    })
    root = with_adapter_public_progress(model)
    bound = composition.bind_run_profile(
        replace(_fake_request(), model=root, context_window=2_048),
        AgentCoreRunOptions(
            reasoning_mode=ReasoningMode.ENABLED,
            binding=RunBinding("test.private", "root", "checkpoint"),
        ),
    )
    run_id = await create_run(
        temp_db, session_id=None, prompt="checkpoint", mode="agent",
        binding=bound.binding, selected_context_window_tokens=2_048,
    )
    await composition.shutdown()
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **dependencies: _FakeAgentProfile(dependencies),),
    )
    task_model = replace(model, options={
        **dict(model.options),
        "baseURL": "https://provider.example/v1",
        "authorization": "Bearer rotated-credential",
        "response_format": {"type": "json_object"},
    })
    try:
        result = await AgentRunService(composition).run_model_text(
            run_id=run_id, turn_id="private-task", api_key="fixture-key",
            messages=(AgentMessage(role="user", content="checkpoint"),),
            model_request=task_model, reasoning_mode=ReasoningMode.ENABLED,
            signal=None,
        )
        assert result.content == '{"ok":true}'
        assert len(invocations) == 1
        assert invocations[0].request == task_model
        assert invocations[0].reasoning_mode is ReasoningMode.ENABLED
        assert invocations[0].max_generation_tokens < 2_048
        assert invocations[0].output_budget.requested_user_max_generation_tokens is None
        assert invocations[0].output_budget.profile_max_generation_tokens == 4_096
        row = await temp_db.fetch_one(
            "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = ?", [run_id],
        )
        assert "old-credential" not in row["binding_attributes_json"]
        assert "provider.example" not in row["binding_attributes_json"]
        assert set(json.loads(row["binding_attributes_json"])["modelTaskIdentity"]) == {
            "schemaVersion", "modelRequestDigest", "requestedReasoningMode",
        }
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_wires_compaction_into_core_not_run_service(
    temp_db: DatabaseConnection,
):
    composition = _writing_composition(temp_db)

    core = composition.create_core(
        "key", agent_profile=WRITING_REPLACEMENT_PROFILE_ID,
    )

    assert core._conversation_compactor_factory is not None
    compactor = core._conversation_compactor_factory(AgentModelTaskRunner(
        core._model_invocations,
        ModelInvocationContext(run_id="composition-test-run"),
        _fake_request().model,
    ))
    assert not isinstance(
        core._conversation_compactor.hook,
        ConversationCompactionService,
    )
    assert isinstance(compactor.hook, ConversationCompactionService)
    assert compactor.hook._repository is (
        composition.conversation_compaction_repository
    )
    assert compactor.settings.trigger_ratio == 0.85
    assert (
        compactor.settings.default_keep_recent_messages
        == 20
    )


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
    composition = _writing_composition(temp_db)
    core = composition.create_core(
        "key", agent_profile=WRITING_REPLACEMENT_PROFILE_ID,
    )

    assert core._tool_executor._limits.approval_timeout_seconds == 3_600


@pytest.mark.asyncio
async def test_composition_exposes_core_tree_delegation_without_legacy_host_dispatch(
    temp_db: DatabaseConnection,
):
    composition = _writing_composition(temp_db)
    request = to_writing_agent_request(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "研究当前章节证据"}],
            apiKey="key",
            apiProvider="openai",
            options=_fixture_model_options(),
            enableAgentTools=True,
            bookId="book-1",
            chapterId="chapter-1",
            chatAgentMode="agent",
        ),
        {"model": "model"},
    )
    core = composition.create_core(
        "key",
        agent_profile=WRITING_REPLACEMENT_PROFILE_ID,
    )

    assert core._preset is not None
    assert not hasattr(core._preset, "delegated_agents")
    assert "delegateToAgents" in {
        item.schema.name for item in core._tool_catalog.registrations()
    }
    assert request.tools_enabled is True


@pytest.mark.asyncio
async def test_composition_rejects_the_removed_legacy_delegation_policy(
    temp_db: DatabaseConnection,
):
    with pytest.raises(TypeError, match="delegation_policy"):
        _writing_composition(temp_db, delegation_policy=object())


def test_request_mapping_rejects_caller_owned_tool_contract():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
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
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
    )

    with pytest.raises(ValueError, match="caller-owned tools"):
        to_writing_agent_request(
            body,
            {"model": "model", "tools": caller_tools},
        )


def test_caller_generation_limit_is_preserved_for_invocation_resolution():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
    )
    provider_options = {
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
        "max_generation_tokens": 1_000,
        "thinking": {"type": "enabled"},
    }
    request = to_writing_agent_request(body, provider_options)

    options = writing_run_options(request, provider_options)

    assert "max_generation_tokens" not in request.model.options
    assert request.model.max_generation_tokens == 1_000
    assert request.model.capability_snapshot.max_generation_tokens == 393_216
    assert options.result_capacity_target_tokens is None


def test_sse_mapping_rejects_legacy_agent_events():
    with pytest.raises(TypeError, match="canonical output"):
        core_update_to_sse_chunk(
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                run_id="run-legacy",
                payload={"status": "running"},
            ),
            model="model",
        )


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
    set_agent_composition(_writing_composition(temp_db))

    response = await chat_stream(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "搜索"}],
            apiKey="key",
            apiProvider="openai",
            options=_fixture_model_options(),
            tools=custom_tools,
            enableAgentTools=True,
            bookId="book-1",
                chatAgentMode="agent",
                planningMode="planned",
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


@pytest.mark.asyncio
async def test_composed_route_reuses_observed_required_tool_choice_capability():
    from infrastructure.models.provider_capabilities import ProviderCapabilityCache

    class _FakeCore:
        def __init__(self, owner):
            self._owner = owner
            self.run_id = ""

        async def submit(self, _request, *, options):
            self._owner.options.append(options)
            self.run_id = f"run-{len(self._owner.options)}"
            if len(self._owner.options) == 1:
                self._owner.unsupported_callback()

            core = self

            class _FakeHandle:
                run_id = core.run_id

                def subscribe(self, after_sequence=0):
                    assert after_sequence == 0

                    async def _events():
                        if False:
                            yield None

                    return _events()

                async def wait(self):
                    return AgentRunResult(
                        run_id=self.run_id,
                        status=RunStatus.DONE,
                        model="resolved-model",
                    )

            return _FakeHandle()

    class _FakeComposition:
        def __init__(self):
            self.options = []
            self.released: list[str] = []
            self.unsupported_callback = lambda: None
            self.provider_capabilities = ProviderCapabilityCache()
            self.output_repository = self

        async def load_validated_result(self, run_id):
            return "synthetic validated result"

        def create_core_for_request(
            self,
            _request,
            _api_key,
            *,
            on_required_tool_choice_unsupported=None,
        ):
            self.unsupported_callback = on_required_tool_choice_unsupported or (lambda: None)
            return _FakeCore(self)

        def create_response_judge_policies(self, _request):
            return ()

        async def prepare_request(self, request):
            return request

        def bind_run_profile(self, _request, options):
            return options

        def observe_event(self, _event):
            return None

        def release_core(self, core):
            self.released.append(core.run_id)

    composition = _FakeComposition()
    set_agent_composition(composition)  # type: ignore[arg-type]
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "执行一个足够长的写作任务"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "glm-5.3-flash",
            "model_profile": "zai:glm-5.3-flash", "profile_binding": "compatible",
            "max_generation_tokens": 2_048,
            "thinking": {"type": "enabled"},
        },
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
    )
    provider_options = {
        "model": "glm-5.3-flash",
        "baseURL": "https://example.test/v1",
        "thinking": {"type": "enabled"},
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
        model="glm-5.3-flash",
        thinking_enabled=True,
    )
    assert composition.provider_capabilities.required_tool_choice_is_unsupported(
        capability_key
    )
    assert composition.options[0].force_planned_tool_choice is True
    assert composition.options[1].force_planned_tool_choice is False
    assert first[-1] == {
        "done": True,
        "model": "resolved-model",
        "runResult": {
            "runId": "run-1",
            "status": "done",
            "errorCode": None,
        },
    }
    assert second[-1] == {
        "done": True,
        "model": "resolved-model",
        "runResult": {
            "runId": "run-2",
            "status": "done",
            "errorCode": None,
        },
    }
    assert composition.released == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_writing_auto_direct_answer_uses_real_live_final_stream(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime_calls = 0
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "Auto 直接回答测试书"],
    )

    async def _planner_must_not_run(*_args, **_kwargs):
        raise AssertionError("Auto direct answer must not start Planner")

    async def _runtime(_key, _messages, options, _provider, signal=None):
        nonlocal runtime_calls
        runtime_calls += 1
        call_number = runtime_calls
        assert signal is not None
        tool_names = {
            item["function"]["name"]
            for item in options.get("tools", [])
        }
        async def _stream():
            if call_number == 1:
                assert "getStoryBackground" in tool_names
                yield {
                    "choices": [{
                        "delta": {"content": "内部候选，不应公开。"},
                        "finish_reason": "stop",
                    }],
                }
                return
            assert call_number == 2
            assert not tool_names
            yield {"choices": [{"delta": {"content": "直接"}}]}
            await asyncio.sleep(0.05)
            yield {
                "choices": [{
                    "delta": {"content": "回答。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": _stream(),
            "model": "model",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner_must_not_run,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _runtime,
    )
    set_agent_composition(_writing_composition(temp_db))

    response = await chat_stream(ChatStreamRequest(
        messages=[{"role": "user", "content": "直接回答这个问题"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
        contextWindow="200k",
    ))
    events = await _collect(response)

    assert_raw_canonical_wire(events)
    assert runtime_calls == 2
    assert events[-1]["runResult"]["status"] == "done"
    final_events = [
        event
        for event in events
        if event.get("source") == "provider"
        and event.get("kind") in {
            "provider.content_delta", "provider.delta_batch",
        }
        and event.get("channel") == "final"
        and event.get("visibility") == "public"
    ]
    assert len(final_events) == 2
    assert provider_text(events) == "直接回答。"
    assert "内部候选" not in provider_text(events)
    assert not any(
        event.get("kind") == "planning.progress"
        for event in events
    )
    assert not any(
        event.get("kind") == "agent.progress"
        for event in events
    )
    assert not any(
        event.get("kind") == "operation.started"
        and event.get("payload", {}).get("kind") == "planning"
        for event in events
    )


@pytest.mark.asyncio
async def test_writing_replacement_auto_does_not_invoke_retired_host_planner(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    planner_calls = 0
    runtime_calls = 0
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "Auto 规划测试书"],
    )

    async def _planner(_key, _messages, options, _provider, signal=None):
        nonlocal planner_calls
        planner_calls += 1
        assert signal is not None
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": False,
                    "title": "回答请求",
                    "goal": "根据请求形成最终答复",
                    "todos": [],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    async def _runtime(_key, _messages, options, _provider, signal=None):
        nonlocal runtime_calls
        runtime_calls += 1
        assert signal is not None

        async def _stream():
            names = [
                item["function"]["name"]
                for item in options.get("tools", [])
            ]
            assert "request_plan" not in names
            yield {
                "choices": [{
                    "delta": {"content": "直接回答。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": _stream(),
            "model": "model",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(
            _planner,
            _runtime,
            progress_chunks=("先核对请求范围，", "再安排执行步骤。"),
        ),
    )
    set_agent_composition(_writing_composition(temp_db))

    response = await chat_stream(ChatStreamRequest(
        messages=[{"role": "user", "content": "先规划再回答"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
        contextWindow="200k",
    ))
    events = await _collect(response)
    assert_raw_canonical_wire(events)
    progress_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("kind") == "planning.progress"
    ]
    assert planner_calls == 0
    assert {
        "status": events[-1]["runResult"]["status"],
        "errorCode": events[-1]["runResult"]["errorCode"],
    } == {"status": "done", "errorCode": None}
    assert runtime_calls == 2
    assert progress_indexes == []
    assert provider_text(events) == "直接回答。"
    assert "request_plan" not in "\n".join(
        json.dumps(event, ensure_ascii=False)
        for event in events
        if event.get("visibility") == "public"
    )


@pytest.mark.asyncio
async def test_composed_route_uses_complete_purra(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    round_number = 0
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "完整 PurrA 路径测试书"],
    )

    async def _create_plan(*_args, **_kwargs):
        return {
            "applied_generation_limit": _args[2].get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": False,
                    "reason": "直接回答即可",
                }, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    async def _create_chat_stream(
        key,
        messages,
        options,
        api_provider,
        signal=None,
    ):
        nonlocal round_number
        round_number += 1
        assert key == "key"
        assert api_provider == "openai"
        assert options["model"] == "model"
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }],
            }

        return {"applied_generation_limit": options.get("max_tokens"), "stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _create_plan,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(_create_plan, _create_chat_stream),
    )
    set_agent_composition(_writing_composition(temp_db))

    response = await chat_stream(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "简短"}],
            apiKey="key",
            apiProvider="openai",
            options=_fixture_model_options(),
            tools=[],
            enableAgentTools=True,
            bookId="book-1",
            chatAgentMode="agent",
            contextWindow="200k",
        ),
    )
    events = await _collect(response)

    assert_raw_canonical_wire(events)
    assert [
        event["payload"]["status"] for event in events
        if event.get("kind") == "run.lifecycle"
    ] == ["running", "done"], events
    assert runtime_events(events, "context.budgeted")
    assert round_number == 2
    assert provider_text(events) == "完成"
    assert events[-1]["done"] is True
    assert events[-1]["model"] == "model"
    assert events[-1]["runResult"]["status"] == "done"
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
    composition = _writing_composition(
        temp_db,
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
        composition.create_core(
            "key", agent_profile=WRITING_REPLACEMENT_PROFILE_ID,
        )


@pytest.mark.asyncio
async def test_composition_shutdown_cancels_owned_root_execution_tasks(
    temp_db: DatabaseConnection,
):
    composition = _writing_composition(temp_db)
    canceled = asyncio.Event()

    async def _worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    worker = asyncio.create_task(_worker())
    composition.track_background_run(worker)
    await asyncio.sleep(0)

    await composition.shutdown()

    assert canceled.is_set()
    assert worker.done()
