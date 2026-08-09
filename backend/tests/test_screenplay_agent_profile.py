from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError

from agent_core.contracts import ContextBudget
from application.composition_factory import create_agent_composition
from application.request_mapping import (
    to_agent_request as to_writing_agent_request,
)
from application.screenplay_agent_request_mapping import (
    screenplay_run_options,
    to_screenplay_agent_request,
)
from database.connection import DatabaseConnection
from tests.support import screenplay_v2_driver as screenplay_crud
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
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest


@pytest_asyncio.fixture
async def screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _body(**updates) -> ScreenplayAgentRunRequest:
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
    return ScreenplayAgentRunRequest(**payload)


def to_agent_request(body, provider_options):
    if isinstance(body, ScreenplayAgentRunRequest):
        return to_screenplay_agent_request(body, provider_options)
    return to_writing_agent_request(body, provider_options)


def agent_run_options(request, provider_options):
    del provider_options
    return screenplay_run_options(request)


def _context_budget(request) -> ContextBudget:
    return ContextBudget(
        window_tokens=128_000,
        output_reserve_tokens=8_192,
        safety_reserve_tokens=4_096,
        runtime_reserve_tokens=4_096,
        minimum_message_tokens=128,
        provider_input_tokens=111_616,
        context_allocations={SCREENPLAY_PROJECT_CONTEXT: 64_000},
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


def test_screenplay_operation_scope_is_normalized_and_profile_bound():
    body = _body(screenplayOperationId=" operation-1 ")
    request = to_agent_request(body, {"model": "model"})

    assert body.screenplayOperationId == "operation-1"
    assert "screenplayOperationId" not in request.metadata
    writing_body = ChatStreamRequest(
        messages=[{"role": "user", "content": "继续写"}],
        apiKey="key",
        options={"model": "model"},
        screenplayOperationId="operation-1",
    )
    assert not hasattr(writing_body, "screenplayOperationId")


def test_screenplay_request_mapping_uses_opaque_domain_context():
    request = to_agent_request(
        _body(
            sourceBookId="book-1",
            activeDocumentId="doc-1",
            contextWindow="128k",
            screenplayTaskIntent="stage_deliverable",
            screenplayDraftSceneCount=3,
            screenplayDraftScope="next_episode",
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
    assert context.task_intent == "stage_deliverable"
    assert context.draft_scene_count == 3
    assert context.draft_scope == "next_episode"
    assert request.context_window == 128_000
    assert request.tools_enabled is False


def test_screenplay_request_accepts_a_bounded_dynamic_episode_scope():
    request = to_agent_request(
        _body(
            activeStage="draft",
            screenplayTaskIntent="stage_deliverable",
            screenplayDraftScope="next_12_episodes",
        ),
        {"model": "model"},
    )
    context = ScreenplayDomainContext.from_core_context(
        request.domain_context
    )

    assert context.draft_scope == "next_12_episodes"
    with pytest.raises(
        ValidationError,
        match="unsupported screenplay draft scope",
    ):
        _body(screenplayDraftScope="next_101_episodes")


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
        activeStage="brief",
    )
    request = to_agent_request(body, {"model": "model"})
    composition = create_agent_composition(screenplay_db)
    registration = composition._profile_registry.for_request(request)
    provider = registration.adapter.context_provider
    demands = await provider.describe_context_demands(request)
    application_demands = await composition.resolve_context_claims(request)
    bundle = await provider.build_context(request, _context_budget(request))

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
    assert demands[0].desired_tokens == bundle.diagnostics[
        "screenplayProjectDesiredTokens"
    ]
    assert application_demands == demands


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
    composition = create_agent_composition(screenplay_db)
    provider = composition._profile_registry.for_request(
        request
    ).adapter.context_provider
    with pytest.raises(ValueError, match="source book scope"):
        await provider.build_context(request, _context_budget(request))


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
    provider = create_agent_composition(screenplay_db)._profile_registry.for_request(
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
    assert "先调用 getSourceCoveragePlan" not in (
        blocks[SCREENPLAY_POLICY_CONTEXT].content
    )
    assert "此阶段不得直接形成创作简报" in (
        blocks[SCREENPLAY_POLICY_CONTEXT].content
    )
    facts = bundle.diagnostics["hostPlanningFacts"]
    assert facts["screenplayStage"] == "orientation"
    assert facts["planningMode"] == "dynamic-within-stage"
    assert facts["deliverable"] == "A reviewable source-range analysis."
    assert facts["sourceAvailable"] is True
    assert "Do not create the adaptation creative brief" in " ".join(
        facts["boundaries"]
    )


@pytest.mark.asyncio
async def test_restricted_scope_hides_book_global_tools_before_planning(
    screenplay_db,
):
    await screenplay_db.execute(
        "INSERT INTO books (id, title) VALUES ('restricted-book', '限定原作')"
    )
    await screenplay_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('restricted-writing', '写作目录', 'writing', 'restricted-book')"
    )
    await screenplay_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('restricted-chapter', 'restricted-writing', '第一章', 1)"
    )
    created = await screenplay_crud.create_project(
        screenplay_db,
        title="限定范围项目",
        source_kind="book",
        source_book_id="restricted-book",
        screenplay_format="短片",
        approach="截取改编",
        premise="",
        source_scope={"mode": "first_chapters", "count": 1},
    )
    request = to_agent_request(
        _body(
            screenplayProjectId=created["project"]["id"],
            sourceBookId="restricted-book",
            enableAgentTools=True,
        ),
        {"model": "model"},
    )
    composition = create_agent_composition(screenplay_db)
    try:
        prepared = await composition.prepare_request(request)
        context = ScreenplayDomainContext.from_core_context(
            prepared.domain_context
        )
        core = composition.create_core_for_request(prepared, "key")
        enabled = core._tool_catalog.enabled_names(prepared)
    finally:
        await composition.shutdown()

    assert context.source_scope_restricted is True
    assert "getSourceCoveragePlan" in enabled
    assert "readSourcePassages" in enabled
    assert "getSourceCharacters" not in enabled
    assert "getSourceWorldSettings" not in enabled


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
    provider = create_agent_composition(screenplay_db)._profile_registry.for_request(
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
            activeStage="brief",
        ),
        {"model": "model"},
    )
    provider = create_agent_composition(screenplay_db)._profile_registry.for_request(
        request
    ).adapter.context_provider

    with pytest.raises(ValueError, match="session does not belong"):
        await provider.build_context(request, _context_budget(request))


