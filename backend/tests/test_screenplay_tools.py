from __future__ import annotations

import json
from dataclasses import replace
from itertools import count
from pathlib import Path

import pytest
import pytest_asyncio

from application.screenplay_agent_request_mapping import (
    to_screenplay_agent_request,
)
from agent_core.artifacts import ArtifactScope, ArtifactWriteClaimCommand
from agent_core.contracts import (
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
    ToolEffectState,
    ToolStepDisposition,
)
from agent_core.json_values import thaw_json_mapping
from agent_core.plan_compiler import projected_planning_tool_names
from agent_core.tools.executor import CoreToolExecutor
from agent_core.work_items import WorkItemRunLinkCommand, WorkItemRunRelation
from database.connection import DatabaseConnection
from tests.support import screenplay_v2_driver as screenplay_crud
from tests.support.screenplay_v2_driver import (
    list_source_refs,
    record_source_refs,
)
from domains.screenplay.execution_state import ScreenplayExecutionStateFactory
from domains.screenplay.contracts import ScreenplayDomainContext
from domains.screenplay.payload_limits import SCENE_DRAFT_PAYLOAD_LIMITS
from domains.screenplay.tool_contracts import (
    SCREENPLAY_PROPOSAL_TOOL_NAMES,
    SCREENPLAY_PLANNING_CAPABILITY_NAMES,
    SCREENPLAY_PRIVATE_TOOL_TO_PLANNING_CAPABILITY,
    SCREENPLAY_READ_TOOL_NAMES,
    SCREENPLAY_TOOL_NAMES,
    SCREENPLAY_TOOL_SCHEMAS,
)
from infrastructure.screenplay import build_screenplay_tool_catalog
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from exceptions import AppError
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest


@pytest_asyncio.fixture
async def source_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _seed_source_project(db: DatabaseConnection):
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-source", "雾港来信"],
    )
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-other", "边界外作品"],
    )
    await db.execute(
        "INSERT INTO outlines "
        "(id, title, type, book_id, markdown_content) VALUES (?, ?, ?, ?, ?)",
        ["outline-source", "总纲", "global", "book-source", "林岚寻找失踪的电台。"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["writing-source", "写作目录", "writing", "book-source"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
        ["chapter-source", "writing-source", "第一章 雾中广播", 1],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-source", json.dumps({
            "root": {
                "children": [{
                    "type": "paragraph",
                    "children": [{
                        "type": "text",
                        "text": "林岚在雾港听见失踪哥哥的声音。",
                    }],
                }],
            },
        }, ensure_ascii=False)],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["writing-other", "其他目录", "writing", "book-other"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
        ["chapter-other", "writing-other", "秘密章节", 1],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-other", "不应被当前剧本读取"],
    )
    await db.execute(
        "INSERT INTO characters "
        "(book_id, name, tags, profile_md) VALUES (?, ?, ?, ?)",
        ["book-source", "林岚", "主角", "电台维修员，害怕承认哥哥已经失踪。"],
    )
    await db.execute(
        "INSERT INTO setting_entities "
        "(book_id, entity_type, name, tags, profile_md) "
        "VALUES (?, ?, ?, ?, ?)",
        ["book-source", "location", "雾港", "港口", "每天午夜出现无法定位的广播。"],
    )
    await db.execute(
        "INSERT INTO story_background (book_id, content) VALUES (?, ?)",
        ["book-source", "近未来海港城，长期被异常浓雾笼罩。"],
    )
    return await screenplay_crud.create_project(
        db,
        title="雾港改编",
        source_kind="book",
        source_book_id="book-source",
        screenplay_format="电影",
        approach="结构重组",
        premise="保留兄妹关系，强化悬疑。",
    )


def test_artifact_protocol_projects_to_business_planning_capabilities():
    class _Db:
        pass

    catalog = build_screenplay_tool_catalog(_Db())
    registrations = catalog.registrations()
    runtime_names = frozenset(
        SCREENPLAY_PRIVATE_TOOL_TO_PLANNING_CAPABILITY
    )

    assert projected_planning_tool_names(
        registrations,
        runtime_names,
    ) == frozenset(SCREENPLAY_PLANNING_CAPABILITY_NAMES)
    assert all(
        registration.planning_capability is not None
        for registration in registrations
        if registration.schema.name in runtime_names
    )
    host_planned = {
        registration.schema.name
        for registration in registrations
        if registration.host_planned_arguments is not None
    }
    assert host_planned == {
        "finalizeSourceAnalysisProposal",
        "finalizeCreativeBriefProposal",
        "finalizeScreenplayStructureProposal",
        "finalizeSceneListProposal",
        "finalizeScreenplayReviewProposal",
        "finalizeScreenplayRevisionProposal",
    }


def test_proposal_tool_inputs_do_not_expose_host_owned_document_provenance():
    proposal_schemas = {
        schema.name: thaw_json_mapping(schema.parameters)
        for schema in SCREENPLAY_TOOL_SCHEMAS
        if schema.name in SCREENPLAY_PROPOSAL_TOOL_NAMES
    }

    assert set(proposal_schemas) == set(SCREENPLAY_PROPOSAL_TOOL_NAMES)
    assert all(
        "derivedFromIds" not in schema["properties"]
        for schema in proposal_schemas.values()
    )
    host_rendered = {
        "finalizeSourceAnalysisProposal",
        "finalizeCreativeBriefProposal",
        "finalizeScreenplayStructureProposal",
        "finalizeSceneListProposal",
        "finalizeScreenplayReviewProposal",
        "finalizeScreenplayRevisionProposal",
    }
    assert all(
        "contentText" not in proposal_schemas[name]["properties"]
        for name in host_rendered
    )
    assert all(
        "contentText" not in schema["properties"]
        for schema in proposal_schemas.values()
    )
    assert "proposeSceneList" not in proposal_schemas
    assert "proposeScreenplayRevision" not in proposal_schemas
    assert "proposeSourceAnalysis" not in proposal_schemas
    assert "proposeCreativeBrief" not in proposal_schemas
    assert "proposeScreenplayReview" not in proposal_schemas
    assert "proposeBeatSheet" not in proposal_schemas
    assert "proposeEpisodeOutline" not in proposal_schemas
    assert proposal_schemas["appendSourceAnalysisBatch"]["properties"][
        "items"
    ]["maxItems"] == 20
    source_item_variants = proposal_schemas[
        "appendSourceAnalysisBatch"
    ]["properties"]["items"]["items"]["anyOf"]
    source_variant_by_type = {
        variant["properties"]["itemType"]["enum"][0]: variant
        for variant in source_item_variants
    }
    assert set(source_variant_by_type) == {
        "character",
        "plot_event",
        "central_conflict",
        "adaptation_asset",
        "continuity_risk",
        "open_question",
        "evidence",
    }
    assert all(
        "index" not in variant["properties"]
        for variant in source_item_variants
    )
    assert "text" in source_variant_by_type["central_conflict"]["required"]
    assert "text" in source_variant_by_type["adaptation_asset"]["required"]
    assert "conflict" not in source_variant_by_type[
        "central_conflict"
    ]["properties"]
    assert "consequence" not in source_variant_by_type[
        "adaptation_asset"
    ]["properties"]
    assert "coverage" not in proposal_schemas[
        "beginSourceAnalysisArtifact"
    ]["properties"]
    assert set(proposal_schemas[
        "beginCreativeBriefArtifact"
    ]["properties"]) == {"title", "expectedDecisionCount"}
    assert proposal_schemas["appendCreativeBriefBatch"]["properties"][
        "items"
    ]["maxItems"] == 8
    creative_variants = proposal_schemas[
        "appendCreativeBriefBatch"
    ]["properties"]["items"]["items"]["anyOf"]
    creative_by_type = {
        variant["properties"]["itemType"]["enum"][0]: variant
        for variant in creative_variants
    }
    assert set(creative_by_type) == {
        "brief_content",
        "adaptation_decision",
    }
    assert all(
        "acknowledgedSourceLimitations" not in variant["properties"]
        for variant in creative_variants
    )
    format_variants = creative_by_type["brief_content"]["properties"][
        "formatPlan"
    ]["anyOf"]
    assert all(
        "targetFormat" not in variant["properties"]
        for variant in format_variants
    )
    assert set(proposal_schemas[
        "beginScreenplayStructureArtifact"
    ]["properties"]) == {"title", "expectedUnitCount"}
    assert proposal_schemas["appendScreenplayStructureBatch"]["properties"][
        "items"
    ]["maxItems"] == 20
    assert set(proposal_schemas[
        "beginScreenplayReviewArtifact"
    ]["properties"]) == {"title", "verdict", "expectedIssueCount"}
    assert proposal_schemas["appendScreenplayReviewBatch"]["properties"][
        "items"
    ]["maxItems"] == 8
    review_variants = proposal_schemas[
        "appendScreenplayReviewBatch"
    ]["properties"]["items"]["items"]["anyOf"]
    assert all(
        "issueId" not in variant["properties"]
        for variant in review_variants
    )
    assert proposal_schemas["appendSceneListBatch"]["properties"][
        "scenes"
    ]["maxItems"] == 10
    assert proposal_schemas["appendScreenplayRevisionBatch"]["properties"][
        "revisedScenes"
    ]["maxItems"] == 1
    assert proposal_schemas[
        "appendScreenplayRevisionResolutionBatch"
    ]["properties"]["issueResolutions"]["maxItems"] == 8
    assert set(
        proposal_schemas["beginScreenplayRevisionArtifact"]["properties"]
    ) == {"title", "additionalSceneIds", "revisionSummary"}
    assert "sceneText" in proposal_schemas["proposeSceneDraft"]["properties"]
    draft_properties = proposal_schemas["proposeSceneDraft"]["properties"]
    execution_properties = draft_properties["execution"]["properties"]
    assert draft_properties["sceneText"]["maxLength"] == 24_000
    assert draft_properties["additionalScenes"]["maxItems"] == 19
    assert draft_properties["notes"]["maxLength"] == 4_000
    assert execution_properties["objectiveResult"]["maxLength"] == 2_000
    assert execution_properties["unresolvedNotes"]["maxItems"] == 10
    assert execution_properties["unresolvedNotes"]["items"]["maxLength"] == 500
    assert "contentText" not in proposal_schemas["proposeSceneDraft"]["properties"]
    assert all(
        field not in proposal_schemas["proposeSceneDraft"]["properties"]
        for field in {
            "sceneId",
            "sceneHeading",
            "completedSceneIds",
            "isComplete",
        }
    )


def _request(
    project_id: str,
    source_book_id: str = "book-source",
    *,
    active_stage: str = "orientation",
    draft_scene_count: int = 1,
):
    return to_screenplay_agent_request(
        ScreenplayAgentRunRequest(
            messages=[{"role": "user", "content": "检索原作并完善简报"}],
            options={"model": "model"},
            screenplayProjectId=project_id,
            sourceBookId=source_book_id,
            activeStage=active_stage,
            screenplayDraftSceneCount=draft_scene_count,
            enableAgentTools=True,
            chatAgentMode="agent",
        ),
        {"model": "model"},
    )


async def _invoke(catalog, state, name: str, arguments: dict):
    registration = catalog.get(name)
    assert registration is not None
    if registration.scope_validator is not None:
        scope_error = await registration.scope_validator(state, arguments)
        if scope_error:
            return scope_error, None
    return None, await registration.handler(state, arguments)


_ARTIFACT_RUN_SEQUENCE = count(1)


