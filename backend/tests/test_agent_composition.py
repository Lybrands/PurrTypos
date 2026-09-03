from __future__ import annotations

import asyncio
import json
import shutil
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
    ResponseConstraints,
    RuntimeLimits,
    RunStatus,
    ToolCall,
    ToolExecutionMode,
)
from purra.events import AgentEvent, CoreEventType
from purra.api import (
    AgentCoreRunOptions,
    AgentModelTaskRunner,
    DelegationPolicy,
    PlanningMode,
)
from purra.model_invocation import ModelInvocationContext
from purra.model_protocol import (
    FeatureSupport,
    InvocationOutputLimit,
    InvocationOutputLimitSource,
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
from application.shared_agent_context import SharedAgentContextProvider
from application.prepared_read_context import PreparedReadContextProvider
from application.conversation_compaction import ConversationCompactionService
from application.memory_reranking import ModelBackedMemoryReranker
from application.writing_agent_profile import build_writing_agent_profile
from application.request_mapping import (
    context_window_tokens,
    to_writing_agent_request,
    writing_run_options,
)
from application.sse_mapping import core_update_to_sse_chunk
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.context import WritingContextProvider
from application.writing_context_source import RepositoryWritingContextSource
from domains.writing.contracts import WritingDomainContext
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

    def __init__(self, factory_dependencies: dict[str, object]):
        self.factory_dependencies = factory_dependencies
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
            runtime_limits=RuntimeLimits(max_run_output_tokens=None),
            recovery_policy=RecoveryPolicy(),
        )

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest:
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
        model=ModelRequest(provider="openai", model="test-model"),
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
        "model_profile": "deepseek:deepseek-v4-flash",
        "max_tokens": 2_048,
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
    }
    assert set(profile.dispatcher_dependencies or {}) == {
        "long_task_repository",
        "executor",
    }


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
            options=AgentCoreRunOptions(output_limit=InvocationOutputLimit(
                max_tokens=256,
                source=InvocationOutputLimitSource.WORKFLOW_POLICY,
                profile_max_tokens=256,
            )),
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
async def test_writing_profile_owns_product_capabilities(
    temp_db: DatabaseConnection,
):
    profile = build_writing_agent_profile(
        db=temp_db,
        skills_dir=BACKEND_DIR / "skills",
    )
    composition = AgentComposition(
        temp_db,
        profile_factories=(lambda **_dependencies: profile,),
    )
    try:
        core = composition.create_core(
            "key",
            agent_profile="writing",
        )
        model_tasks = AgentModelTaskRunner(
            core._model_invocations,
            ModelInvocationContext(run_id="writing-profile-test"),
        )
        provider = profile.context_provider_factory()(model_tasks)
        tool_names = {
            item.schema.name
            for item in profile.adapter.tool_catalog.registrations()
        }
        declared_skill_names = {
            path.parent.name
            for path in (BACKEND_DIR / "skills").glob("*/SKILL.md")
        }

        assert isinstance(profile, AgentProfile)
        assert profile.id == "writing"
        assert profile.domain_namespace == "purrtypos.writing"
        assert profile.adapter.context_strategy is ContextStrategy.STAGED
        assert core._context_strategy is ContextStrategy.STAGED
        assert tool_names == declared_skill_names
        assert not hasattr(core._preset, "delegated_agents")
        assert "delegateToAgents" in {
            item.schema.name for item in core._tool_catalog.registrations()
        }
        assert isinstance(
            profile.adapter.context_provider,
            PreparedReadContextProvider,
        )
        assert isinstance(
            profile.adapter.context_provider.provider,
            WritingContextProvider,
        )
        assert isinstance(
            profile.adapter.context_provider.provider._source,
            RepositoryWritingContextSource,
        )
        assert isinstance(provider, PreparedReadContextProvider)
        assert isinstance(provider.provider, WritingContextProvider)
        assert isinstance(provider.provider._source, RepositoryWritingContextSource)
        reranker = provider.provider._source._memory_reranker
        assert isinstance(reranker, ModelBackedMemoryReranker)
        assert profile.task_admission() is None
        assert profile.create_long_task_dispatcher() is None
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_product_composition_registers_product_profiles(
    temp_db: DatabaseConnection,
):
    composition = create_agent_composition(temp_db)
    try:
        assert composition.agent_profile_ids == (
            "writing",
            "novel_analysis",
            "screenplay",
        )
        for profile_id in composition.agent_profile_ids:
            core = composition.create_core("key", agent_profile=profile_id)
            provider = core._context_provider
            if core._context_provider_factory is not None:
                provider = core._context_provider_factory(AgentModelTaskRunner(
                    core._model_invocations,
                    ModelInvocationContext(run_id=f"{profile_id}-policy-test"),
                ))
            assert isinstance(provider, SharedAgentContextProvider)
            assert (
                core._preset.component_bindings["contextProvider"].revision
                == "2"
            )
            if profile_id == "screenplay":
                assert core._planner._result_validator is not None
                assert core._planner._limits.max_repair_attempts == 2
        novel_core = composition.create_core("key", agent_profile="novel_analysis")
        assert type(novel_core._planner).__name__ == "AgentPlanner"
        assert novel_core._planner._result_validator is not None
        assert novel_core._planner._limits.max_steps is None
        assert novel_core._planner._limits.max_tool_steps == 0
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


