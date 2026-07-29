from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError

from agent_core.contracts import ContextBudget
from application.agent_composition import (
    AgentComposition,
    set_agent_composition,
)
from application.request_mapping import (
    agent_context_claims,
    agent_run_options,
    to_agent_request,
)
from database.connection import DatabaseConnection
from database.crud import screenplay as screenplay_crud
from domains.screenplay.context import (
    SCREENPLAY_POLICY_CONTEXT,
    SCREENPLAY_PROJECT_CONTEXT,
)
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.writing.contracts import WRITING_DOMAIN_NAMESPACE
from schemas.ai import ChatStreamRequest
from routers.ai import _stream_composed_agent


@pytest_asyncio.fixture
async def screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _body(**updates) -> ChatStreamRequest:
    payload = {
        "messages": [{"role": "user", "content": "帮我完善创作简报"}],
        "apiKey": "key",
        "apiProvider": "openai",
        "options": {"model": "model"},
        "agentProfile": "screenplay",
        "screenplayProjectId": "project-1",
        "activeStage": "orientation",
    }
    payload.update(updates)
    return ChatStreamRequest(**payload)


def _context_budget(request) -> ContextBudget:
    claim = agent_context_claims(request)[0]
    return ContextBudget(
        window_tokens=128_000,
        output_reserve_tokens=8_192,
        safety_reserve_tokens=4_096,
        runtime_reserve_tokens=4_096,
        minimum_message_tokens=128,
        provider_input_tokens=111_616,
        context_allocations={claim.name: claim.desired_tokens},
    )


def test_chat_request_defaults_to_writing_profile():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "继续写"}],
        apiKey="key",
        options={"model": "model"},
    )
    request = to_agent_request(body, {"model": "model"})
    assert body.agentProfile == "writing"
    assert request.domain_context.namespace == WRITING_DOMAIN_NAMESPACE


def test_screenplay_profile_requires_project_scope():
    with pytest.raises(ValidationError, match="screenplayProjectId"):
        _body(screenplayProjectId=None)


def test_screenplay_request_mapping_uses_opaque_domain_context():
    request = to_agent_request(
        _body(
            sourceBookId="book-1",
            activeDocumentId="doc-1",
            contextWindow="128k",
        ),
        {"model": "model"},
    )
    context = ScreenplayDomainContext.from_core_context(
        request.domain_context
    )

    assert request.domain_context.namespace == SCREENPLAY_DOMAIN_NAMESPACE
    assert context.project_id == "project-1"
    assert context.requested_source_book_id == "book-1"
    assert context.active_document_id == "doc-1"
    assert request.context_window == 128_000
    assert request.tools_enabled is False


@pytest.mark.asyncio
async def test_screenplay_context_uses_persisted_project_scope(screenplay_db):
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="雾港",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先找人物",
        premise="一个替人保管记忆的人发现自己的记忆属于别人。",
    )
    project = created["project"]
    document = created["initialDocument"]
    body = _body(
        screenplayProjectId=project["id"],
        activeDocumentId=document["id"],
    )
    request = to_agent_request(body, {"model": "model"})
    composition = AgentComposition(screenplay_db)
    registration = composition._profile_registry.for_request(request)
    provider = registration.adapter.context_provider
    claim = agent_context_claims(request)[0]
    bundle = await provider.build_context(
        request,
        ContextBudget(
            window_tokens=128_000,
            output_reserve_tokens=8_192,
            safety_reserve_tokens=4_096,
            runtime_reserve_tokens=4_096,
            minimum_message_tokens=128,
            provider_input_tokens=111_616,
            context_allocations={claim.name: claim.desired_tokens},
        ),
    )

    blocks = {block.name: block for block in bundle.blocks}
    assert set(blocks) == {
        SCREENPLAY_POLICY_CONTEXT,
        SCREENPLAY_PROJECT_CONTEXT,
    }
    assert blocks[SCREENPLAY_POLICY_CONTEXT].untrusted is False
    assert blocks[SCREENPLAY_PROJECT_CONTEXT].untrusted is True
    assert "雾港" in blocks[SCREENPLAY_PROJECT_CONTEXT].content
    assert "不得声称已经保存" in blocks[SCREENPLAY_POLICY_CONTEXT].content
    assert bundle.diagnostics["screenplayProjectId"] == project["id"]
    assert bundle.diagnostics["screenplayDocumentCount"] == 1


