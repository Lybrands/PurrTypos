from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

import application.agent_composition as agent_composition_module
import domains.screenplay_agent.candidate_projection as candidate_projection_module
from application.agent_composition import AgentComposition
from application.composition_factory import create_agent_composition
from application.screenplay_agent_task_executor import (
    normalize_screenplay_candidate,
)
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from database.connection import DatabaseConnection
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.tools.catalog import (
    build_screenplay_tool_catalog as build_domain_catalog,
)
from domains.screenplay_agent.tools.schemas import SCREENPLAY_TOOL_SCHEMAS
from infrastructure.screenplay import (
    ScreenplayCandidateArtifacts,
    build_screenplay_tool_catalog,
)
from infrastructure.screenplay.candidate_completion_projector import (
    ScreenplayCandidateCompletionProjector,
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
    RunStatus,
    ToolCall,
    ToolCallDelta,
)
from purra.errors import ContractViolationError, ModelGatewayError
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
    dependency_part_keys: tuple[str, ...] = (),
    deliverable_revision_scope: dict[str, str] | None = None,
    episode_number: int | None = None,
):
    return ScreenplayAgentDomainContext(
        project_id="screenplay-project",
        task_id="screenplay-task",
        unit_id="generate-deliverable",
        target_role=target_role,
        expected_part_type=expected_part_type,
        expected_part_key=expected_part_key,
        tool_access=tool_access,
        dependency_part_keys=dependency_part_keys,
        deliverable_revision_scope=deliverable_revision_scope,
        episode_number=episode_number,
        source_book_id=source_book_id,
        source_scope=source_scope or {"mode": "whole_book"},
    )


async def _seed_running_root(db, root_run_id: str) -> None:
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, root_run_id) "
        "VALUES (?, 1, 'running', 'agent', 'fixture root', ?)",
        [root_run_id, root_run_id],
    )


def test_screenplay_tool_operations_project_only_the_bound_episode_number():
    async def handler(state, arguments, signal):
        del state, arguments, signal
        return {"content": "{}"}

    catalog = build_domain_catalog(
        handlers={name: handler for name in SCREENPLAY_TOOL_SCHEMAS}
    )
    registration = next(
        item
        for item in catalog.registrations()
        if item.schema.name == "writeScreenplayCandidatePart"
    )
    assert registration.operation_display_params is not None
    assert registration.operation_display_params(
        ExecutionState(domain={"boundEpisodeNumber": 7}),
        {"candidate": {"contentText": "不得进入展示元数据"}},
        ToolCall(
            id="call-write-episode-7",
            name="writeScreenplayCandidatePart",
            arguments_json="{}",
        ),
    ) == {"episodeNumber": 7}


async def _seed_task_dependency(
    db,
    *,
    task_id: str = "screenplay-task",
    project_id: str = "screenplay-project",
    dependency_key: str = "section:premise",
    current_unit_id: str = "generate-deliverable",
    direct: bool = True,
    status: str = "completed",
    output: dict | None = None,
) -> None:
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, total_units) "
        "VALUES (?, 'purrtypos.screenplay', 'screenplay', ?, ?, 2)",
        [task_id, project_id, f"run-root-{task_id}"],
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, run_id, metadata_json) VALUES "
        "(?, ?, ?, 0, ?, ?)",
        [
            task_id,
            dependency_key,
            dependency_key,
            f"run-{dependency_key}",
            json.dumps({"unitKind": "generate_document_section"}),
        ],
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, dependencies_json) VALUES "
        "(?, ?, ?, 1, ?)",
        [
            task_id,
            current_unit_id,
            current_unit_id,
            json.dumps([dependency_key] if direct else []),
        ],
    )
    parts = ScreenplayPartArtifactQuery(db)
    ref = await parts.write_host_part(
        project_id=project_id,
        task_id=task_id,
        unit_id=dependency_key,
        semantic_key=dependency_key,
        part_kind="generate_document_section",
        output=output or {
            "contentJson": {"premise": "旧友在停电中重逢。"},
            "contentText": "正文" * 900,
            "sourceRunIds": ["run-source-a", "run-source-b"],
        },
    )
    await db.execute(
        "UPDATE ai_agent_long_task_units SET status = ?, output_ref = ?, "
        "artifact_digest = ?, validation_receipt_json = ? "
        "WHERE task_id = ? AND unit_id = ?",
        [
            status,
            ref.output_ref,
            ref.content_digest,
            json.dumps(dict(ref.validation_receipt)),
            task_id,
            dependency_key,
        ],
    )