@pytest.mark.asyncio
async def test_product_composition_consumes_configured_writing_skills_dir(
    temp_db: DatabaseConnection,
    tmp_path: Path,
):
    configured_skills = tmp_path / "configured-skills"
    shutil.copytree(BACKEND_DIR / "skills", configured_skills)
    marker = "Configured Writing catalog marker."
    skill_file = configured_skills / "getChapterContent" / "SKILL.md"
    source = skill_file.read_text(encoding="utf-8")
    description_line = next(
        line for line in source.splitlines()
        if line.startswith("description:")
    )
    skill_file.write_text(
        source.replace(description_line, f"description: {marker}", 1),
        encoding="utf-8",
    )

    composition = create_agent_composition(
        temp_db,
        skills_dir=configured_skills,
    )
    try:
        core = composition.create_core("key", agent_profile="writing")
        registration = next(
            item for item in core._tool_catalog.registrations()
            if item.schema.name == "getChapterContent"
        )
        assert registration.schema.description == marker
    finally:
        await composition.shutdown()


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
            "model_profile": "deepseek:deepseek-v4-flash",
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
    domain = WritingDomainContext.from_core_context(request.domain_context)
    options = writing_run_options(request, {"max_tokens": 2048})

    assert request.context_window == 64_000
    assert request.metadata["locale"] == "zh-Hans-CN"
    assert request.model.options["baseURL"] == "https://api.deepseek.com"
    assert request.model.profile_id == "deepseek:deepseek-v4-flash"
    assert "model_profile" not in request.model.options
    assert domain.book_id == "book-1"
    assert domain.chapter_id == "chapter-1"
    assert domain.selected_memory_ids == (1,)
    assert "max_tokens" not in request.model.options
    assert options.output_limit is not None
    assert options.output_limit.max_tokens == 48_000
    assert options.output_limit.source.value == "model_profile"
    assert options.context_claims[0].name == "writing_retrieval"