@pytest.mark.asyncio
async def test_screenplay_context_rejects_caller_source_override(screenplay_db):
    await screenplay_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "原作"],
    )
    await screenplay_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing-1', '写作目录', 'writing', 'book-1')"
    )
    await screenplay_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) "
        "VALUES ('chapter-1', 'writing-1', '第一章', 1)"
    )
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="改编项目",
        source_kind="book",
        source_book_id="book-1",
        screenplay_format="单集剧",
        approach="忠实改编",
        premise="",
    )
    request = to_agent_request(
        _body(
            screenplayProjectId=created["project"]["id"],
            sourceBookId="another-book",
        ),
        {"model": "model"},
    )
    composition = AgentComposition(screenplay_db)
    provider = composition._profile_registry.for_request(
        request
    ).adapter.context_provider
    claim = agent_context_claims(request)[0]

    with pytest.raises(ValueError, match="source book scope"):
        await provider.build_context(
            request,
            ContextBudget(
                window_tokens=128_000,
                output_reserve_tokens=8_192,
                safety_reserve_tokens=4_096,
                runtime_reserve_tokens=4_096,
                minimum_message_tokens=128,
                provider_input_tokens=111_616,
                context_allocations={claim.name: claim.desired_tokens},
            ),
        )


@pytest.mark.asyncio
async def test_screenplay_context_declares_restricted_adaptation_scope(
    screenplay_db,
):
    await screenplay_db.execute(
        "INSERT INTO books (id, title) VALUES ('scope-book', '范围原作')"
    )
    await screenplay_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('scope-writing', '写作目录', 'writing', 'scope-book')"
    )
    await screenplay_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) "
        "VALUES ('scope-chapter-1', 'scope-writing', '第一章', 1)"
    )
    await screenplay_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) "
        "VALUES ('scope-chapter-2', 'scope-writing', '第二章', 2)"
    )
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="只改第一章",
        source_kind="book",
        source_book_id="scope-book",
        screenplay_format="短片",
        approach="截取改编",
        premise="",
        source_scope={"mode": "first_chapters", "count": 1},
    )
    request = to_agent_request(
        _body(
            screenplayProjectId=created["project"]["id"],
            sourceBookId="scope-book",
        ),
        {"model": "model"},
    )
    provider = AgentComposition(screenplay_db)._profile_registry.for_request(
        request
    ).adapter.context_provider
    bundle = await provider.build_context(request, _context_budget(request))
    blocks = {block.name: block for block in bundle.blocks}

    assert '"mode":"first_chapters"' in (
        blocks[SCREENPLAY_PROJECT_CONTEXT].content
    )
    assert "只改编原作中的限定章节/卷" in (
        blocks[SCREENPLAY_POLICY_CONTEXT].content
    )
    assert "proposeSourceAnalysis" in (
        blocks[SCREENPLAY_POLICY_CONTEXT].content
    )
    assert "此阶段不得直接形成创作简报" in (
        blocks[SCREENPLAY_POLICY_CONTEXT].content
    )


@pytest.mark.asyncio
async def test_screenplay_context_rejects_stale_stage_scope(screenplay_db):
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="阶段权威",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先推情节",
        premise="",
    )
    request = to_agent_request(
        _body(
            screenplayProjectId=created["project"]["id"],
            activeStage="review",
        ),
        {"model": "model"},
    )
    provider = AgentComposition(screenplay_db)._profile_registry.for_request(
        request
    ).adapter.context_provider

    with pytest.raises(ValueError, match="stage scope"):
        await provider.build_context(request, _context_budget(request))