def _request(context: ScreenplayAgentDomainContext) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(),
        model=ModelRequest(provider="openai", model="fixture-model"),
        domain_context=context.to_core_context(),
        mode="agent",
        tools_enabled=True,
    )


@pytest.mark.parametrize(
    "contract",
    [
        {"protocol": "unknown", "kind": "generic"},
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "unknown",
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "scene",
            "expectedSceneId": "scene-1",
            "unexpected": True,
        },
    ],
)
def test_candidate_validation_contract_is_strict_and_versioned(contract):
    with pytest.raises(ValueError):
        normalize_screenplay_candidate(
            contract,
            {
                "payload": {"sceneId": "scene-1", "sceneText": "正文"},
                "contentText": "正文",
            },
        )


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "generic",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "generic",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "scene",
                "expectedSceneId": " scene-1 ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "scene",
                "expectedSceneId": "scene-1",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "episode_metadata",
                "episodeNumber": 1,
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "episode_metadata",
                "episodeNumber": 1,
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "review_dimension",
                "episodeNumber": 1,
                "dimension": "continuity",
                "allowedSceneIds": [" scene-1 ", "scene-2"],
                "reviewedDraftId": " draft-1 ",
                "reviewedContentDigest": "A" * 64,
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "review_dimension",
                "episodeNumber": 1,
                "dimension": "continuity",
                "allowedSceneIds": ["scene-1", "scene-2"],
                "reviewedDraftId": "draft-1",
                "reviewedContentDigest": "a" * 64,
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "document_section",
                "sectionKey": " premise ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "document_section",
                "sectionKey": "premise",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "creative_brief_section",
                "sectionKey": " premise ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "creative_brief_section",
                "sectionKey": "premise",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "scene_list_fragment",
                "episodeNumber": 1,
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "scene_list_fragment",
                "episodeNumber": 1,
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_series_arc_index",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_series_arc_index",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_series_arc_phase",
                "phaseKey": " setup ",
                "phaseTitle": " 误入犬域 ",
                "phaseObjective": " 建立目标 ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_series_arc_phase",
                "phaseKey": "setup",
                "phaseTitle": "误入犬域",
                "phaseObjective": "建立目标",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_episode_plan_index",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_episode_plan_index",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_episode_plan_fragment",
                "episodeNumber": 2,
                "episodeId": " ep02 ",
                "episodeTitle": " 绝境觉醒 ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_episode_plan_fragment",
                "episodeNumber": 2,
                "episodeId": "ep02",
                "episodeTitle": "绝境觉醒",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_character_arcs_index",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_character_arcs_index",
            },
        ),
        (
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_character_arc_fragment",
                "characterKey": " linyue ",
                "characterName": " 林月 ",
            },
            {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "structure_character_arc_fragment",
                "characterKey": "linyue",
                "characterName": "林月",
            },
        ),
    ],
)
def test_candidate_validation_contract_has_one_canonical_parser(contract, expected):
    parser = getattr(
        candidate_projection_module,
        "parse_candidate_validation_contract",
        None,
    )
    assert parser is not None
    assert parser(contract) == expected


@pytest.mark.parametrize(
    "contract",
    [
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "episode_metadata",
            "episodeNumber": True,
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "episode_metadata",
            "episodeNumber": 0,
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "scene",
            "expectedSceneId": "x" * 257,
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "scene",
            "expectedSceneId": "   ",
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "review_dimension",
            "episodeNumber": 1,
            "dimension": "continuity",
            "allowedSceneIds": ["scene-1", "scene-1"],
            "reviewedDraftId": "draft-1",
            "reviewedContentDigest": "a" * 64,
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "review_dimension",
            "episodeNumber": 1,
            "dimension": "continuity",
            "allowedSceneIds": ["scene-1"],
            "reviewedDraftId": "draft-1",
            "reviewedContentDigest": "not-a-digest",
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "scene",
            "expectedSceneId": {"nested": "scene-1"},
        },
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "generic",
            "content": "must not enter the contract",
        },
    ],
)
def test_candidate_validation_contract_rejects_untrusted_json_types(contract):
    parser = getattr(
        candidate_projection_module,
        "parse_candidate_validation_contract",
        None,
    )
    assert parser is not None
    with pytest.raises(ValueError):
        parser(contract)


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