def test_writing_chat_stream_id_becomes_an_opaque_run_correlation_binding():
    body = ChatStreamRequest(
        streamId="chat-session-7-request-1",
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
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
    options = writing_run_options(request, {"max_tokens": 2048})

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
            chatAgentMode="agent",
        )
        request = to_writing_agent_request(body, {"model": "model"})
        options = writing_run_options(request, {"max_tokens": 2_048})

        bound = composition.bind_run_profile(request, options)

        assert bound.binding is not None
        assert dict(bound.binding.attributes) == {
            "agentProfile": "writing",
            "domainNamespace": "purrtypos.writing",
        }
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_hydrates_authoritative_book_catalogs(
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
    before = WritingDomainContext.from_core_context(
        request.domain_context
    )
    assert before.writing_chapters == ()
    assert before.available_outlines == ()

    composition = _writing_composition(temp_db)
    try:
        prepared = await composition.prepare_request(request)
    finally:
        await composition.shutdown()
    domain = WritingDomainContext.from_core_context(
        prepared.domain_context
    )

    assert [item["id"] for item in domain.writing_chapters] == [
        "volume-1",
        "chapter-1",
    ]
    assert domain.writing_chapters[1]["parent_id"] == "volume-1"
    assert [item["id"] for item in domain.available_outlines] == [
        "outline-1",
    ]
    assert domain.available_outlines[0]["writing_chapter_id"] == (
        "chapter-1"
    )
    assert all(
        "forged" not in str(item["id"])
        and "other" not in str(item["id"])
        for item in (
            *domain.writing_chapters,
            *domain.available_outlines,
        )
    )


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
        options=_fixture_model_options(),
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
    core = composition.create_core("key", agent_profile="writing")
    p5_options = writing_run_options(
        p5_request,
        {"max_tokens": 2_048},
        response_judge_policies=p5_judges,
    )
    p3_options = writing_run_options(
        p3_request,
        {"max_tokens": 2_048},
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
                applied_output_limit=invocation.max_call_output_tokens,
            )

        async def complete(self, *args, **kwargs):
            raise AssertionError("streaming expected")

    monkeypatch.setattr(composition_module, "ProviderModelGateway", lambda *a, **kw: Gateway())
    monkeypatch.setattr(invocation_manager, "time", SimpleNamespace(
        time=lambda: time.time() - 360,
        monotonic=time.monotonic,
    ))
    run_id = await create_run(
        temp_db, session_id=None, prompt="checkpoint", mode="agent",
        runtime_limits=RuntimeLimits(
            max_run_output_tokens=None, max_model_invocation_attempts=1,
            root_run_timeout_ms=None,
            provider_invocation_timeout_ms=invocation_timeout_ms,
        ),
    )
    composition = create_agent_composition(temp_db)
    service = AgentRunService(composition)

    async def invoke():
        return await service.run_model_text(
            run_id=run_id, turn_id="private-task", api_key="fixture-key",
            messages=(AgentMessage(role="user", content="checkpoint"),),
            model_request=ModelRequest(provider="openai", model="fixture-model"),
            output_limit=InvocationOutputLimit(
                max_tokens=256, source=InvocationOutputLimitSource.WORKFLOW_POLICY,
                profile_max_tokens=256,
            ),
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
            with pytest.raises(Exception) as failure:
                await invoke()
            assert failure.value.code == "runtime_budget_exceeded"
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_composition_wires_compaction_into_core_not_run_service(
    temp_db: DatabaseConnection,
):
    composition = _writing_composition(temp_db)

    core = composition.create_core("key", agent_profile="writing")

    assert core._conversation_compactor_factory is not None
    compactor = core._conversation_compactor_factory(AgentModelTaskRunner(
        core._model_invocations,
        ModelInvocationContext(run_id="composition-test-run"),
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
    core = composition.create_core("key", agent_profile="writing")

    assert core._tool_executor._limits.approval_timeout_seconds == 3_600


@pytest.mark.asyncio
async def test_composition_uses_model_defined_read_only_delegation(
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
        agent_profile="writing",
    )

    assert core._preset is not None
    assert not hasattr(core._preset, "delegated_agents")
    executor = core._dynamic_delegated_executor
    assert executor is not None
    registrations, enabled = executor._read_capabilities(request)
    by_name = {item.schema.name: item for item in registrations}
    assert "getChapterContent" in enabled
    assert "editChapterContent" not in enabled
    assert "editChapterContent" not in by_name
    assert "delegateToAgents" not in by_name
    assert all(
        by_name[name].policy.mode is ToolExecutionMode.READ
        for name in enabled
    )
    delegation_schema = next(
        item.schema
        for item in core._tool_catalog.registrations()
        if item.schema.name == "delegateToAgents"
    )
    item_properties = delegation_schema.parameters["properties"][
        "delegations"
    ]["items"]["properties"]
    assert "agentName" in item_properties
    assert "instruction" in item_properties
    assert "enum" not in item_properties["agentName"]


@pytest.mark.asyncio
async def test_composition_owns_the_delegation_policy(
    temp_db: DatabaseConnection,
):
    policy = DelegationPolicy(max_agents_per_call=1, max_parallel=1)
    composition = _writing_composition(temp_db, delegation_policy=policy)
    try:
        core = composition.create_core("key", agent_profile="writing")
        schema = next(
            item.schema
            for item in core._tool_catalog.registrations()
            if item.schema.name == "delegateToAgents"
        ).parameters

        assert composition.delegation_policy is policy
        assert schema["properties"]["delegations"]["maxItems"] == 1
    finally:
        await composition.shutdown()


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


def test_caller_output_limit_is_preserved_for_invocation_limit_resolution():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="anthropic",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
    )
    provider_options = {
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash",
        "max_tokens": 1_000,
        "thinking": {"type": "enabled"},
    }
    request = to_writing_agent_request(body, provider_options)

    options = writing_run_options(request, provider_options)

    assert request.model.options["max_tokens"] == 1_000
    assert options.output_limit is not None
    assert options.output_limit.max_tokens == 1_000


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
            "model_profile": "zai:glm-5.3-flash",
            "max_tokens": 2_048,
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
async def test_writing_auto_direct_answer_uses_one_normal_model_call(
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
        assert signal is not None
        tool_names = {
            item["function"]["name"]
            for item in options.get("tools", [])
        }
        assert "request_plan" in tool_names

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "直接回答。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_output_limit": options.get("max_tokens"),
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
    assert runtime_calls == 1
    assert events[-1]["runResult"]["status"] == "done"
    assert [
        event["payload"]["delta"]
        for event in events
        if event.get("kind") == "provider.content_delta"
        and event.get("channel") == "final"
        and event.get("visibility") == "public"
    ] == ["直接回答。"]
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
async def test_writing_auto_request_plan_uses_shared_core_and_public_progress(
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
            "applied_output_limit": options.get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "回答请求",
                    "goal": "根据请求形成最终答复",
                    "todos": [{
                        "id": "answer",
                        "title": "形成答复",
                        "type": "write",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    }],
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
            if runtime_calls == 1:
                names = [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ]
                assert "request_plan" in names
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-request-plan",
                                "type": "function",
                                "function": {
                                    "name": "request_plan",
                                    "arguments": "{}",
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return
            assert not options.get("tools")
            yield {
                "choices": [{
                    "delta": {"content": "规划完成后回答。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_output_limit": options.get("max_tokens"),
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
            progress="先核对请求范围，再安排执行步骤。",
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
    assert planner_calls == 1
    assert {
        "status": events[-1]["runResult"]["status"],
        "errorCode": events[-1]["runResult"]["errorCode"],
    } == {"status": "done", "errorCode": None}
    assert runtime_calls == 2
    assert progress_indexes
    assert progress_indexes[0] < len(events) - 1
    assert events[progress_indexes[0]]["payload"]["text"] == (
        "先核对请求范围，再安排执行步骤。"
    )
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

    async def _create_plan(*_args, **_kwargs):
        return {
            "applied_output_limit": _args[2].get("max_tokens"),
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
        assert not options.get("tools")
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }],
            }

        return {"applied_output_limit": options.get("max_tokens"), "stream": _stream(), "model": "model"}

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
            planningMode="planned",
            contextWindow="200k",
        ),
    )
    events = await _collect(response)

    assert_raw_canonical_wire(events)
    assert [
        event["payload"]["status"] for event in events
        if event.get("kind") == "run.lifecycle"
    ] == ["running", "done"]
    assert runtime_events(events, "context.budgeted")
    assert round_number == 1
    assert provider_text(events) == "完成"
    assert events[-1]["done"] is True
    assert events[-1]["model"] == "model"
    assert events[-1]["runResult"]["status"] == "done"


@pytest.mark.asyncio
async def test_writing_read_evidence_replans_only_unfinished_semantic_steps(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    chapter_text = "弄堂里没有雨声，只有晾衣竹竿在风里轻撞墙面。"
    background_fact = "弄堂狭窄安静，晾衣竹竿会在风中轻撞墙面"
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-replan", "重规划测试书"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-replan", "写作目录", "book-replan"],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort) VALUES (?, ?, ?, 1, 1)",
        ["chapter-replan", "writing-replan", "第一章：弄堂"],
    )
    await temp_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-replan", _lexical(chapter_text)],
    )
    await temp_db.execute(
        "INSERT INTO story_background (book_id, content) VALUES (?, ?)",
        ["book-replan", background_fact],
    )
    planner_payloads: list[dict[str, object]] = []

    async def _planner(_key, messages, _options, _provider, signal=None):
        assert signal is not None
        payload = json.loads(messages[1]["content"])
        planner_payloads.append(payload)
        if len(planner_payloads) == 1:
            content = {
                "needsTodos": True,
                "title": "深化弄堂氛围",
                "goal": "根据故事背景证据提出氛围改写",
                "todos": [
                    {
                        "id": "inspect-story-background",
                        "title": "检查故事背景",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getStoryBackground"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "propose-atmosphere-rewrite",
                        "title": "提出氛围改写",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                ],
            }
        else:
            execution = payload["executionState"]
            assert execution["completedSteps"][0]["id"] == (
                "inspect-story-background"
            )
            assert background_fact in json.dumps(
                execution["recentToolObservations"],
                ensure_ascii=False,
            )
            content = {
                "needsTodos": True,
                "title": "深化弄堂氛围",
                "goal": "利用弄堂背景证据调整氛围策略",
                "todos": [{
                    "id": "shape-alley-atmosphere",
                    "title": "按背景证据重塑弄堂氛围",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "riskLevel": "read",
                }],
            }
        return {
            "applied_output_limit": _options.get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps(content, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    runtime_round = 0

    async def _runtime(_key, messages, options, _provider, signal=None):
        nonlocal runtime_round
        runtime_round += 1
        assert signal is not None

        async def _stream():
            if runtime_round == 1:
                assert [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ] == ["getStoryBackground"]
                yield {
                    "choices": [{
                        "delta": {"content": "【进展】正在"},
                        "finish_reason": None,
                    }],
                }
                yield {
                    "choices": [{
                        "delta": {"content": "核对故事背景"},
                        "finish_reason": None,
                    }],
                }
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-read-replan",
                                "type": "function",
                                "function": {
                                    "name": "getStoryBackground",
                                    "arguments": "{}",
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return
            assert not options.get("tools")
            assert background_fact in json.dumps(messages, ensure_ascii=False)
            yield {
                "choices": [{
                    "delta": {"content": "改写应以寂静和轻微碰撞声为核心。"},
                    "finish_reason": "stop",
                }],
            }

        return {"applied_output_limit": options.get("max_tokens"), "stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(_planner, _runtime),
    )
    set_agent_composition(_writing_composition(temp_db))

    response = await chat_stream(ChatStreamRequest(
        messages=[{"role": "user", "content": "深化弄堂氛围"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-replan",
        chapterId="chapter-replan",
        currentChapterTitle="第一章：弄堂",
        chatAgentMode="agent",
        planningMode="planned",
        contextWindow="200k",
    ))
    events = await _collect(response)
    assert_raw_canonical_wire(events)
    plans = [
        event["payload"]["data"]
        for event in runtime_events(events, "run.todos_updated")
    ]

    assert len(planner_payloads) == 2
    assert len(plans) >= 2
    progress_events = [
        event for event in events
        if event.get("kind") == "agent.progress"
    ]
    assert [event["payload"]["text"] for event in progress_events] == [
        "正在",
        "正在核对故事背景",
    ]
    first_tool_operation = next(
        index for index, event in enumerate(events)
        if event.get("kind") == "operation.started"
        and event.get("payload", {}).get("kind") == "tool"
    )
    assert all(events.index(event) < first_tool_operation for event in progress_events)
    root_run_ids = {
        event["runId"] for event in events
        if event.get("kind") == "run.lifecycle"
        and event["payload"]["status"] == "running"
    }
    assert len(root_run_ids) == 1
    latest = plans[-1]
    assert [
        (step["id"], step["title"], step["status"])
        for step in latest["steps"]
    ] == [
        ("inspect-story-background", "检查故事背景", "done"),
        (
            "shape-alley-atmosphere",
            "按背景证据重塑弄堂氛围",
            "running",
        ),
    ]
    encoded = json.dumps(latest, ensure_ascii=False).lower()
    assert all(
        forbidden not in encoded
        for forbidden in ("create", "publish", "recipe", "校验候选稿")
    )
    assert events[-1]["runResult"]["status"] == "done"
    runs = await temp_db.fetch_all(
        "SELECT id, status FROM ai_agent_runs ORDER BY create_time ASC"
    )
    assert runs == [{
        "id": next(iter(root_run_ids)),
        "status": "done",
    }]


@pytest.mark.asyncio
async def test_composed_approval_is_resolved_through_existing_http_contract(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    round_number = 0

    async def _create_plan(*_args, **_kwargs):
        return {
            "applied_output_limit": _args[2].get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "安全删除人物",
                    "goal": "经用户确认后删除人物",
                    "todos": [{
                        "id": "delete-character",
                        "title": "删除人物",
                        "type": "write",
                        "executor": "tool",
                        "expectedTools": ["deleteCharacter"],
                        "riskLevel": "destructive",
                    }],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    async def _create_chat_stream(
        _key,
        _messages,
        options,
        _api_provider,
        signal=None,
    ):
        nonlocal round_number
        round_number += 1
        assert signal is not None

        async def _stream():
            if round_number == 1:
                assert [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ] == ["listBookCharacters"]
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-list",
                                "type": "function",
                                "function": {
                                    "name": "listBookCharacters",
                                    "arguments": "{}",
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
            elif round_number == 2:
                assert [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ] == ["deleteCharacter"]
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

        return {"applied_output_limit": options.get("max_tokens"), "stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _create_plan,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(_create_plan, _create_chat_stream),
    )
    set_agent_composition(_writing_composition(temp_db))
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "删除"}],
        apiKey="key",
        apiProvider="openai",
        options=_fixture_model_options(),
        enableAgentTools=True,
        bookId="book-1",
        chatAgentMode="agent",
        planningMode="planned",
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
        payload = chunk.get("payload")
        approval = (
            payload.get("data")
            if chunk.get("kind") == "runtime.event"
            and isinstance(payload, dict)
            and payload.get("eventType") == "approval.requested"
            and isinstance(payload.get("data"), dict)
            else None
        )
        if approval:
            resolved = await resolve_pending_tool_approval(
                approval["approvalId"],
                ResolveToolApprovalRequest(approved=False),
            )
            assert resolved == {
                "success": True,
                "data": {"status": "rejected"},
            }

    assert round_number == 4
    assert_raw_canonical_wire(chunks)
    assert runtime_events(chunks, "approval.requested")
    assert runtime_events(chunks, "approval.resolved")
    assert not runtime_events(chunks, "tool.results")
    assert provider_text(chunks) == "已保留人物"
    assert chunks[-1]["done"] is True
    assert chunks[-1]["model"] == "model"
    assert chunks[-1]["runResult"]["status"] == "done"


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
        composition.create_core("key", agent_profile="writing")


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