async def _submit_scene_list(catalog, state, scenes, *, title=""):
    state.run_id = f"test-scene-list-{next(_ARTIFACT_RUN_SEQUENCE)}"
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginSceneListArtifact",
        {
            "expectedSceneCount": len(scenes),
            **({"title": title} if title else {}),
        },
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    for index in range(0, len(scenes), 10):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendSceneListBatch",
            {"scenes": scenes[index:index + 10]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(catalog, state, "finalizeSceneListProposal", {})


async def _submit_source_analysis(
    catalog,
    state,
    analysis,
    *,
    title="",
):
    if not state.run_id:
        state.run_id = f"test-source-analysis-{next(_ARTIFACT_RUN_SEQUENCE)}"
    groups = {
        "character": list(analysis.get("characters") or []),
        "plot_event": list(analysis.get("plotEvents") or []),
        "central_conflict": list(analysis.get("centralConflicts") or []),
        "adaptation_asset": list(analysis.get("adaptationAssets") or []),
        "continuity_risk": list(analysis.get("continuityRisks") or []),
        "open_question": list(analysis.get("openQuestions") or []),
        "evidence": list(analysis.get("evidence") or []),
    }
    begin_arguments = {
        "rangeSummary": analysis["rangeSummary"],
        "narrativeSummary": analysis["narrativeSummary"],
        "coverageLimitations": list(
            (analysis.get("coverage") or {}).get("limitations") or []
        ),
        "expectedItemCounts": {
            item_type: len(values)
            for item_type, values in groups.items()
        },
        **({"title": title} if title else {}),
    }
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginSourceAnalysisArtifact",
        begin_arguments,
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    items = []
    for item in groups["character"]:
        items.append({"itemType": "character", **item})
    for item in groups["plot_event"]:
        items.append({
            "itemType": "plot_event",
            "event": item["event"],
            "consequence": item["consequence"],
        })
    for item_type in (
        "central_conflict",
        "adaptation_asset",
        "continuity_risk",
        "open_question",
    ):
        for text in groups[item_type]:
            items.append({
                "itemType": item_type,
                "text": text,
            })
    for item in groups["evidence"]:
        items.append({"itemType": "evidence", **item})
    for offset in range(0, len(items), 20):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendSourceAnalysisBatch",
            {"items": items[offset:offset + 20]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(
        catalog,
        state,
        "finalizeSourceAnalysisProposal",
        {},
    )


async def _submit_creative_brief(catalog, state, brief, *, title=""):
    decisions = list(brief.get("adaptationDecisions") or [])
    state.run_id = f"test-creative-brief-{next(_ARTIFACT_RUN_SEQUENCE)}"
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginCreativeBriefArtifact",
        {
            "expectedDecisionCount": len(decisions),
            **({"title": title} if title else {}),
        },
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    brief_content = {
        key: value
        for key, value in brief.items()
        if key not in {
            "adaptationDecisions",
            "acknowledgedSourceLimitations",
        }
    }
    format_plan = brief_content.get("formatPlan")
    if isinstance(format_plan, dict):
        brief_content["formatPlan"] = {
            key: value
            for key, value in format_plan.items()
            if key != "targetFormat"
        }
    items = [{"itemType": "brief_content", **brief_content}]
    items.extend({
        "itemType": "adaptation_decision",
        "index": index,
        **decision,
    } for index, decision in enumerate(decisions, start=1))
    for index in range(0, len(items), 8):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendCreativeBriefBatch",
            {"items": items[index:index + 8]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(
        catalog,
        state,
        "finalizeCreativeBriefProposal",
        {},
    )


async def _submit_structure(
    catalog,
    state,
    units,
    decision_coverage,
    *,
    title="",
):
    state.run_id = f"test-structure-{next(_ARTIFACT_RUN_SEQUENCE)}"
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginScreenplayStructureArtifact",
        {
            "expectedUnitCount": len(units),
            **({"title": title} if title else {}),
        },
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    items = [{
        "itemType": "structure_unit",
        "index": item.get("order", item.get("number")),
        "id": item["id"],
        "title": item.get("label", item.get("title")),
        "summary": item["summary"],
    } for item in units]
    items.extend({
        "itemType": "decision_coverage",
        **item,
    } for item in decision_coverage)
    for index in range(0, len(items), 20):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendScreenplayStructureBatch",
            {"items": items[index:index + 20]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(
        catalog,
        state,
        "finalizeScreenplayStructureProposal",
        {},
    )


async def _submit_review(
    catalog,
    state,
    *,
    summary,
    strengths,
    issues,
    verdict,
    verification_results=(),
    title="",
):
    state.run_id = f"test-review-{next(_ARTIFACT_RUN_SEQUENCE)}"
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginScreenplayReviewArtifact",
        {
            "verdict": verdict,
            "expectedIssueCount": len(issues),
            **({"title": title} if title else {}),
        },
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    items = [{
        "itemType": "review_summary",
        "summary": summary,
        "strengths": list(strengths),
    }]
    items.extend({
        "itemType": "review_issue",
        "index": index,
        **issue,
    } for index, issue in enumerate(issues, start=1))
    items.extend({
        "itemType": "verification_result",
        "index": index,
        "status": verification["status"],
        "verificationEvidence": verification["verificationEvidence"],
    } for index, verification in enumerate(verification_results, start=1))
    for index in range(0, len(items), 8):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendScreenplayReviewBatch",
            {"items": items[index:index + 8]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(
        catalog,
        state,
        "finalizeScreenplayReviewProposal",
        {},
    )


async def _submit_revision(
    catalog,
    state,
    *,
    issue_resolutions,
    execution_updates,
    revision_summary,
    revised_scenes,
):
    state.run_id = f"test-revision-{next(_ARTIFACT_RUN_SEQUENCE)}"
    scope_error, result = await _invoke(
        catalog,
        state,
        "beginScreenplayRevisionArtifact",
        {"revisionSummary": revision_summary},
    )
    if scope_error or result is None or result.error_code:
        return scope_error, result
    begin_receipt = json.loads(result.content)
    assert set(begin_receipt["expectedSceneIds"]) == {
        str(item["sceneId"]) for item in execution_updates
    }
    assert set(begin_receipt["reviewIssueIds"]) == {
        str(item["issueId"]) for item in issue_resolutions
    }
    executions_by_scene = {
        str(item["sceneId"]): {
            key: value
            for key, value in item.items()
            if key != "sceneId"
        }
        for item in execution_updates
    }
    for index in range(0, len(revised_scenes)):
        scene = revised_scenes[index]
        scene_id = str(scene["sceneId"])
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendScreenplayRevisionBatch",
            {"revisedScenes": [{
                **scene,
                "execution": executions_by_scene[scene_id],
            }]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeScreenplayRevisionProposal",
        {},
    )
    assert incomplete is not None
    assert incomplete.error_code == "tool_input_invalid"
    for index in range(0, len(issue_resolutions), 8):
        scope_error, result = await _invoke(
            catalog,
            state,
            "appendScreenplayRevisionResolutionBatch",
            {"issueResolutions": issue_resolutions[index:index + 8]},
        )
        if scope_error or result is None or result.error_code:
            return scope_error, result
    return await _invoke(
        catalog,
        state,
        "finalizeScreenplayRevisionProposal",
        {},
    )


class _RecordingSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


async def _accept_source_analysis(db, created, *, limitations=None):
    project_id = created["project"]["id"]
    run_id = f"source-analysis-{project_id}"
    await record_source_refs(
        db,
        project_id=project_id,
        agent_run_id=run_id,
        tool_name="readSourcePassages",
        refs=[{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
            "sourceRevision": "revision-source",
            "excerpt": "林岚在雾港听见失踪哥哥的声音。",
        }],
    )
    analysis = await screenplay_crud.create_document(
        db,
        project_id=project_id,
        kind="source_analysis",
        title="原作范围分析",
        content_json={
            "analysis": {
                "narrativeSummary": "林岚追查雾中广播。",
                "coverage": {
                    "selectedChapterCount": 1,
                    "readChapterIds": ["chapter-source"],
                    "sampledChapterIds": [],
                    "limitations": list(limitations or []),
                },
                "plotEvents": [{
                    "order": 1,
                    "event": "林岚听见哥哥的声音",
                    "consequence": "她决定追查广播来源",
                }],
                "evidence": [{
                    "sourceType": "chapter",
                    "sourceId": "chapter-source",
                    "claim": "异常广播触发主线",
                }],
            },
        },
        content_text="# 原作范围分析",
        derived_from_ids=[created["initialDocument"]["id"]],
        source_run_id=run_id,
    )
    await screenplay_crud.accept_document(db, analysis["id"])
    return analysis


def _book_adaptation_brief() -> dict:
    return {
        "audience": "偏好悬疑与情感故事的成年观众",
        "logline": "维修员循着雾中广播寻找失踪哥哥。",
        "theme": "执念与告别",
        "protagonist": "林岚",
        "coreConflict": "她必须在真相与对哥哥的执念之间选择。",
        "formatPlan": {
            "targetFormat": "电影",
            "targetDurationMinutes": 110,
            "scopeStrategy": "压缩调查过程，集中保留兄妹关系主线。",
            "narrativeEndpoint": "林岚确认广播真相并决定告别哥哥。",
        },
        "adaptationDecisions": [{
            "id": "decision-radio-inciting",
            "action": "preserve",
            "subject": "异常广播触发调查",
            "rationale": "这是原作主线的清晰启动事件。",
            "screenIntent": "作为第一幕激励事件。",
            "sourceAnchors": [{
                "sourceType": "chapter",
                "sourceId": "chapter-source",
            }],
        }],
        "acknowledgedSourceLimitations": [],
        "adaptationPrinciples": ["保留兄妹关系，重组调查节奏。"],
        "openQuestions": [],
    }


def _book_beat_structure(brief_id: str) -> dict:
    return {
        "schemaVersion": 1,
        "documentKind": "beat_sheet",
        "creativeBriefId": brief_id,
        "beats": [{
            "id": "beat-inciting-radio",
            "order": 1,
            "label": "异常广播",
            "summary": "林岚听见哥哥的声音并决定追查广播。",
        }],
        "decisionCoverage": [{
            "decisionId": "decision-radio-inciting",
            "structureUnitIds": ["beat-inciting-radio"],
            "implementation": "把异常广播作为第一幕激励事件。",
        }],
    }


def _book_scene_list(structure_id: str, *, count: int = 1) -> dict:
    scenes = [{
        "id": f"scene-{index}",
        "order": index,
        "heading": f"内景·电台·夜·{index}",
        "structureUnitIds": ["beat-inciting-radio"],
        "objective": "确认异常广播来源",
        "conflict": "信号随浓雾消失",
        "turn": "林岚听见哥哥的声音",
        "synopsis": "林岚在电台追踪异常信号。",
    } for index in range(1, count + 1)]
    return {
        "structureId": structure_id,
        "scenes": scenes,
    }


def _scene_execution_input(index: int = 1) -> dict:
    return {
        "objectiveResult": f"第 {index} 场完成了角色的即时目标。",
        "conflictResult": f"第 {index} 场让阻力升级并迫使角色行动。",
        "turnResult": f"第 {index} 场以新信息改变行动方向。",
        "continuityState": f"第 {index} 场结束后角色带着新目标进入下一场。",
        "unresolvedNotes": [],
    }


def _book_draft_content(
    scene_list_id: str,
    completed_scene_ids: list[str],
    *,
    scene_text: str,
) -> dict:
    executions = []
    for scene_id in completed_scene_ids:
        index = int(scene_id.rsplit("-", 1)[-1])
        executions.append({
            "sceneId": scene_id,
            "structureUnitIds": ["beat-inciting-radio"],
            **_scene_execution_input(index),
        })
    last_id = completed_scene_ids[-1]
    last_index = int(last_id.rsplit("-", 1)[-1])
    return {
        "sceneListId": scene_list_id,
        "sceneId": last_id,
        "sceneHeading": f"内景·电台·夜·{last_index}",
        "newSceneIds": [last_id],
        "newSceneHeadings": [f"内景·电台·夜·{last_index}"],
        "completedSceneIds": completed_scene_ids,
        "sceneExecutions": executions,
        "episodeDrafts": [{
            "episodeNumber": 1,
            "sceneIds": [last_id],
            "sceneExecutions": [executions[-1]],
            "sceneTexts": [{
                "sceneId": last_id,
                "contentText": scene_text,
            }],
            "contentText": scene_text,
            "continuitySummary": executions[-1]["continuityState"],
        }],
        "isComplete": False,
    }


async def _advance_book_to_structure(db, created):
    analysis = await _accept_source_analysis(db, created)
    brief = await screenplay_crud.create_document(
        db,
        project_id=created["project"]["id"],
        kind="creative_brief",
        title="创作简报",
        content_json={
            "schemaVersion": 1,
            "documentKind": "creative_brief",
            "sourceAnalysisId": analysis["id"],
            "brief": _book_adaptation_brief(),
        },
        content_text="以异常广播作为叙事线索。",
        derived_from_ids=[analysis["id"]],
    )
    await screenplay_crud.accept_document(db, brief["id"])
    return brief


async def _advance_book_series_to_structure(db, created, *, episodes: int):
    analysis = await _accept_source_analysis(db, created)
    brief_payload = _book_adaptation_brief()
    brief_payload["formatPlan"] = {
        "targetFormat": "连续剧",
        "episodeCount": episodes,
        "episodeDurationMinutes": 45,
        "scopeStrategy": "以调查阶段拆分单集，集中兄妹关系主线。",
        "narrativeEndpoint": "季终确认异常广播的来源。",
    }
    brief = await screenplay_crud.create_document(
        db,
        project_id=created["project"]["id"],
        kind="creative_brief",
        title="连续剧创作简报",
        content_json={
            "schemaVersion": 1,
            "documentKind": "creative_brief",
            "sourceAnalysisId": analysis["id"],
            "brief": brief_payload,
        },
        content_text="以异常广播作为跨集悬念。",
        derived_from_ids=[analysis["id"]],
    )
    await screenplay_crud.accept_document(db, brief["id"])
    return brief


@pytest.mark.asyncio
async def test_screenplay_catalog_exposes_read_and_stage_proposal_tools(source_db):
    created = await _seed_source_project(source_db)
    request = _request(created["project"]["id"])
    catalog = build_screenplay_tool_catalog(source_db)

    assert catalog.names == frozenset(SCREENPLAY_TOOL_NAMES)
    assert catalog.enabled_names(request) == frozenset({
        *SCREENPLAY_READ_TOOL_NAMES,
        "beginSourceAnalysisArtifact",
        "appendSourceAnalysisBatch",
        "finalizeSourceAnalysisProposal",
    } - {"getScreenplayDraftContext", "getScreenplayEpisodeContext"})
    assert all(
        catalog.get(name).policy.mode.value == "read"
        for name in SCREENPLAY_READ_TOOL_NAMES
    )
    assert all(
        catalog.get(name).policy.mode.value == "propose"
        for name in {
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
        }
    )


@pytest.mark.asyncio
async def test_original_project_enables_only_project_read_tools(source_db):
    created = await screenplay_crud.create_project(
        source_db,
        title="原创项目",
        source_kind="original",
        source_book_id=None,
        screenplay_format="短片",
        approach="先找人物",
        premise="",
    )
    request = _request(created["project"]["id"], source_book_id="")
    catalog = build_screenplay_tool_catalog(source_db)

    assert catalog.enabled_names(request) == frozenset({
        "getScreenplayProject",
        "getScreenplayDocument",
        "beginCreativeBriefArtifact",
        "appendCreativeBriefBatch",
        "finalizeCreativeBriefProposal",
    })


@pytest.mark.asyncio
async def test_long_source_coverage_plan_reads_every_chapter_in_bounded_batches(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    for index in range(2, 46):
        chapter_id = f"chapter-{index:02d}"
        await source_db.execute(
            "INSERT INTO outline_chapters "
            "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
            [
                chapter_id,
                "writing-source",
                f"第 {index} 章",
                index,
            ],
        )
        await source_db.execute(
            "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
            [chapter_id, f"章节 {index} " + ("雾港线索。" * 900)],
        )
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-long-coverage"
    catalog = build_screenplay_tool_catalog(source_db)

    scope_error, plan_result = await _invoke(
        catalog,
        state,
        "getSourceCoveragePlan",
        {},
    )
    assert scope_error is None
    assert plan_result.error_code is None
    plan = json.loads(plan_result.content)
    assert plan["selectedChapterCount"] == 45
    assert 2 <= plan["batchCount"] <= 6
    assert sum(
        batch["chapterCount"] for batch in plan["batches"]
    ) == 45

    covered_ids: list[str] = []
    for batch in plan["batches"]:
        _, batch_result = await _invoke(
            catalog,
            state,
            "readSourceCoverageBatch",
            {
                "planId": plan["planId"],
                "batchNumber": batch["batchNumber"],
            },
        )
        assert batch_result.error_code is None
        payload = json.loads(batch_result.content)
        assert len(batch_result.content) < 64_000
        covered_ids.extend(
            chapter["sourceId"] for chapter in payload["chapters"]
        )
        assert all(
            chapter["coverage"] in {"full", "sampled"}
            for chapter in payload["chapters"]
        )

    assert len(covered_ids) == 45
    assert len(set(covered_ids)) == 45
    refs = await list_source_refs(
        source_db,
        project_id=project_id,
        agent_run_id=state.run_id,
    )
    assert {
        ref["source_id"] for ref in refs
        if ref["source_type"] == "chapter"
    } == set(covered_ids)
    assert {
        ref["coverage_mode"] for ref in refs
        if ref["source_type"] == "chapter"
    }.issubset({"full", "sampled"})

    await source_db.execute(
        "UPDATE articles SET content = content || '新增内容' "
        "WHERE chapter_id = 'chapter-02'"
    )
    _, stale_result = await _invoke(
        catalog,
        state,
        "readSourceCoverageBatch",
        {
            "planId": plan["planId"],
            "batchNumber": 1,
        },
    )
    assert stale_result.error_code == "tool_input_invalid"
    assert "stale" in stale_result.content


@pytest.mark.asyncio
async def test_source_analysis_proposal_requires_and_preserves_read_evidence(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-source-analysis"
    catalog = build_screenplay_tool_catalog(source_db)

    overview_error, overview = await _invoke(
        catalog,
        state,
        "getSourceBookOverview",
        {},
    )
    assert overview_error is None
    assert overview.error_code is None
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    scope_error, result = await _submit_source_analysis(
        catalog,
        state,
        {
            "rangeSummary": "整本作品，当前一章。",
            "narrativeSummary": "林岚追查雾中广播。",
            "coverage": {"limitations": []},
            "plotEvents": [{
                "order": 1,
                "event": "林岚听见哥哥的声音",
                "consequence": "她开始追踪广播",
            }],
            "evidence": [{
                "sourceType": "chapter",
                "sourceId": "chapter-source",
                "claim": "异常广播触发主线",
            }],
        },
    )

    assert scope_error is None
    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["kind"] == "source_analysis"
    assert proposal["derivedFromIds"] == []
    assert proposal["contentJson"]["analysis"]["coverage"] == {
        "selectedChapterCount": 1,
        "readChapterIds": ["chapter-source"],
        "sampledChapterIds": [],
        "limitations": [],
    }
    assert proposal["contentJson"]["artifactRef"].startswith("artifact://")
    assert proposal["contentText"].startswith("# Agent 原作范围分析")
    assert "林岚追查雾中广播" in proposal["contentText"]

    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind=proposal["kind"],
        title=proposal["title"],
        content_json=proposal["contentJson"],
        content_text=proposal["contentText"],
        derived_from_ids=proposal["derivedFromIds"],
        source_run_id=state.run_id,
    )
    await screenplay_crud.accept_document(source_db, document["id"])
    project = await screenplay_crud.get_project(source_db, project_id)
    assert project["active_stage"] == "brief"


@pytest.mark.asyncio
async def test_source_analysis_accepts_json_arrays_through_core_executor(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    state = ScreenplayExecutionStateFactory().create(_request(project_id))
    state.run_id = "run-source-analysis-core"
    catalog = build_screenplay_tool_catalog(source_db)

    overview_error, overview = await _invoke(
        catalog,
        state,
        "getSourceBookOverview",
        {},
    )
    assert overview_error is None
    assert overview.error_code is None
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    executor = CoreToolExecutor(catalog)
    begin_result = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-source-analysis-begin",
                name="beginSourceAnalysisArtifact",
                arguments_json=json.dumps({
                    "rangeSummary": "整本作品，当前一章。",
                    "narrativeSummary": "林岚追查雾中广播。",
                    "expectedItemCounts": {
                        "character": 0,
                        "plot_event": 1,
                        "central_conflict": 0,
                        "adaptation_asset": 0,
                        "continuity_risk": 0,
                        "open_question": 0,
                        "evidence": 1,
                    },
                }, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"beginSourceAnalysisArtifact"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert begin_result.outcome is ToolBatchOutcome.COMPLETED
    append_result = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-source-analysis-items",
                name="appendSourceAnalysisBatch",
                arguments_json=json.dumps({"items": [{
                    "itemType": "plot_event",
                    "event": "林岚听见哥哥的声音",
                    "consequence": "她开始追踪广播",
                }, {
                    "itemType": "evidence",
                    "sourceType": "chapter",
                    "sourceId": "chapter-source",
                    "claim": "异常广播触发主线",
                }]}, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"appendSourceAnalysisBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert append_result.outcome is ToolBatchOutcome.COMPLETED
    final_result = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-source-analysis-finalize",
                name="finalizeSourceAnalysisProposal",
                arguments_json="{}",
            ),),
            allowed_tool_names=frozenset({"finalizeSourceAnalysisProposal"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert final_result.outcome is ToolBatchOutcome.COMPLETED
    assert final_result.results[0].error is None


@pytest.mark.asyncio
async def test_source_analysis_artifact_is_resumable_and_rejects_unread_evidence(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    state = ScreenplayExecutionStateFactory().create(_request(project_id))
    state.run_id = "run-source-analysis-resume"
    catalog = build_screenplay_tool_catalog(source_db)
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    begin_arguments = {
        "rangeSummary": "锁定范围为第一章。",
        "narrativeSummary": "广播推动林岚展开调查。",
        "expectedItemCounts": {
            "character": 0,
            "plot_event": 2,
            "central_conflict": 0,
            "adaptation_asset": 0,
            "continuity_risk": 0,
            "open_question": 0,
            "evidence": 1,
        },
    }
    _, begun = await _invoke(
        catalog,
        state,
        "beginSourceAnalysisArtifact",
        begin_arguments,
    )
    assert begun.error_code is None
    assert json.loads(begun.content)["remainingItemCount"] == 3

    first_batch = {"items": [{
        "itemType": "plot_event",
        "event": "异常广播出现",
        "consequence": "林岚决定调查",
    }]}
    _, first = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        first_batch,
    )
    assert first.error_code is None
    _, resumed = await _invoke(
        build_screenplay_tool_catalog(source_db),
        state,
        "beginSourceAnalysisArtifact",
        begin_arguments,
    )
    assert resumed.error_code is None
    resumed_progress = json.loads(resumed.content)
    assert resumed_progress["committedItemCount"] == 1
    assert resumed_progress["committedItemCounts"]["plot_event"] == 1
    assert resumed_progress["remainingItemCounts"]["plot_event"] == 1
    assert resumed_progress["nextItemIndices"]["plot_event"] == 2

    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeSourceAnalysisProposal",
        {},
    )
    assert incomplete.error_code == "tool_input_invalid"

    _, unread = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        {"items": [{
            "itemType": "evidence",
            "sourceType": "chapter",
            "sourceId": "chapter-other",
            "claim": "不应允许引用未读章节",
        }]},
    )
    assert unread.error_code == "tool_input_invalid"
    assert "current run" in unread.content

    _, completed_batch = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        {"items": [{
            "itemType": "plot_event",
            "event": "广播传来哥哥的声音",
            "consequence": "调查转为寻找失踪亲人",
        }, {
            "itemType": "evidence",
            "sourceType": "chapter",
            "sourceId": "chapter-source",
            "claim": "哥哥的声音改变主角行动方向",
        }]},
    )
    assert completed_batch.error_code is None
    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeSourceAnalysisProposal",
        {},
    )
    assert finalized.error_code is None
    proposal = thaw_json_mapping(finalized.effects[0].payload)
    assert [
        item["order"]
        for item in proposal["contentJson"]["analysis"]["plotEvents"]
    ] == [1, 2]
    artifact_id = proposal["contentJson"]["sourceAnalysisArtifactId"]
    rows = await source_db.fetch_all(
        "SELECT items_json FROM ai_agent_artifact_batches "
        "WHERE artifact_id = ? ORDER BY sequence ASC",
        [artifact_id],
    )
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_source_analysis_variant_mismatch_fails_before_artifact_mutation(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    state = ScreenplayExecutionStateFactory().create(_request(project_id))
    state.run_id = "run-source-analysis-variant-contract"
    catalog = build_screenplay_tool_catalog(source_db)
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    _, begun = await _invoke(
        catalog,
        state,
        "beginSourceAnalysisArtifact",
        {
            "rangeSummary": "锁定范围为第一章。",
            "narrativeSummary": "广播推动林岚展开调查。",
            "expectedItemCounts": {
                "character": 0,
                "plot_event": 1,
                "central_conflict": 1,
                "adaptation_asset": 1,
                "continuity_risk": 0,
                "open_question": 0,
                "evidence": 1,
            },
        },
    )
    assert begun.error_code is None
    executor = CoreToolExecutor(catalog)

    invalid = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-invalid-source-variant",
                name="appendSourceAnalysisBatch",
                arguments_json=json.dumps({"items": [{
                    "itemType": "central_conflict",
                    "name": "普通人和觉醒者世界",
                    "conflict": "林岚被动卷入。",
                }]}, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"appendSourceAnalysisBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )

    assert invalid.outcome is ToolBatchOutcome.FAILED
    assert invalid.error == "invalid_tool_arguments_schema"
    assert invalid.effect_state is ToolEffectState.NOT_STARTED

    model_owned_index = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-invalid-source-index",
                name="appendSourceAnalysisBatch",
                arguments_json=json.dumps({"items": [{
                    "itemType": "central_conflict",
                    "index": 2,
                    "text": "索引超出当前 Artifact 声明。",
                }]}, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"appendSourceAnalysisBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )

    assert model_owned_index.outcome is ToolBatchOutcome.FAILED
    assert model_owned_index.error == "invalid_tool_arguments_schema"
    assert model_owned_index.effect_state is ToolEffectState.NOT_STARTED

    corrected = await executor.execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(ToolCall(
                id="call-corrected-source-variant",
                name="appendSourceAnalysisBatch",
                arguments_json=json.dumps({"items": [{
                    "itemType": "central_conflict",
                    "text": "普通人林岚被动卷入觉醒者世界。",
                }]}, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"appendSourceAnalysisBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )

    assert corrected.outcome is ToolBatchOutcome.PROGRESSED
    assert json.loads(corrected.results[0].content)[
        "committedItemCount"
    ] == 1


@pytest.mark.asyncio
async def test_source_analysis_host_cursor_clamps_surplus_and_blocks_repeats(
    source_db,
):
    created = await _seed_source_project(source_db)
    state = ScreenplayExecutionStateFactory().create(
        _request(created["project"]["id"]),
    )
    state.run_id = "run-source-analysis-host-cursor"
    catalog = build_screenplay_tool_catalog(source_db)
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    _, begun = await _invoke(
        catalog,
        state,
        "beginSourceAnalysisArtifact",
        {
            "rangeSummary": "锁定范围为第一章。",
            "narrativeSummary": "广播推动林岚展开调查。",
            "expectedItemCounts": {
                "character": 0,
                "plot_event": 1,
                "central_conflict": 0,
                "adaptation_asset": 2,
                "continuity_risk": 1,
                "open_question": 8,
                "evidence": 1,
            },
        },
    )
    assert begun.error_code is None
    _, seeded = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        {"items": [{
            "itemType": "plot_event",
            "event": "异常广播出现",
            "consequence": "林岚决定调查",
        }, {
            "itemType": "adaptation_asset",
            "text": "雾港具有鲜明视觉氛围。",
        }, {
            "itemType": "adaptation_asset",
            "text": "广播声音适合作为悬念钩子。",
        }, {
            "itemType": "continuity_risk",
            "text": "哥哥失踪时间需要保持一致。",
        }, {
            "itemType": "evidence",
            "sourceType": "chapter",
            "sourceId": "chapter-source",
            "claim": "异常广播触发调查主线。",
        }]},
    )
    assert seeded.error_code is None

    _, questions = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        {"items": [
            {
                "itemType": "open_question",
                "text": f"待确认问题 {index}",
            }
            for index in range(1, 10)
        ]},
    )
    assert questions.error_code is None
    progress = json.loads(questions.content)
    assert progress["committedItemCounts"]["open_question"] == 8
    assert progress["remainingItemCounts"]["open_question"] == 0
    assert progress["nextItemIndices"]["open_question"] is None
    assert progress["discardedSurplusItemCounts"] == {"open_question": 1}
    assert progress["nextAction"] == "finalize"

    _, repeated = await _invoke(
        catalog,
        state,
        "appendSourceAnalysisBatch",
        {"items": [{
            "itemType": "adaptation_asset",
            "text": "不应覆盖已经提交的改编资产。",
        }, {
            "itemType": "continuity_risk",
            "text": "不应覆盖已经提交的连续性风险。",
        }]},
    )
    assert repeated.error_code == "tool_input_invalid"
    assert "remainingItemCounts" in repeated.content

    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeSourceAnalysisProposal",
        {},
    )
    assert finalized.error_code is None


@pytest.mark.asyncio
async def test_screenplay_internal_tool_error_is_opaque(
    source_db,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await _seed_source_project(source_db)
    state = ScreenplayExecutionStateFactory().create(
        _request(created["project"]["id"]),
    )
    registration = build_screenplay_tool_catalog(source_db).get(
        "getSourceBookOverview",
    )
    assert registration is not None

    async def _fail_fetch(*_args, **_kwargs):
        raise RuntimeError("secret database path /private/project.db")

    monkeypatch.setattr(source_db, "fetch_one", _fail_fetch)
    result = await registration.handler(state, {})

    assert result.error_code == "tool_internal_error"
    assert "secret" not in result.content
    assert "/private/project.db" not in result.content


@pytest.mark.asyncio
async def test_source_analysis_host_discloses_partial_reading_limitations(
    source_db,
):
    created = await _seed_source_project(source_db)
    await source_db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
        ["chapter-unread", "writing-source", "第二章 未读章节", 2],
    )
    await source_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-unread", "尚未读取的第二章"],
    )
    request = _request(created["project"]["id"])
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-partial-source-analysis"
    catalog = build_screenplay_tool_catalog(source_db)
    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {"sources": [{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
        }]},
    )
    assert passage.error_code is None
    _, result = await _submit_source_analysis(
        catalog,
        state,
        {
            "rangeSummary": "范围共两章。",
            "narrativeSummary": "当前只精读第一章。",
            "coverage": {"limitations": []},
            "plotEvents": [{
                "order": 1,
                "event": "异常广播出现",
                "consequence": "主角决定调查",
            }],
            "evidence": [{
                "sourceType": "chapter",
                "sourceId": "chapter-source",
                "claim": "第一章触发调查",
            }],
        },
    )

    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    coverage = proposal["contentJson"]["analysis"]["coverage"]
    assert coverage["selectedChapterCount"] == 2
    assert coverage["readChapterIds"] == ["chapter-source"]
    assert coverage["sampledChapterIds"] == []
    assert "1 个锁定范围章节未形成正文读取凭证" in coverage["limitations"][0]


@pytest.mark.asyncio
async def test_creative_brief_tool_emits_unsaved_structured_proposal(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    analysis = await _accept_source_analysis(source_db, created)
    request = _request(project_id, active_stage="brief")
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-proposal"
    catalog = build_screenplay_tool_catalog(source_db)
    assert {
        "beginSourceAnalysisArtifact",
        "appendSourceAnalysisBatch",
        "finalizeSourceAnalysisProposal",
        "beginCreativeBriefArtifact",
        "appendCreativeBriefBatch",
        "finalizeCreativeBriefProposal",
    }.issubset(catalog.enabled_names(request))

    scope_error, result = await _submit_creative_brief(
        catalog,
        state,
        _book_adaptation_brief(),
        title="雾港电影创作简报",
    )

    assert scope_error is None
    assert result.error_code is None
    assert json.loads(result.content)["status"] == "awaiting_user_review"
    assert len(result.effects) == 1
    effect = result.effects[0]
    assert effect.type == "screenplay.document_proposal"
    assert effect.payload["kind"] == "creative_brief"
    assert effect.payload["derivedFromIds"] == [
        analysis["id"],
    ]
    assert effect.payload["contentJson"]["sourceAnalysisId"] == analysis["id"]
    assert effect.payload["contentJson"]["creativeBriefArtifactId"]
    assert effect.payload["contentJson"]["artifactRef"].startswith(
        "artifact://purrtypos.screenplay/creative_brief_entries/"
    )
    assert (
        effect.payload["contentJson"]["brief"]["formatPlan"]["targetFormat"]
        == "电影"
    )
    assert (
        effect.payload["contentJson"]["brief"]["adaptationDecisions"][0]["id"]
        == "decision-radio-inciting"
    )
    documents = await screenplay_crud.list_documents(source_db, project_id)
    assert [document["id"] for document in documents] == [analysis["id"]]


@pytest.mark.asyncio
async def test_creative_brief_rejects_source_anchor_outside_analysis(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    await _accept_source_analysis(source_db, created)
    request = _request(project_id, active_stage="brief")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)
    brief = _book_adaptation_brief()
    brief["adaptationDecisions"][0]["sourceAnchors"][0]["sourceId"] = (
        "chapter-not-in-analysis"
    )

    _, result = await _submit_creative_brief(
        catalog,
        state,
        brief,
    )

    assert result.error_code == "tool_input_invalid"
    assert "accepted source analysis" in result.content


@pytest.mark.asyncio
async def test_creative_brief_artifact_resumes_and_host_inherits_limitations(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    analysis = await _accept_source_analysis(
        source_db,
        created,
        limitations=["部分环境细节仅获得片段文本。"],
    )
    state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="brief")
    )
    state.run_id = "run-creative-brief-batches"
    catalog = build_screenplay_tool_catalog(source_db)

    _, begun = await _invoke(
        catalog,
        state,
        "beginCreativeBriefArtifact",
        {"expectedDecisionCount": 2, "title": "分批创作简报"},
    )
    assert begun.error_code is None
    receipt = json.loads(begun.content)
    assert receipt["expectedItemCount"] == 3
    assert receipt["remainingItemCount"] == 3
    assert receipt["targetFormat"] == "电影"

    content_item = {
        "itemType": "brief_content",
        "audience": "偏好悬疑与情感故事的成年观众",
        "logline": "维修员循着雾中广播寻找失踪哥哥。",
        "theme": "执念与告别",
        "protagonist": "林岚",
        "coreConflict": "她必须在真相与执念之间选择。",
        "formatPlan": {
            "targetDurationMinutes": 110,
            "scopeStrategy": "压缩调查过程，保留兄妹关系主线。",
            "narrativeEndpoint": "林岚确认真相并决定告别哥哥。",
        },
        "adaptationPrinciples": ["保留兄妹关系。"],
        "openQuestions": [],
    }
    _, first = await _invoke(
        catalog,
        state,
        "appendCreativeBriefBatch",
        {"items": [content_item]},
    )
    assert first.error_code is None
    assert json.loads(first.content)["remainingItemCount"] == 2
    assert first.step_disposition is ToolStepDisposition.CONTINUE

    catalog = build_screenplay_tool_catalog(source_db)
    _, replayed = await _invoke(
        catalog,
        state,
        "appendCreativeBriefBatch",
        {"items": [content_item]},
    )
    assert replayed.error_code is None
    assert json.loads(replayed.content)["committedItemCount"] == 1

    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeCreativeBriefProposal",
        {},
    )
    assert incomplete.error_code == "tool_input_invalid"
    assert "incomplete" in incomplete.content

    _, invalid_anchor = await _invoke(
        catalog,
        state,
        "appendCreativeBriefBatch",
        {"items": [{
            "itemType": "adaptation_decision",
            "index": 1,
            "id": "decision-invalid",
            "action": "preserve",
            "subject": "不存在的情节",
            "rationale": "测试非法锚点。",
            "screenIntent": "不应提交。",
            "sourceAnchors": [{
                "sourceType": "chapter",
                "sourceId": "chapter-not-read",
            }],
        }]},
    )
    assert invalid_anchor.error_code == "tool_input_invalid"
    assert "accepted source analysis" in invalid_anchor.content

    decision_result = await CoreToolExecutor(catalog).execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(
                ToolCall(
                    id="call-brief-decision-1",
                    name="appendCreativeBriefBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "adaptation_decision",
                        "index": 1,
                        "id": "decision-radio-inciting",
                        "action": "preserve",
                        "subject": "异常广播触发调查",
                        "rationale": "这是主线启动事件。",
                        "screenIntent": "作为第一幕激励事件。",
                        "sourceAnchors": [{
                            "sourceType": "chapter",
                            "sourceId": "chapter-source",
                        }],
                    }]}, ensure_ascii=False),
                ),
                ToolCall(
                    id="call-brief-decision-2",
                    name="appendCreativeBriefBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "adaptation_decision",
                        "index": 2,
                        "id": "decision-invent-ending-image",
                        "action": "invent",
                        "subject": "终场的雾散意象",
                        "rationale": "用视觉变化表达人物完成告别。",
                        "screenIntent": "作为结尾的情感落点。",
                        "sourceAnchors": [],
                    }]}, ensure_ascii=False),
                ),
            ),
            allowed_tool_names=frozenset({"appendCreativeBriefBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert decision_result.outcome is ToolBatchOutcome.COMPLETED
    assert decision_result.results[-1].step_disposition is (
        ToolStepDisposition.COMPLETE
    )
    assert json.loads(decision_result.results[-1].content)[
        "remainingItemCount"
    ] == 0

    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeCreativeBriefProposal",
        {},
    )
    assert finalized.error_code is None
    proposal = thaw_json_mapping(finalized.effects[0].payload)
    brief = proposal["contentJson"]["brief"]
    assert brief["formatPlan"]["targetFormat"] == "电影"
    assert brief["acknowledgedSourceLimitations"] == [
        "部分环境细节仅获得片段文本。"
    ]
    assert len(brief["adaptationDecisions"]) == 2
    assert proposal["derivedFromIds"] == [analysis["id"]]

    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="creative_brief",
        title=str(proposal["title"]),
        content_json=dict(proposal["contentJson"]),
        content_text=str(proposal["contentText"]),
        derived_from_ids=list(proposal["derivedFromIds"]),
    )
    accepted = await screenplay_crud.accept_document(source_db, document["id"])
    assert accepted["status"] == "accepted"