async def test_source_analysis_profiles_expose_only_their_required_tools(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    chapter_tools = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        source_scope={
            "mode": "selected_chapters",
            "chapterIds": ["chapter-1"],
        },
        target_role="sourceAnalysis",
        expected_part_type="document_section",
        expected_part_key="source_digest:chapter:chapter-1",
        tool_access="source_chapter_digest",
    )))
    reduction_tools = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="sourceAnalysis",
        expected_part_type="document_section",
        expected_part_key="source_digest:reduce:1:1",
        tool_access="source_digest_reduction",
        dependency_part_keys=("source-analysis:chapter:chapter-1",),
    )))
    section_tools = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="sourceAnalysis",
        expected_part_type="document_section",
        expected_part_key="characters",
        tool_access="source_analysis_section",
        dependency_part_keys=("source-analysis:reduce:1:1",),
    )))

    assert chapter_tools == frozenset({
        "readSourceChapters",
        "writeScreenplayCandidatePart",
    })
    assert reduction_tools == frozenset({
        "readScreenplayTaskDependencies",
        "writeScreenplayCandidatePart",
    })
    assert section_tools == reduction_tools


async def test_source_chapter_digest_run_scope_cannot_read_a_sibling_chapter(
    screenplay_tool_db,
):
    await _install_source_project(screenplay_tool_db, restricted=False)
    await screenplay_tool_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-2', 'writing-source', '第二章', 2)"
    )
    await screenplay_tool_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-2", json.dumps({
            "root": {"children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": "第二章正文"}],
            }]},
        }, ensure_ascii=False)],
    )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "readSourceChapters")
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "source-analysis:chapter:chapter-1",
            "targetRole": "sourceAnalysis",
            "expectedPartType": "document_section",
            "expectedPartKey": "source_digest:chapter:chapter-1",
            "sourceBookId": "source-book",
            "sourceScope": {
                "mode": "selected_chapters",
                "chapterIds": ["chapter-1"],
            },
            "toolAccess": "source_chapter_digest",
        },
        run_id="run-source-chapter-1",
    )

    sibling = await read(state, {"chapterIds": ["chapter-2"]})
    own = await read(state, {"chapterIds": ["chapter-1"]})

    assert sibling.error_code == "tool_input_invalid"
    assert json.loads(sibling.content)["invalidChapterIds"] == ["chapter-2"]
    assert own.error_code is None
    assert json.loads(own.content)["chapters"][0]["chapterId"] == "chapter-1"
    receipt = await screenplay_tool_db.fetch_one(
        "SELECT agent_run_id, tool_name, source_type, source_id, coverage_mode "
        "FROM screenplay_source_receipts WHERE agent_run_id = ?",
        ["run-source-chapter-1"],
    )
    assert receipt == {
        "agent_run_id": "run-source-chapter-1",
        "tool_name": "readSourceChapters",
        "source_type": "chapter",
        "source_id": "chapter-1",
        "coverage_mode": "full",
    }


async def test_host_captured_scene_exposes_reads_but_no_model_writer(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    enabled = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="screenplayDraft",
        expected_part_type="scene",
        expected_part_key="ep01_s04",
    )))

    assert "getScreenplayEpisodeContext" in enabled
    assert "readSourceChapters" in enabled
    assert "writeScreenplayCandidatePart" not in enabled


async def test_root_run_exposes_scoped_reads_but_no_candidate_writer(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    root = ScreenplayAgentDomainContext(
        project_id="screenplay-project",
        turn_id="turn-root-tools",
    )
    enabled = catalog.enabled_names(_request(root))

    assert "inspectScreenplayProject" in enabled
    assert "readScreenplayDeliverable" in enabled
    assert "getScreenplayEpisodeContext" in enabled
    assert "readSourceChapters" not in enabled
    assert "writeScreenplayCandidatePart" not in enabled


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


async def test_part_tool_profiles_expose_only_their_declared_capabilities(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    draft = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="screenplayDraft",
        expected_part_type="scene",
        tool_access="draft_scene",
    )))
    metadata = catalog.enabled_names(_request(_context(
        target_role="screenplayDraft",
        expected_part_type="episode_metadata",
        tool_access="episode_metadata",
    )))
    review = catalog.enabled_names(_request(_context(
        target_role="review",
        expected_part_type="review_dimension",
        tool_access="review_dimension",
    )))
    scene_list = catalog.enabled_names(_request(_context(
        target_role="sceneList",
        expected_part_type="document_section",
        expected_part_key="episode-1",
        tool_access="scene_list_episode",
        deliverable_revision_scope={"structure": "structure-1"},
        episode_number=1,
    )))
    document = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        expected_part_type="document_section",
        tool_access="creative_brief_section",
    )))
    series_index = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="structure",
        expected_part_type="document_section",
        tool_access="series_arc_index",
    )))
    series_phase = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="structure",
        expected_part_type="document_section",
        tool_access="series_arc_phase",
    )))
    character_fragment = catalog.enabled_names(_request(_context(
        source_book_id="source-book",
        target_role="structure",
        expected_part_type="document_section",
        tool_access="character_arc_fragment",
    )))
    final_response = catalog.enabled_names(_request(_context(
        expected_part_type="public_response",
        tool_access="final_response",
    )))

    assert "readSourceChapters" in draft
    assert "inspectScreenplayProject" not in draft
    assert "readScreenplayDeliverable" not in draft
    assert "readScreenplayTaskDependencies" in draft
    assert "writeScreenplayCandidatePart" not in draft
    assert metadata == {
        "readScreenplayTaskDependencies",
        "writeScreenplayCandidatePart",
    }
    assert review == {
        "getScreenplayEpisodeContext",
        "writeScreenplayCandidatePart",
    }
    assert scene_list == {
        "readScreenplayDeliverable",
        "writeScreenplayCandidatePart",
    }
    assert document == {
        "readScreenplayDeliverable",
        "readScreenplayTaskDependencies",
        "writeScreenplayCandidatePart",
    }
    assert "writeScreenplayCandidatePart" in series_index
    assert "readScreenplayTaskDependencies" not in series_index
    assert "readScreenplayTaskDependencies" in series_phase
    assert "readScreenplayTaskDependencies" in character_fragment
    assert final_response == set()