@pytest.mark.asyncio
async def test_screenplay_context_rejects_session_from_another_project(
    screenplay_db,
):
    first = await screenplay_crud.create_project(
        screenplay_db,
        title="项目一",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先推情节",
        premise="",
    )
    second = await screenplay_crud.create_project(
        screenplay_db,
        title="项目二",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先推情节",
        premise="",
    )
    foreign_session = await screenplay_crud.get_or_create_agent_session(
        screenplay_db,
        second["project"]["id"],
    )
    request = to_agent_request(
        _body(
            screenplayProjectId=first["project"]["id"],
            sessionId=foreign_session["id"],
        ),
        {"model": "model"},
    )
    provider = AgentComposition(screenplay_db)._profile_registry.for_request(
        request
    ).adapter.context_provider

    with pytest.raises(ValueError, match="session does not belong"):
        await provider.build_context(request, _context_budget(request))


@pytest.mark.asyncio
async def test_composition_registry_selects_screenplay_adapter(screenplay_db):
    composition = AgentComposition(screenplay_db)
    request = to_agent_request(_body(), {"model": "model"})
    core = composition.create_core_for_request(request, "key")
    options = agent_run_options(request, {"max_tokens": 2_048})

    assert composition.agent_profile_ids == ("writing", "screenplay")
    assert core._context_provider.__class__.__name__ == (
        "ScreenplayContextProvider"
    )
    assert core._tool_catalog.names == frozenset({
        "getScreenplayProject",
        "getScreenplayDocument",
        "getSourceBookOverview",
        "getSourceCoveragePlan",
        "readSourceCoverageBatch",
        "searchSourceMaterial",
        "readSourcePassages",
        "getSourceCharacters",
        "getSourceWorldSettings",
        "proposeSourceAnalysis",
        "proposeCreativeBrief",
        "proposeBeatSheet",
        "proposeEpisodeOutline",
        "proposeSceneList",
        "proposeSceneDraft",
        "proposeScreenplayReview",
        "proposeScreenplayRevision",
    })
    assert core._tool_catalog.enabled_names(request) == frozenset()
    assert options.context_claims[0].name == SCREENPLAY_PROJECT_CONTEXT
    assert options.response_validators == ()


@pytest.mark.asyncio
async def test_composed_stream_routes_screenplay_profile_end_to_end(
    screenplay_db,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="月背电台",
        source_kind="original",
        source_book_id=None,
        screenplay_format="短片",
        approach="先推情节",
        premise="月球背面只剩一个仍在播音的人。",
    )
    captured_messages: list[dict] = []
    captured_options: dict = {}

    async def _create_chat_stream(
        _key,
        messages,
        options,
        _api_provider,
        signal=None,
    ):
        assert signal is not None
        captured_messages.extend(messages)
        captured_options.update(options)

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "先确认主角为何仍在播音。"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _create_chat_stream,
    )
    composition = AgentComposition(screenplay_db)
    set_agent_composition(composition)
    try:
        chunks = [
            chunk
            async for chunk in _stream_composed_agent(
                body=_body(
                    screenplayProjectId=created["project"]["id"],
                ),
                api_key="key",
                provider_options={"model": "model"},
                signal=asyncio.Event(),
            )
        ]
    finally:
        set_agent_composition(None)
        await composition.shutdown()

    serialized_messages = str(captured_messages)
    assert "PurrTypos 剧本 Agent 工作约定" in serialized_messages
    assert "月背电台" in serialized_messages
    assert "tools" not in captured_options
    assert "".join(chunk.get("delta", "") for chunk in chunks) == (
        "先确认主角为何仍在播音。"
    )
    assert chunks[-1] == {"done": True, "model": "model"}