@pytest.mark.asyncio
async def test_original_creative_brief_artifact_uses_one_content_item(source_db):
    created = await screenplay_crud.create_project(
        source_db,
        title="原创末班车",
        source_kind="original",
        source_book_id=None,
        screenplay_format="短片",
        approach="人物驱动",
        premise="陌生人在末班车上交换秘密。",
    )
    request = _request(
        created["project"]["id"],
        source_book_id="",
        active_stage="orientation",
    )
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    _, result = await _submit_creative_brief(
        catalog,
        state,
        {
            "audience": "成年观众",
            "logline": "两名陌生人在末班车上交换秘密。",
            "theme": "坦白与自由",
            "protagonist": "两名陌生乘客",
            "coreConflict": "坦白可能带来自由，也可能毁掉彼此。",
            "adaptationPrinciples": ["以封闭空间强化关系变化。"],
            "openQuestions": [],
        },
    )

    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert "adaptationDecisions" not in proposal["contentJson"]["brief"]
    assert "acknowledgedSourceLimitations" not in proposal[
        "contentJson"
    ]["brief"]
    assert proposal["derivedFromIds"] == []


@pytest.mark.asyncio
async def test_structure_proposal_requires_accepted_creative_brief(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    await source_db.execute(
        "UPDATE screenplay_projects SET active_stage = 'structure' WHERE id = ?",
        [project_id],
    )
    request = _request(project_id, active_stage="structure")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    scope_error, result = await _submit_structure(
        catalog,
        state,
        [{
            "id": "beat-inciting-radio",
            "order": 1,
            "label": "开场",
            "summary": "雾中广播响起。",
        }],
        [{
            "decisionId": "decision-radio-inciting",
            "structureUnitIds": ["beat-inciting-radio"],
            "implementation": "以广播作为第一幕激励事件。",
        }],
    )

    assert scope_error is None
    assert result.error_code == "tool_input_invalid"
    assert "accepted creative brief" in result.content


@pytest.mark.asyncio
async def test_structure_artifact_derives_project_format_and_parent(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief = await _advance_book_to_structure(source_db, created)
    initial_id = brief["id"]
    request = _request(project_id, active_stage="structure")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    assert catalog.enabled_names(request).issuperset({
        "beginScreenplayStructureArtifact",
        "appendScreenplayStructureBatch",
        "finalizeScreenplayStructureProposal",
    })
    assert "proposeEpisodeOutline" not in catalog.names
    assert "proposeBeatSheet" not in catalog.names

    scope_error, result = await _submit_structure(
        catalog,
        state,
        [{
            "id": "beat-inciting-radio",
            "order": 1,
            "label": "开场",
            "summary": "雾中广播响起。",
        }],
        [{
            "decisionId": "decision-radio-inciting",
            "structureUnitIds": ["beat-inciting-radio"],
            "implementation": "以广播作为第一幕激励事件。",
        }],
    )
    assert scope_error is None
    assert result.error_code is None
    assert result.effects[0].payload["kind"] == "beat_sheet"
    assert result.effects[0].payload["derivedFromIds"] == [initial_id]
    assert (
        result.effects[0].payload["contentJson"]["creativeBriefId"]
        == initial_id
    )
    assert result.effects[0].payload["contentJson"][
        "structureArtifactId"
    ]
    assert result.effects[0].payload["contentJson"]["artifactRef"].startswith(
        "artifact://purrtypos.screenplay/screenplay_structure_units/"
    )
    assert result.effects[0].payload["contentJson"]["decisionCoverage"] == [{
        "decisionId": "decision-radio-inciting",
        "structureUnitIds": ["beat-inciting-radio"],
        "implementation": "以广播作为第一幕激励事件。",
    }]


@pytest.mark.asyncio
async def test_episode_outline_matches_brief_scale_and_decisions(source_db):
    await _seed_source_project(source_db)
    created = await screenplay_crud.create_project(
        source_db,
        title="雾港连续剧",
        source_kind="book",
        source_book_id="book-source",
        screenplay_format="连续剧",
        approach="结构重组",
        premise="",
    )
    brief = await _advance_book_series_to_structure(
        source_db,
        created,
        episodes=2,
    )
    request = _request(
        created["project"]["id"],
        active_stage="structure",
    )
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)
    coverage = [{
        "decisionId": "decision-radio-inciting",
        "structureUnitIds": ["episode-1"],
        "implementation": "第一集以异常广播启动调查。",
    }]

    _, short_result = await _submit_structure(
        catalog,
        state,
        [{
            "id": "episode-1",
            "number": 1,
            "title": "雾中来信",
            "summary": "林岚第一次听见哥哥的声音。",
        }],
        coverage,
    )
    assert short_result.error_code == "tool_input_invalid"
    assert "episodeCount" in short_result.content

    _, result = await _submit_structure(
        catalog,
        state,
        [
            {
                "id": "episode-1",
                "number": 1,
                "title": "雾中来信",
                "summary": "林岚第一次听见哥哥的声音。",
            },
            {
                "id": "episode-2",
                "number": 2,
                "title": "广播来源",
                "summary": "林岚追踪信号并面对阶段性真相。",
            },
        ],
        coverage,
    )

    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["contentJson"]["creativeBriefId"] == brief["id"]
    assert len(proposal["contentJson"]["episodes"]) == 2
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="episode_outline",
        title=str(proposal["title"]),
        content_json=dict(proposal["contentJson"]),
        content_text=str(proposal["contentText"]),
        derived_from_ids=list(proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scene_request = _request(
        created["project"]["id"],
        active_stage="scenes",
    )
    scene_state = ScreenplayExecutionStateFactory().create(scene_request)
    _, episode_context_result = await _invoke(
        catalog,
        scene_state,
        "getScreenplayEpisodeContext",
        {"documentId": structure["id"], "episodeNumbers": [2]},
    )
    assert episode_context_result.error_code is None
    episode_context = json.loads(episode_context_result.content)
    assert len(episode_context["episodeIndex"]) == 2
    assert episode_context["selectedEpisodes"][0]["content_json"][
        "episode"
    ]["id"] == "episode-2"
    scenes = [
        {
            "id": "scene-episode-1",
            "order": 1,
            "heading": "内景·电台·夜",
            "episodeNumber": 1,
            "structureUnitIds": ["episode-1"],
            "objective": "确认广播来源",
            "conflict": "信号即将消失",
            "turn": "林岚听见哥哥的声音",
            "synopsis": "第一集以异常广播启动调查。",
        },
        {
            "id": "scene-episode-2",
            "order": 2,
            "heading": "外景·雾港·夜",
            "episodeNumber": 2,
            "structureUnitIds": ["episode-2"],
            "objective": "追踪信号终点",
            "conflict": "港区被封锁",
            "turn": "林岚找到哥哥留下的设备",
            "synopsis": "第二集给出阶段性真相。",
        },
    ]
    invalid_scenes = [dict(item) for item in scenes]
    invalid_scenes[0]["episodeNumber"] = 2
    _, invalid_scene_result = await _submit_scene_list(
        catalog,
        scene_state,
        invalid_scenes,
    )
    assert invalid_scene_result.error_code == "tool_input_invalid"
    assert "episodeNumber" in invalid_scene_result.content

    _, scene_result = await _submit_scene_list(
        catalog,
        scene_state,
        scenes,
    )
    assert scene_result.error_code is None
    scene_proposal = thaw_json_mapping(scene_result.effects[0].payload)
    assert scene_proposal["contentJson"]["structureId"] == structure["id"]
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="scene_list",
        title=str(scene_proposal["title"]),
        content_json=dict(scene_proposal["contentJson"]),
        content_text=str(scene_proposal["contentText"]),
        derived_from_ids=list(scene_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    draft_state = ScreenplayExecutionStateFactory().create(_request(
        created["project"]["id"],
        active_stage="draft",
    ))
    _, context_result = await _invoke(
        catalog,
        draft_state,
        "getScreenplayDraftContext",
        {},
    )
    assert context_result.error_code is None
    context_payload = json.loads(context_result.content)
    assert context_payload["nextEpisodeNumber"] == 1
    assert context_payload["selectedEpisodes"][0]["episodeNumber"] == 1
    assert [
        scene["id"]
        for scene in context_payload["selectedEpisodes"][0]["scenes"]
    ] == ["scene-episode-1"]
    _, draft_result = await _invoke(
        catalog,
        draft_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(1),
            "sceneText": "INT. 电台 - 夜\n\n林岚听见异常广播。",
        },
    )
    assert draft_result.error_code is None
    draft_proposal = thaw_json_mapping(draft_result.effects[0].payload)
    assert draft_proposal["contentJson"]["episodeDrafts"][0][
        "episodeNumber"
    ] == 1
    draft = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="scene_draft",
        title=str(draft_proposal["title"]),
        content_json=dict(draft_proposal["contentJson"]),
        content_text=str(draft_proposal["contentText"]),
        derived_from_ids=list(draft_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, draft["id"])
    episode_documents = await screenplay_crud.list_accepted_draft_episodes(
        source_db,
        created["project"]["id"],
    )
    assert episode_documents[0]["episode_number"] == 1
    assert episode_documents[0]["storage_mode"] == "revision_part"
    accepted_draft = await screenplay_crud.get_document(source_db, draft["id"])
    assert accepted_draft["content_json"]["episodeDrafts"][0][
        "episodeNumber"
    ] == 1


@pytest.mark.asyncio
async def test_scene_list_and_rolling_draft_follow_accepted_versions(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="开场、转折、结局",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    catalog = build_screenplay_tool_catalog(source_db)

    scene_request = _request(project_id, active_stage="scenes")
    scene_state = ScreenplayExecutionStateFactory().create(scene_request)
    scene_enabled = catalog.enabled_names(scene_request)
    assert {
        "beginSceneListArtifact",
        "appendSceneListBatch",
        "finalizeSceneListProposal",
    }.issubset(scene_enabled)
    assert "proposeSceneDraft" not in scene_enabled
    scope_error, scene_result = await _submit_scene_list(
        catalog,
        scene_state,
        [
                {
                    "id": "scene-1",
                    "order": 1,
                    "heading": "内景·电台·夜",
                    "structureUnitIds": ["beat-inciting-radio"],
                    "objective": "林岚确认信号来源",
                    "conflict": "信号随浓雾消失",
                    "turn": "她听见哥哥的声音",
                    "synopsis": "林岚修理设备时收到异常广播。",
                },
                {
                    "id": "scene-2",
                    "order": 2,
                    "heading": "外景·雾港·夜",
                    "structureUnitIds": ["beat-inciting-radio"],
                    "objective": "追踪广播",
                    "conflict": "港区封锁",
                    "turn": "她找到哥哥留下的设备",
                    "synopsis": "林岚进入封锁区。",
                },
            ],
    )
    assert scope_error is None
    assert scene_result.error_code is None
    scene_proposal = thaw_json_mapping(scene_result.effects[0].payload)
    assert scene_proposal["kind"] == "scene_list"
    assert scene_proposal["derivedFromIds"] == [structure["id"]]
    assert scene_proposal["contentJson"]["structureId"] == structure["id"]

    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title=str(scene_proposal["title"]),
        content_json=dict(scene_proposal["contentJson"]),
        content_text=str(scene_proposal["contentText"]),
        derived_from_ids=list(scene_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    project = await screenplay_crud.get_project(source_db, project_id)
    assert project["active_stage"] == "draft"

    draft_request = _request(project_id, active_stage="draft")
    first_state = ScreenplayExecutionStateFactory().create(draft_request)
    draft_enabled = catalog.enabled_names(draft_request)
    assert "proposeSceneDraft" in draft_enabled
    assert "finalizeSceneListProposal" not in draft_enabled
    assert catalog.get("proposeSceneDraft").max_argument_chars is None
    batch_request = _request(
        project_id,
        active_stage="draft",
        draft_scene_count=2,
    )
    batch_state = ScreenplayExecutionStateFactory().create(batch_request)
    _, incomplete_batch_result = await _invoke(
        catalog,
        batch_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(1),
            "sceneText": "INT. 电台 - 夜\n\n林岚调试设备。",
        },
    )
    assert incomplete_batch_result.error_code == "tool_input_invalid"
    assert "exactly 2 scene(s)" in incomplete_batch_result.content
    _, batch_result = await _invoke(
        catalog,
        batch_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(1),
            "sceneText": "INT. 电台 - 夜\n\n林岚调试设备。",
            "additionalScenes": [{
                "execution": _scene_execution_input(2),
                "sceneText": "EXT. 雾港 - 夜\n\n她走进浓雾。",
            }],
        },
    )
    assert batch_result.error_code is None
    batch_proposal = thaw_json_mapping(batch_result.effects[0].payload)
    assert batch_proposal["contentJson"]["newSceneIds"] == [
        "scene-1",
        "scene-2",
    ]
    assert batch_proposal["contentJson"]["isComplete"] is True
    assert batch_proposal["contentText"] == (
        "INT. 电台 - 夜\n\n林岚调试设备。\n\n"
        "EXT. 雾港 - 夜\n\n她走进浓雾。"
    )
    oversized_execution = _scene_execution_input(1)
    oversized_execution["objectiveResult"] = "过长" * 1_001
    _, oversized_execution_result = await _invoke(
        catalog,
        first_state,
        "proposeSceneDraft",
        {
            "execution": oversized_execution,
            "sceneText": "INT. 电台 - 夜\n\n林岚调试设备。",
        },
    )
    assert oversized_execution_result.error_code == "tool_input_invalid"
    assert "objectiveResult" in oversized_execution_result.content
    _, oversized_text_result = await _invoke(
        catalog,
        first_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(1),
            "sceneText": (
                "场" * (SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars + 1)
            ),
        },
    )
    assert oversized_text_result.error_code == "tool_input_invalid"
    assert "sceneText" in oversized_text_result.content
    _, first_result = await _invoke(
        catalog,
        first_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(1),
            "sceneText": "INT. 电台 - 夜\n\n林岚调试设备。",
        },
    )
    assert first_result.error_code is None
    first_proposal = thaw_json_mapping(first_result.effects[0].payload)
    first_draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title=str(first_proposal["title"]),
        content_json=dict(first_proposal["contentJson"]),
        content_text=str(first_proposal["contentText"]),
        derived_from_ids=list(first_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, first_draft["id"])
    project = await screenplay_crud.get_project(source_db, project_id)
    assert project["active_stage"] == "draft"

    second_state = ScreenplayExecutionStateFactory().create(draft_request)
    _, second_result = await _invoke(
        catalog,
        second_state,
        "proposeSceneDraft",
        {
            "execution": _scene_execution_input(2),
            "sceneText": "EXT. 雾港 - 夜\n\n她走进浓雾。",
        },
    )
    assert second_result.error_code is None
    second_proposal = thaw_json_mapping(second_result.effects[0].payload)
    assert set(second_proposal["derivedFromIds"]) == {
        scene_list["id"],
        first_draft["id"],
    }
    assert "sceneExecutions" not in second_proposal["contentJson"]
    assert second_proposal["contentJson"]["episodeDrafts"][0][
        "sceneIds"
    ] == ["scene-2"]
    assert second_proposal["contentText"] == "EXT. 雾港 - 夜\n\n她走进浓雾。"
    second_draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title=str(second_proposal["title"]),
        content_json=dict(second_proposal["contentJson"]),
        content_text=str(second_proposal["contentText"]),
        derived_from_ids=list(second_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, second_draft["id"])
    project = await screenplay_crud.get_project(source_db, project_id)
    assert project["active_stage"] == "review"