async def test_task_dependency_tool_schema_is_model_owned_and_bounded(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    registration = next(
        item for item in catalog.registrations()
        if item.schema.name == "readScreenplayTaskDependencies"
    )
    schema = registration.schema.parameters

    assert set(schema["properties"]) == {"partKeys"}
    assert schema["required"] == ["partKeys"]
    assert schema["properties"]["partKeys"]["minItems"] == 1
    assert schema["properties"]["partKeys"]["maxItems"] == 12
    assert schema["properties"]["partKeys"]["uniqueItems"] is True
    assert "不得重复" in schema["properties"]["partKeys"]["description"]
    assert "dependencyPartKeys" in registration.data_contract.host_bound_paths


async def test_task_dependency_read_returns_only_compact_completed_direct_parts(
    screenplay_tool_db,
):
    await _seed_task_dependency(screenplay_tool_db)
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "readScreenplayTaskDependencies")
    state = ExecutionState(
        domain=_context(
            tool_access="creative_brief_section",
            dependency_part_keys=("section:premise",),
        ).to_core_context().payload,
        run_id="run-current",
    )

    result = await read(state, {"partKeys": ["section:premise"]})

    assert result.error_code is None
    payload = json.loads(result.content)
    assert payload == {
        "dependencies": [{
            "partKey": "section:premise",
            "kind": "generate_document_section",
            "contentJson": {"premise": "旧友在停电中重逢。"},
            "contentTextTail": ("正文" * 600),
            "sourceRunRefs": [
                "run-section:premise",
                "run-source-a",
                "run-source-b",
            ],
        }],
    }
    encoded = result.content
    for secret in (
        "artifactId",
        "outputRef",
        "screenplay-task",
        "generate-deliverable",
        "screenplay-project",
    ):
        assert secret not in encoded


async def test_task_dependency_read_rejects_scope_status_and_cardinality_errors(
    screenplay_tool_db,
):
    await _seed_task_dependency(screenplay_tool_db)
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "readScreenplayTaskDependencies")

    async def rejected(domain, part_keys):
        result = await read(
            ExecutionState(domain=domain, run_id="run-current"),
            {"partKeys": part_keys},
        )
        assert result.error_code == "tool_input_invalid"

    allowed = _context(
        tool_access="creative_brief_section",
        dependency_part_keys=("section:premise",),
    ).to_core_context().payload
    await rejected(allowed, ["section:not-allowed"])
    await rejected(allowed, ["section:premise", "section:premise"])
    await rejected(allowed, [f"section:{index}" for index in range(13)])

    await screenplay_tool_db.execute(
        "UPDATE ai_agent_long_task_units SET status = 'pending' "
        "WHERE task_id = 'screenplay-task' AND unit_id = 'section:premise'"
    )
    await rejected(allowed, ["section:premise"])
    await screenplay_tool_db.execute(
        "UPDATE ai_agent_long_task_units SET status = 'completed' "
        "WHERE task_id = 'screenplay-task' AND unit_id = 'section:premise'"
    )
    await screenplay_tool_db.execute(
        "UPDATE ai_agent_long_task_units SET dependencies_json = '[]' "
        "WHERE task_id = 'screenplay-task' AND unit_id = 'generate-deliverable'"
    )
    await rejected(allowed, ["section:premise"])

    wrong_project = dict(allowed)
    wrong_project["projectId"] = "another-project"
    await rejected(wrong_project, ["section:premise"])
    wrong_task = dict(allowed)
    wrong_task["taskId"] = "another-task"
    await rejected(wrong_task, ["section:premise"])
    wrong_unit = dict(allowed)
    wrong_unit["unitId"] = "another-unit"
    await rejected(wrong_unit, ["section:premise"])