@pytest.mark.asyncio
async def test_composition_registry_selects_screenplay_adapter(screenplay_db):
    composition = create_agent_composition(screenplay_db)
    request = to_agent_request(_body(), {"model": "model"})
    core = composition.create_core_for_request(request, "key")
    options = agent_run_options(request, {"max_tokens": 2_048})

    assert composition.agent_profile_ids == ("writing", "screenplay")
    assert core._context_provider.__class__.__name__ == (
        "ScreenplayContextProvider"
    )
    assert core._runtime_limits.max_model_rounds == 12
    assert core._tool_catalog.names == frozenset({
            "getScreenplayProject",
                "getScreenplayDocument",
                "getScreenplayEpisodeContext",
                "getScreenplayDraftContext",
        "getSourceBookOverview",
        "getSourceCoveragePlan",
        "readSourceCoverageBatch",
        "searchSourceMaterial",
        "readSourcePassages",
        "getSourceCharacters",
        "getSourceWorldSettings",
        "beginSourceAnalysisArtifact",
        "appendSourceAnalysisBatch",
        "finalizeSourceAnalysisProposal",
        "beginCreativeBriefArtifact",
        "appendCreativeBriefBatch",
        "finalizeCreativeBriefProposal",
        "beginScreenplayStructureArtifact",
        "appendScreenplayStructureBatch",
        "finalizeScreenplayStructureProposal",
        "beginSceneListArtifact",
        "appendSceneListBatch",
        "finalizeSceneListProposal",
        "proposeSceneDraft",
        "beginScreenplayReviewArtifact",
        "appendScreenplayReviewBatch",
        "finalizeScreenplayReviewProposal",
        "beginScreenplayRevisionArtifact",
        "appendScreenplayRevisionBatch",
        "appendScreenplayRevisionResolutionBatch",
        "finalizeScreenplayRevisionProposal",
    })
    assert core._tool_catalog.enabled_names(request) == frozenset()
    assert options.context_claims == ()
    assert options.response_validators == ()