@pytest.mark.asyncio
async def test_rolling_draft_host_owns_next_scene_and_ignores_model_progress(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title="场景表",
        content_json=_book_scene_list(structure["id"], count=2),
        content_text="场景表",
        derived_from_ids=[structure["id"]],
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    request = _request(project_id, active_stage="draft")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    _, result = await _invoke(
        catalog,
        state,
        "proposeSceneDraft",
        {
            # Direct handler invocation simulates a provider that violates the
            # additionalProperties contract. Host progress remains authoritative.
            "sceneId": "scene-2",
            "sceneHeading": "第二场",
            "execution": _scene_execution_input(1),
            "completedSceneIds": ["scene-2"],
            "isComplete": True,
            "sceneText": "第一场正文",
        },
    )
    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["contentJson"]["sceneId"] == "scene-1"
    assert proposal["contentJson"]["completedSceneIds"] == ["scene-1"]
    assert proposal["contentJson"]["isComplete"] is False


@pytest.mark.asyncio
async def test_structure_artifact_resumes_replays_and_freezes_brief_contract(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief = await _advance_book_to_structure(source_db, created)
    state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="structure")
    )
    state.run_id = "run-structure-batches"
    catalog = build_screenplay_tool_catalog(source_db)

    _, begun = await _invoke(
        catalog,
        state,
        "beginScreenplayStructureArtifact",
        {"expectedUnitCount": 2},
    )
    assert begun.error_code is None
    receipt = json.loads(begun.content)
    assert receipt["structureKind"] == "beat_sheet"
    assert receipt["expectedDecisionIds"] == ["decision-radio-inciting"]
    assert receipt["remainingItemCount"] == 3

    first_item = {
        "itemType": "structure_unit",
        "index": 1,
        "id": "beat-radio",
        "title": "异常广播",
        "summary": "林岚听见哥哥的声音并开始追查。",
    }
    _, first = await _invoke(
        catalog,
        state,
        "appendScreenplayStructureBatch",
        {"items": [first_item]},
    )
    assert first.error_code is None
    assert json.loads(first.content)["remainingItemCount"] == 2

    catalog = build_screenplay_tool_catalog(source_db)
    _, replayed = await _invoke(
        catalog,
        state,
        "appendScreenplayStructureBatch",
        {"items": [first_item]},
    )
    assert replayed.error_code is None
    assert json.loads(replayed.content)["committedItemCount"] == 1

    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeScreenplayStructureProposal",
        {},
    )
    assert incomplete.error_code == "tool_input_invalid"
    assert "incomplete" in incomplete.content

    _, unknown_decision = await _invoke(
        catalog,
        state,
        "appendScreenplayStructureBatch",
        {"items": [{
            "itemType": "decision_coverage",
            "decisionId": "decision-invented-by-model",
            "structureUnitIds": ["beat-radio"],
            "implementation": "错误引用。",
        }]},
    )
    assert unknown_decision.error_code == "tool_input_invalid"
    assert "accepted creative brief" in unknown_decision.content

    batch_result = await CoreToolExecutor(catalog).execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(
                ToolCall(
                    id="call-structure-unit-2",
                    name="appendScreenplayStructureBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "structure_unit",
                        "index": 2,
                        "id": "beat-choice",
                        "title": "真相与告别",
                        "summary": "林岚确认真相并决定告别哥哥。",
                    }]}, ensure_ascii=False),
                ),
                ToolCall(
                    id="call-structure-decision",
                    name="appendScreenplayStructureBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "decision_coverage",
                        "decisionId": "decision-radio-inciting",
                        "structureUnitIds": ["beat-radio"],
                        "implementation": "把异常广播作为第一幕激励事件。",
                    }]}, ensure_ascii=False),
                ),
            ),
            allowed_tool_names=frozenset({
                "appendScreenplayStructureBatch"
            }),
            state=state,
        ),
        _RecordingSink(),
    )
    assert batch_result.outcome is ToolBatchOutcome.COMPLETED
    assert json.loads(batch_result.results[-1].content)[
        "remainingItemCount"
    ] == 0

    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeScreenplayStructureProposal",
        {},
    )
    assert finalized.error_code is None
    proposal = thaw_json_mapping(finalized.effects[0].payload)
    assert proposal["kind"] == "beat_sheet"
    assert proposal["derivedFromIds"] == [brief["id"]]
    assert [item["order"] for item in proposal["contentJson"]["beats"]] == [
        1,
        2,
    ]

    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title=str(proposal["title"]),
        content_json=dict(proposal["contentJson"]),
        content_text=str(proposal["contentText"]),
        derived_from_ids=list(proposal["derivedFromIds"]),
    )
    accepted = await screenplay_crud.accept_document(source_db, document["id"])
    assert accepted["status"] == "accepted"