async def test_candidate_completion_requires_the_recorded_dependency_read(
    screenplay_tool_db,
):
    projector = ScreenplayCandidateCompletionProjector(
        screenplay_tool_db,
        candidate_normalizer=normalize_screenplay_candidate,
    )
    scope = {"dependencyPartKeys": ["section:premise"]}

    with pytest.raises(ValueError, match="dependencies were not read"):
        await projector._validate_dependency_read("run-dependency-read", scope)

    operation_id = "operation-read-dependency"
    await screenplay_tool_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, sequence, source, kind, "
        "channel, visibility, source_event_key) VALUES "
        "(?, 'operation.started', ?, 'event-dependency-started', 1, 'runtime', "
        "'operation.started', 'operation', 'public', 'dependency-started'), "
        "(?, 'operation.finished', ?, 'event-dependency-finished', 2, 'runtime', "
        "'operation.finished', 'operation', 'public', 'dependency-finished')",
        [
            "run-dependency-read",
            json.dumps({
                "operationId": operation_id,
                "kind": "tool",
                "display": {"labelParams": {
                    "toolCallId": "call-read-dependency",
                    "toolName": "readScreenplayTaskDependencies",
                }},
            }),
            "run-dependency-read",
            json.dumps({"operationId": operation_id, "status": "succeeded"}),
        ],
    )

    await projector._validate_dependency_read("run-dependency-read", scope)


async def test_draft_scene_completion_evidence_requires_episode_context_read(
    screenplay_tool_db,
):
    projector = ScreenplayCandidateCompletionProjector(
        screenplay_tool_db,
        candidate_normalizer=normalize_screenplay_candidate,
    )
    assert not await projector._has_successful_tool_read(
        "run-draft-read",
        "getScreenplayEpisodeContext",
    )
    for sequence, tool_name in enumerate(
        ("inspectSourceStructure", "getScreenplayEpisodeContext"),
        start=1,
    ):
        operation_id = f"operation-draft-read-{sequence}"
        await screenplay_tool_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'operation.started', ?, ?, ?, 'runtime', "
            "'operation.started', 'operation', 'public', ?), "
            "(?, 'operation.finished', ?, ?, ?, 'runtime', "
            "'operation.finished', 'operation', 'public', ?)",
            [
                "run-draft-read",
                json.dumps({
                    "operationId": operation_id,
                    "kind": "tool",
                    "display": {"labelParams": {"toolName": tool_name}},
                }),
                f"event-draft-read-start-{sequence}",
                sequence * 2 - 1,
                f"draft-read-start-{sequence}",
                "run-draft-read",
                json.dumps({"operationId": operation_id, "status": "succeeded"}),
                f"event-draft-read-finish-{sequence}",
                sequence * 2,
                f"draft-read-finish-{sequence}",
            ],
        )
        has_episode_context = await projector._has_successful_tool_read(
            "run-draft-read",
            "getScreenplayEpisodeContext",
        )
        assert has_episode_context is (tool_name == "getScreenplayEpisodeContext")


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
        "SELECT id, status FROM ai_agent_artifacts WHERE created_by_run_id = ?",
        [state.run_id],
    )
    assert artifact is not None
    assert artifact["status"] == "open"
    assert await screenplay_tool_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_batches "
        "WHERE artifact_id = ?",
        [artifact["id"]],
    ) == {"count": 0}


async def test_candidate_normalizer_must_be_deterministic_before_artifact_write(
    screenplay_tool_db,
):
    calls = 0

    def unstable(contract, candidate):
        nonlocal calls
        del contract
        calls += 1
        value = dict(candidate)
        value["payload"] = {**dict(value["payload"]), "call": calls}
        return value

    artifacts = ScreenplayCandidateArtifacts(
        screenplay_tool_db,
        candidate_normalizer=unstable,
    )
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "section:premise",
            "targetRole": "creativeBrief",
            "expectedPartType": "document_section",
            "expectedPartKey": "premise",
            "candidateValidation": {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "generic",
            },
        },
        run_id="run-unstable-normalizer",
    )

    with pytest.raises(ValueError, match="not deterministic"):
        await artifacts.write(state, {"candidate": {
            "sectionKey": "premise",
            "title": "故事前提",
            "contentText": "正文",
            "contentJson": {"premise": "人物重新相遇。"},
        }})

    assert await screenplay_tool_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
        "WHERE created_by_run_id = ?",
        [state.run_id],
    ) == {"count": 0}