@pytest.mark.asyncio
async def test_original_structure_artifact_needs_no_fake_decision_batch(source_db):
    created = await screenplay_crud.create_project(
        source_db,
        title="原创短片",
        source_kind="original",
        source_book_id=None,
        screenplay_format="短片",
        approach="人物驱动",
        premise="陌生人在末班车上交换秘密。",
    )
    brief = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="creative_brief",
        title="原创简报",
        content_json={
            "schemaVersion": 1,
            "documentKind": "creative_brief",
            "brief": {
                "logline": "两名陌生人在末班车上交换秘密。",
                "coreConflict": "坦白可能带来自由，也可能毁掉彼此。",
            },
        },
        content_text="原创短片创作简报。",
        derived_from_ids=[created["initialDocument"]["id"]],
    )
    await screenplay_crud.accept_document(source_db, brief["id"])
    request = _request(
        created["project"]["id"],
        source_book_id="",
        active_stage="structure",
    )
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    _, result = await _submit_structure(
        catalog,
        state,
        [{
            "id": "beat-confession",
            "order": 1,
            "label": "交换秘密",
            "summary": "两人决定在终点前各自坦白一件秘密。",
        }],
        [],
    )

    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["contentJson"]["decisionCoverage"] == []
    assert proposal["contentJson"]["creativeBriefId"] == brief["id"]