@pytest.mark.parametrize(
    ("kind", "expected_part_key", "contract_fields", "target_role"),
    [
        (
            "creative_brief_section",
            "premise",
            {"sectionKey": "premise"},
            "creativeBrief",
        ),
        ("structure_series_arc_index", "series_arc:index", {}, "structure"),
        (
            "structure_series_arc_phase",
            "series_arc:phase:setup",
            {
                "phaseKey": "setup",
                "phaseTitle": "建立目标",
                "phaseObjective": "使人物进入主线冲突",
            },
            "structure",
        ),
        (
            "structure_character_arcs_index",
            "character_arcs:index",
            {},
            "structure",
        ),
        (
            "structure_character_arc_fragment",
            "character_arcs:character:protagonist",
            {"characterKey": "protagonist", "characterName": "主角"},
            "structure",
        ),
    ],
)
async def test_candidate_artifact_accepts_every_bound_document_scope(
    screenplay_tool_db,
    kind,
    expected_part_key,
    contract_fields,
    target_role,
):
    artifacts = ScreenplayCandidateArtifacts(
        screenplay_tool_db,
        candidate_normalizer=lambda contract, candidate: candidate,
    )
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": f"unit:{kind}",
            "targetRole": target_role,
            "expectedPartType": "document_section",
            "expectedPartKey": expected_part_key,
            "candidateValidation": {
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": kind,
                **contract_fields,
            },
        },
        run_id=f"run-{kind}",
    )
    await screenplay_tool_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, source, "
        "kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.lifecycle', '{}', ?, 'turn-scope-test', 1, 'runtime', "
        "'run.lifecycle', 'lifecycle', 'public', ?)",
        [
            state.run_id,
            f"event-{kind}",
            f"run:{state.run_id}:running",
        ],
    )

    result = await artifacts.write(state, {"candidate": {
        "sectionKey": expected_part_key,
        "title": "候选章节",
        "contentText": "有界候选内容。",
        "contentJson": {"items": []},
    }})

    assert result["acceptedParts"] == 1


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


async def test_creative_brief_deliverable_read_is_bound_to_exact_revision_scope(
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
    for revision_id, number in (("brief-old", 1), ("brief-bound", 2)):
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
            [revision_id, revision_id, f"part-{number}"],
        )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "readScreenplayDeliverable")
    state = ExecutionState(
        domain=_context(
            expected_part_type="document_section",
            expected_part_key="premise",
            tool_access="creative_brief_section",
            deliverable_revision_scope={"creativeBrief": "brief-bound"},
        ).to_core_context().payload,
        run_id="run-brief-bound-read",
    )

    bound = await read(state, {"role": "creativeBrief"})
    old = await read(
        state,
        {"role": "creativeBrief", "revisionId": "brief-old"},
    )
    wrong_role = await read(state, {"role": "sourceAnalysis"})

    assert json.loads(bound.content)["revisionId"] == "brief-bound"
    assert old.error_code == "tool_input_invalid"
    assert wrong_role.error_code == "tool_input_invalid"


async def test_scene_list_structure_read_is_bound_to_one_revision_and_episode(
    screenplay_tool_db,
):
    await screenplay_tool_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('screenplay-project', '原创项目', 'original', '{}')"
    )
    deliverable = await screenplay_tool_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = 'screenplay-project' AND role = 'structure'"
    )
    assert deliverable is not None
    for revision_id, revision_no in (("structure-old", 1), ("structure-bound", 2)):
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, "
            "summary_json, created_by) VALUES (?, 'screenplay-project', ?, "
            "?, ?, '{}', 'test')",
            [revision_id, deliverable["id"], revision_no, f"digest-{revision_no}"],
        )
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES "
            "(?, 'document', 'main', 0, ?, '完整结构不得泄露', ?)",
            [
                revision_id,
                json.dumps({"episodes": [
                    {"number": 1, "title": "第一集"},
                    {"number": 2, "title": f"第二集-{revision_id}"},
                ]}, ensure_ascii=False),
                f"part-{revision_no}",
            ],
        )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "readScreenplayDeliverable")
    state = ExecutionState(
        domain=_context(
            target_role="sceneList",
            expected_part_type="document_section",
            expected_part_key="episode-2",
            tool_access="scene_list_episode",
            deliverable_revision_scope={"structure": "structure-bound"},
            episode_number=2,
        ).to_core_context().payload,
        run_id="run-scene-list-episode-2",
    )

    bound = json.loads((await read(state, {"role": "structure"})).content)
    wrong_episode = await read(
        state,
        {"role": "structure", "episodeNumber": 1},
    )
    wrong_revision = await read(
        state,
        {"role": "structure", "revisionId": "structure-old"},
    )

    assert bound["revisionId"] == "structure-bound"
    assert bound["payload"] == {
        "episode": {"number": 2, "title": "第二集-structure-bound"},
    }
    assert bound["contentText"] == ""
    assert wrong_episode.error_code == "tool_input_invalid"
    assert wrong_revision.error_code == "tool_input_invalid"