@pytest.mark.asyncio
async def test_scene_list_artifact_resumes_replays_and_finalizes_in_batches(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scenes = _book_scene_list(structure["id"], count=3)["scenes"]
    state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="scenes")
    )
    state.run_id = "run-scene-list-batches"
    catalog = build_screenplay_tool_catalog(source_db)

    _, begun = await _invoke(
        catalog,
        state,
        "beginSceneListArtifact",
        {"expectedSceneCount": 3},
    )
    assert begun.error_code is None
    assert json.loads(begun.content)["remainingItemCount"] == 3
    assert begun.effects == ()
    scoped = await source_db.fetch_one(
        "SELECT a.artifact_scope, a.work_item_id, wi.status AS work_item_status, "
        "wir.relation, c.run_id AS claim_run_id "
        "FROM ai_agent_artifacts AS a "
        "JOIN ai_agent_work_items AS wi ON wi.id = a.work_item_id "
        "JOIN ai_agent_work_item_runs AS wir ON wir.work_item_id = wi.id "
        "LEFT JOIN ai_agent_artifact_claims AS c ON c.artifact_id = a.id "
        "WHERE a.owner_id = ? AND a.kind = 'scene_list_batches' "
        "AND wir.run_id = ?",
        [project_id, state.run_id],
    )
    assert scoped is not None
    assert scoped["artifact_scope"] == ArtifactScope.WORK_ITEM.value
    assert str(scoped["work_item_id"])
    assert scoped["work_item_status"] == "open"
    assert scoped["relation"] == "created"
    assert scoped["claim_run_id"] == state.run_id

    _, first = await _invoke(
        catalog,
        state,
        "appendSceneListBatch",
        {"scenes": scenes[:1]},
    )
    assert first.error_code is None
    assert json.loads(first.content)["remainingItemCount"] == 2
    assert first.effects == ()

    # Rebuilding the catalog simulates process/service reconstruction. The
    # persisted run-owned artifact remains the authority, and an exact batch
    # retry does not add another row.
    catalog = build_screenplay_tool_catalog(source_db)
    _, replayed = await _invoke(
        catalog,
        state,
        "appendSceneListBatch",
        {"scenes": scenes[:1]},
    )
    assert replayed.error_code is None
    assert json.loads(replayed.content)["committedItemCount"] == 1

    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeSceneListProposal",
        {},
    )
    assert incomplete.error_code == "tool_input_invalid"
    assert "incomplete" in incomplete.content

    batch_result = await CoreToolExecutor(catalog).execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(
                ToolCall(
                    id="call-scene-batch-2",
                    name="appendSceneListBatch",
                    arguments_json=json.dumps({"scenes": scenes[1:2]}),
                ),
                ToolCall(
                    id="call-scene-batch-3",
                    name="appendSceneListBatch",
                    arguments_json=json.dumps({"scenes": scenes[2:]}),
                ),
            ),
            allowed_tool_names=frozenset({"appendSceneListBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert batch_result.outcome is ToolBatchOutcome.COMPLETED
    assert len(batch_result.results) == 2
    assert json.loads(batch_result.results[-1].content)[
        "remainingItemCount"
    ] == 0

    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeSceneListProposal",
        {},
    )
    assert finalized.error_code is None
    proposal = thaw_json_mapping(finalized.effects[0].payload)
    assert len(proposal["contentJson"]["scenes"]) == 3
    assert proposal["contentJson"]["artifactRef"].startswith("artifact://")
    completed = await source_db.fetch_one(
        "SELECT wi.status, COUNT(c.artifact_id) AS claim_count "
        "FROM ai_agent_work_items AS wi "
        "LEFT JOIN ai_agent_artifact_claims AS c "
        "ON c.work_item_id = wi.id WHERE wi.id = ? GROUP BY wi.id",
        [scoped["work_item_id"]],
    )
    assert completed == {"status": "completed", "claim_count": 0}

    _, replayed_final = await _invoke(
        catalog,
        state,
        "finalizeSceneListProposal",
        {},
    )
    assert replayed_final.error_code is None
    assert replayed_final.effects[0].payload == finalized.effects[0].payload

    # A later delivery-recovery Run receives read-only reference access to the
    # finalized Artifact. It can replay the proposal projection without a
    # write claim or regenerating any scene content.
    completed_item = await SqliteWorkItemRepository(source_db).load(
        str(scoped["work_item_id"])
    )
    assert completed_item is not None
    recovery_run_id = "run-scene-list-delivery-recovery"
    await SqliteWorkItemRepository(source_db).link_run(
        WorkItemRunLinkCommand(
            work_item_id=completed_item.id,
            run_id=recovery_run_id,
            relation=WorkItemRunRelation.REFERENCE,
            expected_revision=completed_item.revision,
        )
    )
    recovery_state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="scenes")
    )
    recovery_state.run_id = recovery_run_id
    _, recovered_final = await _invoke(
        build_screenplay_tool_catalog(source_db),
        recovery_state,
        "finalizeSceneListProposal",
        {},
    )
    assert recovered_final.error_code is None
    assert recovered_final.effects[0].payload == finalized.effects[0].payload
    row = await source_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_batches",
    )
    assert int(row["count"]) == 3
    assert await screenplay_crud.delete_project(source_db, project_id) is True
    artifact_row = await source_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts",
    )
    batch_row = await source_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_batches",
    )
    assert int(artifact_row["count"]) == 0
    assert int(batch_row["count"]) == 0


@pytest.mark.asyncio
async def test_scene_list_work_item_continues_in_a_later_run_without_begin(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scenes = _book_scene_list(structure["id"], count=2)["scenes"]
    request = _request(project_id, active_stage="scenes")
    first_state = ScreenplayExecutionStateFactory().create(request)
    first_state.run_id = "run-scene-list-origin"
    catalog = build_screenplay_tool_catalog(source_db)

    _, begun = await _invoke(
        catalog,
        first_state,
        "beginSceneListArtifact",
        {"expectedSceneCount": 2},
    )
    assert begun.error_code is None
    _, first = await _invoke(
        catalog,
        first_state,
        "appendSceneListBatch",
        {"scenes": scenes[:1]},
    )
    assert first.error_code is None
    artifact = await source_db.fetch_one(
        "SELECT id, work_item_id, revision FROM ai_agent_artifacts "
        "WHERE owner_id = ? AND kind = 'scene_list_batches'",
        [project_id],
    )
    assert artifact is not None

    claims = SqliteArtifactClaimRepository(source_db)
    assert await claims.release_for_run(str(first_state.run_id)) == 1
    second_run_id = "run-scene-list-continuation"
    await SqliteWorkItemRepository(source_db).link_run(WorkItemRunLinkCommand(
        work_item_id=str(artifact["work_item_id"]),
        run_id=second_run_id,
        relation=WorkItemRunRelation.CONTINUATION,
        expected_revision=1,
    ))
    await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=str(artifact["id"]),
        work_item_id=str(artifact["work_item_id"]),
        run_id=second_run_id,
        expected_revision=int(artifact["revision"]),
        lease_duration_ms=300_000,
    ))

    second_state = ScreenplayExecutionStateFactory().create(request)
    second_state.run_id = second_run_id
    catalog = build_screenplay_tool_catalog(source_db)
    _, second = await _invoke(
        catalog,
        second_state,
        "appendSceneListBatch",
        {"scenes": scenes[1:]},
    )
    assert second.error_code is None
    assert json.loads(second.content)["remainingItemCount"] == 0
    _, finalized = await _invoke(
        catalog,
        second_state,
        "finalizeSceneListProposal",
        {},
    )
    assert finalized.error_code is None
    row = await source_db.fetch_one(
        "SELECT COUNT(*) AS artifact_count, "
        "COUNT(DISTINCT a.work_item_id) AS work_item_count, "
        "MAX(wi.status) AS status "
        "FROM ai_agent_artifacts AS a "
        "JOIN ai_agent_work_items AS wi ON wi.id = a.work_item_id "
        "WHERE a.owner_id = ? AND a.kind = 'scene_list_batches'",
        [project_id],
    )
    assert row == {
        "artifact_count": 1,
        "work_item_count": 1,
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_review_artifact_resumes_replays_and_rejects_unknown_scenes(
    source_db,
):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title="场景表",
        content_json=_book_scene_list(structure["id"]),
        content_text="场景表",
        derived_from_ids=[structure["id"]],
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title="完整剧本",
        content_json={
            **_book_draft_content(
                scene_list["id"],
                ["scene-1"],
                scene_text="INT. 电台 - 夜\n\n@林岚\n广播开始了。",
            ),
            "isComplete": True,
        },
        content_text="INT. 电台 - 夜\n\n@林岚\n广播开始了。",
        derived_from_ids=[scene_list["id"]],
    )
    await screenplay_crud.accept_document(source_db, draft["id"])
    state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="review")
    )
    state.run_id = "run-review-batches"
    catalog = build_screenplay_tool_catalog(source_db)

    _, begun = await _invoke(
        catalog,
        state,
        "beginScreenplayReviewArtifact",
        {"verdict": "revise", "expectedIssueCount": 2},
    )
    assert begun.error_code is None
    receipt = json.loads(begun.content)
    assert receipt["reviewedDraftId"] == draft["id"]
    assert receipt["allowedSceneIds"] == ["scene-1"]
    assert receipt["verificationIssueIds"] == []
    assert receipt["remainingItemCount"] == 3

    summary_item = {
        "itemType": "review_summary",
        "summary": "结构完整，但对白和转折仍需收紧。",
        "strengths": ["广播意象统一"],
    }
    _, first = await _invoke(
        catalog,
        state,
        "appendScreenplayReviewBatch",
        {"items": [summary_item]},
    )
    assert first.error_code is None
    assert json.loads(first.content)["remainingItemCount"] == 2

    catalog = build_screenplay_tool_catalog(source_db)
    _, replayed = await _invoke(
        catalog,
        state,
        "appendScreenplayReviewBatch",
        {"items": [summary_item]},
    )
    assert replayed.error_code is None
    assert json.loads(replayed.content)["committedItemCount"] == 1

    _, invalid_scene = await _invoke(
        catalog,
        state,
        "appendScreenplayReviewBatch",
        {"items": [{
            "itemType": "review_issue",
            "index": 1,
            "id": "issue-invalid-scene",
            "severity": "major",
            "category": "continuity",
            "sceneIds": ["scene-not-in-draft"],
            "executionFields": ["continuityState"],
            "problem": "错误场景引用。",
            "recommendation": "不应提交。",
            "acceptanceCriteria": "不应提交。",
        }]},
    )
    assert invalid_scene.error_code == "tool_input_invalid"
    assert "completed scenes" in invalid_scene.content

    _, incomplete = await _invoke(
        catalog,
        state,
        "finalizeScreenplayReviewProposal",
        {},
    )
    assert incomplete.error_code == "tool_input_invalid"
    assert "incomplete" in incomplete.content

    issue_result = await CoreToolExecutor(catalog).execute_batch(
        ToolBatchRequest(
            run_id=state.run_id,
            calls=(
                ToolCall(
                    id="call-review-issue-1",
                    name="appendScreenplayReviewBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "review_issue",
                        "index": 1,
                        "id": "issue-dialogue",
                        "severity": "major",
                        "category": "dialogue",
                        "sceneIds": ["scene-1"],
                        "executionFields": ["conflictResult"],
                        "problem": "对白解释信息过多。",
                        "recommendation": "改为行动与潜台词。",
                        "acceptanceCriteria": "背景信息由行动传达。",
                    }]}, ensure_ascii=False),
                ),
                ToolCall(
                    id="call-review-issue-2",
                    name="appendScreenplayReviewBatch",
                    arguments_json=json.dumps({"items": [{
                        "itemType": "review_issue",
                        "index": 2,
                        "id": "issue-turn",
                        "severity": "minor",
                        "category": "pacing",
                        "sceneIds": ["scene-1"],
                        "executionFields": ["turnResult"],
                        "problem": "转折出现得过快。",
                        "recommendation": "增加一次失败尝试。",
                        "acceptanceCriteria": "转折前存在明确阻力升级。",
                    }]}, ensure_ascii=False),
                ),
            ),
            allowed_tool_names=frozenset({"appendScreenplayReviewBatch"}),
            state=state,
        ),
        _RecordingSink(),
    )
    assert issue_result.outcome is ToolBatchOutcome.COMPLETED
    assert json.loads(issue_result.results[-1].content)[
        "remainingItemCount"
    ] == 0

    _, finalized = await _invoke(
        catalog,
        state,
        "finalizeScreenplayReviewProposal",
        {},
    )
    assert finalized.error_code is None
    proposal = thaw_json_mapping(finalized.effects[0].payload)
    assert proposal["contentJson"]["reviewedDraftId"] == draft["id"]
    assert proposal["contentJson"]["reviewArtifactId"]
    assert len(proposal["contentJson"]["issues"]) == 2
    assert proposal["derivedFromIds"] == [draft["id"]]

    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="review",
        title=str(proposal["title"]),
        content_json=dict(proposal["contentJson"]),
        content_text=str(proposal["contentText"]),
        derived_from_ids=list(proposal["derivedFromIds"]),
    )
    accepted = await screenplay_crud.accept_document(source_db, document["id"])
    assert accepted["status"] == "accepted"