async def test_episode_context_is_bound_to_host_episode_and_revisions(
    screenplay_tool_db,
):
    await screenplay_tool_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('screenplay-project', '原创项目', 'original', '{}')"
    )
    for role, revision_id, payload in (
        (
            "sceneList",
            "scene-list-bound",
            {"episodeNumber": 1, "scenes": [{"id": "scene-1"}]},
        ),
        (
            "screenplayDraft",
            "draft-bound",
            {"episodeNumber": 1, "title": "绑定草稿"},
        ),
    ):
        deliverable = await screenplay_tool_db.fetch_one(
            "SELECT id FROM screenplay_deliverables "
            "WHERE project_id = 'screenplay-project' AND role = ?",
            [role],
        )
        assert deliverable is not None
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, "
            "summary_json, created_by) VALUES (?, 'screenplay-project', ?, "
            "1, ?, '{}', 'test')",
            [revision_id, deliverable["id"], f"digest-{role}"],
        )
        await screenplay_tool_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES "
            "(?, 'episode', '1', 1, ?, '', ?)",
            [
                revision_id,
                json.dumps(payload, ensure_ascii=False),
                f"part-{role}",
            ],
        )
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    read = _tool_handler(catalog, "getScreenplayEpisodeContext")
    state = ExecutionState(
        domain=_context(
            target_role="review",
            expected_part_type="review_dimension",
            expected_part_key="1:continuity",
            tool_access="review_dimension",
            deliverable_revision_scope={
                "sceneList": "scene-list-bound",
                "screenplayDraft": "draft-bound",
            },
            episode_number=1,
        ).to_core_context().payload,
        run_id="run-review-bound-context",
    )

    bound = json.loads((await read(state, {"episodeNumber": 1})).content)
    wrong_episode = await read(state, {"episodeNumber": 2})
    wrong_revision = await read(state, {
        "episodeNumber": 1,
        "draftRevisionId": "draft-other",
    })

    assert bound["sceneListId"] == "scene-list-bound"
    assert bound["currentDraft"]["title"] == "绑定草稿"
    assert wrong_episode.error_code == "tool_input_invalid"
    assert wrong_revision.error_code == "tool_input_invalid"