@pytest.mark.asyncio
async def test_review_then_revision_requires_user_accepted_report(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title="场景表",
        content_json=_book_scene_list(structure["id"]),
        content_text="场景表",
        derived_from_ids=[structure["id"]],
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title="完整剧本",
        content_json={
            **_book_draft_content(
                scene_list["id"],
                ["scene-1"],
                scene_text="INT. 电台 - 夜\n\n@林岚\n广播开始了。",
            ),
            "isComplete": True,
        },
        content_text="INT. 电台 - 夜\n\n@林岚\n广播开始了。",
        derived_from_ids=[scene_list["id"]],
    )
    await screenplay_crud.accept_document(source_db, draft["id"])
    request = _request(project_id, active_stage="review")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)
    enabled = catalog.enabled_names(request)
    assert {
        "beginScreenplayReviewArtifact",
        "appendScreenplayReviewBatch",
        "finalizeScreenplayReviewProposal",
        "beginScreenplayRevisionArtifact",
        "appendScreenplayRevisionBatch",
        "appendScreenplayRevisionResolutionBatch",
        "finalizeScreenplayRevisionProposal",
    }.issubset(enabled)

    _, blocked_revision = await _submit_revision(
        catalog,
        state,
        issue_resolutions=[{
                "issueId": "issue-1",
                "status": "resolved",
                "resolutionEvidence": "对白已改写。",
            }],
        execution_updates=[{
                "sceneId": "scene-1",
                **_scene_execution_input(1),
            }],
        revision_summary="调整对白。",
        revised_scenes=[{
            "sceneId": "scene-1",
            "sceneText": "修订稿",
        }],
    )
    assert blocked_revision.error_code == "tool_input_invalid"
    assert "accepted screenplay review" in blocked_revision.content

    _, review_result = await _submit_review(
        catalog,
        state,
        summary="整体结构清晰，对白仍可收紧。",
        strengths=["广播意象统一"],
        issues=[{
            "id": "issue-1",
            "severity": "major",
            "category": "dialogue",
            "sceneIds": ["scene-1"],
            "executionFields": [
                "conflictResult",
                "turnResult",
            ],
            "problem": "对白解释信息过多。",
            "recommendation": "改为行动与潜台词。",
            "acceptanceCriteria": "信息通过行动和潜台词传达。",
        }],
        verdict="revise",
    )
    assert review_result.error_code is None
    review_proposal = thaw_json_mapping(review_result.effects[0].payload)
    assert review_proposal["kind"] == "review"
    assert review_proposal["derivedFromIds"] == [draft["id"]]
    review = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="review",
        title=str(review_proposal["title"]),
        content_json=dict(review_proposal["contentJson"]),
        content_text=str(review_proposal["contentText"]),
        derived_from_ids=list(review_proposal["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, review["id"])

    _, revision_result = await _submit_revision(
        catalog,
        state,
        issue_resolutions=[{
                "issueId": "issue-1",
                "status": "resolved",
                "resolutionEvidence": "林岚用“又是你”确认识别结果，不再口述背景。",
            }],
        execution_updates=[{
                "sceneId": "scene-1",
                "objectiveResult": "林岚确认异常广播来自熟悉的人。",
                "conflictResult": "短句暴露她认识声音来源，却隐藏具体关系。",
                "turnResult": "她从追踪信号转为直接回应声音。",
                "continuityState": "林岚进入主动对话，身份悬念留待下一场。",
                "unresolvedNotes": ["声音来源的真实身份仍未揭示。"],
            }],
        revision_summary="将说明性对白改为带潜台词的短句。",
        revised_scenes=[{
            "sceneId": "scene-1",
            "sceneText": "INT. 电台 - 夜\n\n@林岚\n又是你。",
        }],
    )
    assert revision_result.error_code is None
    revision = thaw_json_mapping(revision_result.effects[0].payload)
    assert revision["kind"] == "scene_draft"
    assert revision["contentJson"]["isComplete"] is True
    assert revision["contentJson"]["resolvedIssueIds"] == ["issue-1"]
    assert revision["contentJson"]["partiallyResolvedIssueIds"] == []
    assert revision["contentJson"]["reassessedSceneIds"] == ["scene-1"]
    assert "sceneExecutions" not in revision["contentJson"]
    assert (
        revision["contentJson"]["episodeDrafts"][0][
            "sceneExecutions"
        ][0]["turnResult"]
        == "她从追踪信号转为直接回应声音。"
    )
    assert set(revision["derivedFromIds"]) == {
        draft["id"],
        review["id"],
    }
    revision_document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title=str(revision["title"]),
        content_json=dict(revision["contentJson"]),
        content_text=str(revision["contentText"]),
        derived_from_ids=list(revision["derivedFromIds"]),
    )
    await screenplay_crud.accept_document(source_db, revision_document["id"])

    _, rereview_result = await _submit_review(
        catalog,
        state,
        summary="说明性对白问题已经修复。",
        strengths=["潜台词更集中"],
        issues=[],
        verification_results=[{
            "issueId": "issue-1",
            "status": "verified",
            "verificationEvidence": "“又是你”以潜台词替代了背景解释。",
        }],
        verdict="ready",
    )

    assert rereview_result.error_code is None
    rereview = thaw_json_mapping(rereview_result.effects[0].payload)
    assert (
        rereview["contentJson"]["verificationOfReviewId"]
        == review["id"]
    )
    assert rereview["contentJson"]["verifiedIssueIds"] == ["issue-1"]
    assert rereview["contentJson"]["failedVerificationIssueIds"] == []
    verification_batches = await source_db.fetch_all(
        "SELECT items_json FROM ai_agent_artifact_batches "
        "WHERE artifact_id = ? ORDER BY sequence ASC",
        [rereview["contentJson"]["reviewArtifactId"]],
    )
    verification_items = [
        item
        for row in verification_batches
        for item in json.loads(row["items_json"])
        if item.get("itemType") == "verification_result"
    ]
    assert verification_items == [{
        "itemType": "verification_result",
        "index": 1,
        "status": "verified",
        "verificationEvidence": "“又是你”以潜台词替代了背景解释。",
    }]


@pytest.mark.asyncio
async def test_revision_artifact_replaces_only_affected_scene_text(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief_id = (await _advance_book_to_structure(source_db, created))["id"]
    structure = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="beat_sheet",
        title="节拍表",
        content_json=_book_beat_structure(brief_id),
        content_text="结构",
        derived_from_ids=[brief_id],
    )
    await screenplay_crud.accept_document(source_db, structure["id"])
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title="场景表",
        content_json=_book_scene_list(structure["id"], count=2),
        content_text="场景表",
        derived_from_ids=[structure["id"]],
    )
    await screenplay_crud.accept_document(source_db, scene_list["id"])
    original_scene_1 = "INT. 电台 - 夜\n\n@林岚\n第一场保持不变。"
    original_scene_2 = "EXT. 雾港 - 夜\n\n@林岚\n第二场旧对白。"
    first_draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title="第一场正文",
        content_json=_book_draft_content(
            scene_list["id"],
            ["scene-1"],
            scene_text=original_scene_1,
        ),
        content_text=original_scene_1,
        derived_from_ids=[scene_list["id"]],
    )
    await screenplay_crud.accept_document(source_db, first_draft["id"])
    draft = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title="完整剧本",
        content_json={
            **_book_draft_content(
                scene_list["id"],
                ["scene-1", "scene-2"],
                scene_text=original_scene_2,
            ),
            "isComplete": True,
        },
        content_text=original_scene_2,
        derived_from_ids=[scene_list["id"], first_draft["id"]],
    )
    await screenplay_crud.accept_document(source_db, draft["id"])
    review = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="review",
        title="审阅",
        content_json={
            "reviewedDraftId": draft["id"],
            "summary": "第二场对白需要收紧。",
            "strengths": ["第一场目标明确"],
            "issues": [{
                "id": "issue-scene-2",
                "severity": "major",
                "category": "dialogue",
                "sceneIds": ["scene-2"],
                "executionFields": ["turnResult"],
                "problem": "第二场对白解释过多。",
                "recommendation": "改为潜台词。",
                "acceptanceCriteria": "第二场不再直接解释背景。",
            }],
            "verdict": "revise",
        },
        content_text="第二场需要修订。",
        derived_from_ids=[draft["id"]],
    )
    await screenplay_crud.accept_document(source_db, review["id"])
    state = ScreenplayExecutionStateFactory().create(
        _request(project_id, active_stage="review")
    )
    revised_scene_2 = "EXT. 雾港 - 夜\n\n@林岚\n你终于回应了。"
    _, result = await _submit_revision(
        build_screenplay_tool_catalog(source_db),
        state,
        issue_resolutions=[{
            "issueId": "issue-scene-2",
            "status": "resolved",
            "resolutionEvidence": "第二场改用带潜台词的短句。",
        }],
        execution_updates=[{
            "sceneId": "scene-2",
            **_scene_execution_input(2),
        }],
        revision_summary="仅修订第二场对白。",
        revised_scenes=[{
            "sceneId": "scene-2",
            "sceneText": revised_scene_2,
        }],
    )

    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["contentText"] == revised_scene_2
    assert original_scene_2 not in proposal["contentText"]
    assert proposal["contentJson"]["reassessedSceneIds"] == ["scene-2"]
    assert proposal["contentJson"]["artifactRef"].startswith("artifact://")
    artifact_id = proposal["contentJson"]["revisionArtifactId"]
    rows = await source_db.fetch_all(
        "SELECT items_json FROM ai_agent_artifact_batches "
        "WHERE artifact_id = ?",
        [artifact_id],
    )
    assert len(rows) == 2
    artifact_items = [
        item
        for row in rows
        for item in json.loads(row["items_json"])
    ]
    assert [
        item["sceneId"]
        for item in artifact_items
        if item["itemType"] == "scene_revision"
    ] == ["scene-2"]
    assert [
        item["issueId"]
        for item in artifact_items
        if item["itemType"] == "issue_resolution"
    ] == ["issue-scene-2"]

    revision_document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title=str(proposal["title"]),
        content_json=dict(proposal["contentJson"]),
        content_text=str(proposal["contentText"]),
        derived_from_ids=list(proposal["derivedFromIds"]),
    )
    accepted = await screenplay_crud.accept_document(
        source_db,
        revision_document["id"],
    )
    assert accepted["status"] == "accepted"


@pytest.mark.asyncio
async def test_character_read_records_run_scoped_source_receipt(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-character"
    catalog = build_screenplay_tool_catalog(source_db)

    scope_error, result = await _invoke(
        catalog,
        state,
        "getSourceCharacters",
        {"names": ["林岚"]},
    )

    assert scope_error is None
    assert result.error_code is None
    assert "电台维修员" in result.content
    refs = await list_source_refs(
        source_db,
        project_id=project_id,
        agent_run_id="run-character",
    )
    assert len(refs) == 1
    assert refs[0]["source_type"] == "character"
    assert refs[0]["source_id"].isdigit()
    assert len(refs[0]["source_revision"]) == 64


@pytest.mark.asyncio
async def test_source_scope_rejects_model_owned_book_id(source_db):
    created = await _seed_source_project(source_db)
    request = _request(created["project"]["id"])
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    scope_error, result = await _invoke(
        catalog,
        state,
        "searchSourceMaterial",
        {"query": "秘密", "bookId": "book-other"},
    )

    assert result is None
    assert "host-bound" in scope_error


@pytest.mark.asyncio
async def test_passage_read_cannot_cross_bound_source_book(source_db):
    created = await _seed_source_project(source_db)
    request = _request(created["project"]["id"])
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-cross-scope"
    catalog = build_screenplay_tool_catalog(source_db)

    scope_error, result = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {
            "sources": [{
                "sourceType": "chapter",
                "sourceId": "chapter-other",
            }],
        },
    )

    assert scope_error is None
    assert result.error_code == "tool_input_invalid"
    assert "outside the bound source book" in result.content
    refs = await list_source_refs(
        source_db,
        project_id=created["project"]["id"],
        agent_run_id="run-cross-scope",
    )
    assert refs == []


@pytest.mark.asyncio
async def test_search_then_read_records_minimal_evidence_set(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-search"
    catalog = build_screenplay_tool_catalog(source_db)

    _, search = await _invoke(
        catalog,
        state,
        "searchSourceMaterial",
        {"query": "失踪哥哥", "sourceTypes": ["chapter"], "limit": 3},
    )
    payload = json.loads(search.content)
    assert payload["hits"][0]["sourceId"] == "chapter-source"

    _, passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {
            "sources": [{
                "sourceType": "chapter",
                "sourceId": "chapter-source",
            }],
        },
    )
    assert "林岚在雾港" in passage.content

    refs = await list_source_refs(
        source_db,
        project_id=project_id,
        agent_run_id="run-search",
    )
    assert len(refs) == 1
    assert refs[0]["source_type"] == "chapter"


@pytest.mark.asyncio
async def test_restricted_adaptation_scope_filters_every_narrative_read(
    source_db,
):
    await _seed_source_project(source_db)
    for row in [
        ("chapter-second", "writing-source", "第二章 灯塔", 2),
        ("chapter-third", "writing-source", "第三章 真相", 3),
    ]:
        await source_db.execute(
            "INSERT INTO outline_chapters "
            "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
            list(row),
        )
    await source_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-second", "范围内灯塔发出回声。"],
    )
    await source_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-third", "范围外秘密揭示哥哥仍然活着。"],
    )
    for row in [
        ("outline-second", "第二章大纲", "chapter-second", "范围内灯塔"),
        ("outline-third", "第三章大纲", "chapter-third", "范围外秘密"),
    ]:
        await source_db.execute(
            "INSERT INTO outlines "
            "(id, title, type, book_id, writing_chapter_id, markdown_content) "
            "VALUES (?, ?, 'chapter', 'book-source', ?, ?)",
            list(row),
        )
    created = await screenplay_crud.create_project(
        source_db,
        title="前两章改编",
        source_kind="book",
        source_book_id="book-source",
        screenplay_format="电影",
        approach="截取改编",
        premise="",
        source_scope={"mode": "first_chapters", "count": 2},
    )
    project_id = created["project"]["id"]
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    _, overview = await _invoke(catalog, state, "getSourceBookOverview", {})
    overview_payload = json.loads(overview.content)
    assert overview_payload["sourceScope"]["mode"] == "first_chapters"
    assert {
        item["id"] for item in overview_payload["writingChapters"]
    } == {"chapter-source", "chapter-second"}
    assert {
        item["id"] for item in overview_payload["outlines"]
    } == {"outline-second"}
    _, coverage_plan = await _invoke(
        catalog,
        state,
        "getSourceCoveragePlan",
        {},
    )
    coverage_payload = json.loads(coverage_plan.content)
    assert coverage_payload["selectedChapterCount"] == 2
    assert set(coverage_payload["batches"][0]["chapterIds"]) == {
        "chapter-source",
        "chapter-second",
    }

    _, inside_search = await _invoke(
        catalog,
        state,
        "searchSourceMaterial",
        {"query": "范围内灯塔", "sourceTypes": ["chapter", "outline"]},
    )
    assert {
        item["sourceId"]
        for item in json.loads(inside_search.content)["hits"]
    } == {"chapter-second", "outline-second"}

    _, outside_search = await _invoke(
        catalog,
        state,
        "searchSourceMaterial",
        {"query": "范围外秘密", "sourceTypes": ["chapter", "outline"]},
    )
    assert json.loads(outside_search.content)["hits"] == []

    _, outside_passage = await _invoke(
        catalog,
        state,
        "readSourcePassages",
        {
            "sources": [{
                "sourceType": "chapter",
                "sourceId": "chapter-third",
            }],
        },
    )
    assert outside_passage.error_code == "tool_input_invalid"
    assert "adaptation range" in outside_passage.content

    character_scope_error, character_result = await _invoke(
        catalog,
        state,
        "getSourceCharacters",
        {"names": ["林岚"]},
    )
    assert character_result is None
    assert "book-global" in character_scope_error

    _, global_search = await _invoke(
        catalog,
        state,
        "searchSourceMaterial",
        {"query": "林岚", "sourceTypes": ["character"]},
    )
    assert global_search.error_code == "tool_input_invalid"
    assert "restricted adaptation range" in global_search.content


@pytest.mark.asyncio
async def test_saving_agent_document_attaches_run_refs_atomically(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    request = _request(project_id)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "run-save"
    catalog = build_screenplay_tool_catalog(source_db)
    _, result = await _invoke(
        catalog,
        state,
        "getSourceWorldSettings",
        {"names": ["雾港"], "includeBackground": True},
    )
    assert result.error_code is None

    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="creative_brief",
        title="有来源的简报",
        content_json={"schemaVersion": 1},
        content_text="以雾港异常广播为核心意象。",
        derived_from_ids=[created["initialDocument"]["id"]],
        source_run_id="run-save",
    )
    refs = await list_source_refs(
        source_db,
        project_id=project_id,
        document_id=document["id"],
    )

    assert {ref["source_type"] for ref in refs} == {
        "setting",
        "background",
    }
    assert all(ref["document_id"] == document["id"] for ref in refs)