async def test_original_creative_brief_with_explicit_empty_scope_can_write_without_read(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = _tool_handler(catalog, "writeScreenplayCandidatePart")
    state = ExecutionState(
        domain=_context(
            expected_part_type="document_section",
            expected_part_key="premise",
            tool_access="creative_brief_section",
            deliverable_revision_scope={},
        ).to_core_context().payload,
        run_id="run-original-brief-no-evidence",
    )

    result = await write(state, {"candidate": {
        "sectionKey": "premise",
        "title": "故事前提",
        "contentText": "旧友在停电夜重逢。",
        "contentJson": {"fields": {"premise": "旧友在停电夜重逢。"}},
    }})

    assert result.error_code is None


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


async def test_document_candidate_requires_a_successful_recorded_read_operation(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = _tool_handler(catalog, "writeScreenplayCandidatePart")
    state = ExecutionState(
        domain={
            "projectId": "screenplay-project",
            "taskId": "screenplay-task",
            "unitId": "section:premise",
            "targetRole": "creativeBrief",
            "expectedPartType": "document_section",
            "expectedPartKey": "premise",
            "toolAccess": "creative_brief_section",
        },
        run_id="run-read-before-document-write",
    )
    candidate = {
        "sectionKey": "premise",
        "title": "故事前提",
        "executionSummary": "根据项目目标完成故事前提。",
        "contentText": "两名旧友因一场停电再次合作。",
        "contentJson": {"fields": {"premise": "旧友在停电中重逢。"}},
    }

    rejected = await write(state, {"candidate": candidate})
    assert rejected.error_code == "tool_input_invalid"
    assert "before its evidence is read" in rejected.content

    operation_id = "operation-read-project"
    await screenplay_tool_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, sequence, source, kind, "
        "channel, visibility, source_event_key) VALUES "
        "(?, 'operation.started', ?, 'event-read-started', 1, 'runtime', "
        "'operation.started', 'operation', 'public', 'read-started'), "
        "(?, 'operation.finished', ?, 'event-read-finished', 2, 'runtime', "
        "'operation.finished', 'operation', 'public', 'read-finished')",
        [
            state.run_id,
            json.dumps({
                "operationId": operation_id,
                "kind": "tool",
                "display": {"labelParams": {
                    "toolCallId": "call-read-project",
                    "toolName": "inspectScreenplayProject",
                }},
            }),
            state.run_id,
            json.dumps({"operationId": operation_id, "status": "succeeded"}),
        ],
    )

    accepted = await write(state, {"candidate": candidate})
    assert accepted.error_code is None
    assert json.loads(accepted.content)["acceptedParts"] == 1


async def test_scoped_creative_brief_requires_deliverable_read_not_any_read(
    screenplay_tool_db,
):
    catalog = build_screenplay_tool_catalog(db=screenplay_tool_db)
    write = _tool_handler(catalog, "writeScreenplayCandidatePart")
    state = ExecutionState(
        domain=_context(
            expected_part_type="document_section",
            expected_part_key="premise",
            tool_access="creative_brief_section",
            deliverable_revision_scope={"sourceAnalysis": "analysis-bound"},
        ).to_core_context().payload,
        run_id="run-scoped-brief-read-kind",
    )
    candidate = {
        "sectionKey": "premise",
        "title": "故事前提",
        "contentText": "旧友在停电夜重逢。",
        "contentJson": {"fields": {"premise": "旧友在停电夜重逢。"}},
    }

    for sequence, tool_name in enumerate(
        ("readScreenplayTaskDependencies", "readScreenplayDeliverable"),
        start=1,
    ):
        operation_id = f"operation-scoped-brief-{sequence}"
        await screenplay_tool_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'operation.started', ?, ?, ?, 'runtime', "
            "'operation.started', 'operation', 'public', ?), "
            "(?, 'operation.finished', ?, ?, ?, 'runtime', "
            "'operation.finished', 'operation', 'public', ?)",
            [
                state.run_id,
                json.dumps({
                    "operationId": operation_id,
                    "kind": "tool",
                    "display": {"labelParams": {"toolName": tool_name}},
                }),
                f"event-scoped-brief-start-{sequence}",
                sequence * 2 - 1,
                f"scoped-brief-start-{sequence}",
                state.run_id,
                json.dumps({"operationId": operation_id, "status": "succeeded"}),
                f"event-scoped-brief-finish-{sequence}",
                sequence * 2,
                f"scoped-brief-finish-{sequence}",
            ],
        )
        result = await write(state, {"candidate": candidate})
        if tool_name == "readScreenplayTaskDependencies":
            assert result.error_code == "tool_input_invalid"
        else:
            assert result.error_code is None


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


class _TaskValidationRetryGateway:
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
            if call_number in {1, 3}:
                scene_id = "wrong-scene" if call_number == 1 else "scene-1"
                arguments = json.dumps({
                    "candidate": {
                        "episodeNumber": 1,
                        "reviewDimension": "continuity",
                        "title": "第 1 集 continuity 审阅",
                        "contentText": "发现一个连续性问题。",
                        "contentJson": {
                            "verdict": "revise",
                            "issues": [{
                                "id": "issue-1",
                                "severity": "major",
                                "description": "证物出现顺序不一致。",
                                "sceneIds": [scene_id],
                            }],
                        },
                    },
                }, ensure_ascii=False)
                yield ModelStreamChunk(
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id=f"call-write-review-{call_number}",
                        type="function",
                        name="writeScreenplayCandidatePart",
                        arguments_fragment=arguments,
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )
                return
            yield ModelStreamChunk(
                content_delta="候选审阅已写入。",
                finish_reason=ModelFinishReason.STOP,
            )

        return ModelStream(chunks=chunks(), model="fixture-model")

    async def complete(self, messages, invocation, signal=None):
        del messages, invocation, signal
        return ModelCompletion(
            message={"role": "assistant", "content": "unused"},
            model="fixture-model",
        )
