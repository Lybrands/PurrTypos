from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from application.request_mapping import to_agent_request
from agent_core.json_values import thaw_json_mapping
from database.connection import DatabaseConnection
from database.crud import screenplay as screenplay_crud
from database.crud.screenplay_source_refs import (
    list_source_refs,
    record_source_refs,
)
from domains.screenplay.execution_state import ScreenplayExecutionStateFactory
from domains.screenplay.tool_contracts import (
    SCREENPLAY_READ_TOOL_NAMES,
    SCREENPLAY_TOOL_NAMES,
)
from infrastructure.screenplay import build_screenplay_tool_catalog
from exceptions import AppError
from schemas.ai import ChatStreamRequest


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


def _request(
    project_id: str,
    source_book_id: str = "book-source",
    *,
    active_stage: str = "orientation",
):
    return to_agent_request(
        ChatStreamRequest(
            messages=[{"role": "user", "content": "检索原作并完善简报"}],
            apiKey="key",
            options={"model": "model"},
            agentProfile="screenplay",
            screenplayProjectId=project_id,
            sourceBookId=source_book_id,
            activeStage=active_stage,
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


async def _accept_source_analysis(db, created):
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
                    "limitations": [],
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
        "completedSceneIds": completed_scene_ids,
        "sceneExecutions": executions,
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
        "proposeSourceAnalysis",
    })
    assert all(
        catalog.get(name).policy.mode.value == "read"
        for name in SCREENPLAY_READ_TOOL_NAMES
    )
    assert all(
        catalog.get(name).policy.mode.value == "propose"
        for name in {
            "proposeSourceAnalysis",
            "proposeCreativeBrief",
            "proposeBeatSheet",
            "proposeEpisodeOutline",
            "proposeSceneList",
            "proposeSceneDraft",
            "proposeScreenplayReview",
            "proposeScreenplayRevision",
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
        "proposeCreativeBrief",
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
    assert stale_result.error_code == "tool_execution_failed"
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

    scope_error, result = await _invoke(
        catalog,
        state,
        "proposeSourceAnalysis",
        {
            "analysis": {
                "rangeSummary": "整本作品，当前一章。",
                "narrativeSummary": "林岚追查雾中广播。",
                "coverage": {
                    "selectedChapterCount": 1,
                    "readChapterIds": ["chapter-source"],
                    "sampledChapterIds": [],
                    "limitations": [],
                },
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
            "contentText": "# 原作范围分析\n\n主线由异常广播触发。",
        },
    )

    assert scope_error is None
    assert result.error_code is None
    proposal = thaw_json_mapping(result.effects[0].payload)
    assert proposal["kind"] == "source_analysis"
    assert proposal["derivedFromIds"] == [created["initialDocument"]["id"]]

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
async def test_source_analysis_cannot_accept_unread_evidence(source_db):
    created = await _seed_source_project(source_db)
    document = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="source_analysis",
        title="无凭据分析",
        content_json={
            "analysis": {
                "narrativeSummary": "未经读取的总结。",
                "coverage": {
                    "selectedChapterCount": 1,
                    "readChapterIds": ["chapter-source"],
                    "sampledChapterIds": [],
                    "limitations": [],
                },
                "plotEvents": [{
                    "order": 1,
                    "event": "假定事件",
                    "consequence": "假定结果",
                }],
                "evidence": [{
                    "sourceType": "chapter",
                    "sourceId": "chapter-source",
                    "claim": "这条来源没有绑定到文档",
                }],
            },
        },
        content_text="# 无凭据分析",
        derived_from_ids=[created["initialDocument"]["id"]],
    )

    with pytest.raises(AppError, match="未实际读取"):
        await screenplay_crud.accept_document(source_db, document["id"])


@pytest.mark.asyncio
async def test_source_analysis_cannot_understate_locked_scope(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    run_id = "run-understated-scope"
    await record_source_refs(
        source_db,
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
    document = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="source_analysis",
        title="缩小范围的分析",
        content_json={
            "analysis": {
                "narrativeSummary": "林岚追查雾中广播。",
                "coverage": {
                    "selectedChapterCount": 0,
                    "readChapterIds": [],
                    "sampledChapterIds": [],
                    "limitations": ["未覆盖原作章节。"],
                },
                "plotEvents": [{
                    "order": 1,
                    "event": "异常广播出现",
                    "consequence": "林岚开始调查",
                }],
                "evidence": [{
                    "sourceType": "chapter",
                    "sourceId": "chapter-source",
                    "claim": "异常广播触发主线",
                }],
            },
        },
        content_text="# 缩小范围的分析",
        derived_from_ids=[created["initialDocument"]["id"]],
        source_run_id=run_id,
    )

    with pytest.raises(AppError, match="项目锁定范围"):
        await screenplay_crud.accept_document(source_db, document["id"])


@pytest.mark.asyncio
async def test_source_analysis_discloses_partial_reading_limitations(source_db):
    created = await _seed_source_project(source_db)
    request = _request(created["project"]["id"])
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    _, result = await _invoke(
        catalog,
        state,
        "proposeSourceAnalysis",
        {
            "analysis": {
                "rangeSummary": "范围共两章。",
                "narrativeSummary": "当前只精读第一章。",
                "coverage": {
                    "selectedChapterCount": 2,
                    "readChapterIds": ["chapter-source"],
                    "sampledChapterIds": [],
                    "limitations": [],
                },
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
            "contentText": "# 不完整分析",
        },
    )

    assert result.error_code == "tool_execution_failed"
    assert "disclose at least one limitation" in result.content


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
        "proposeSourceAnalysis",
        "proposeCreativeBrief",
    }.issubset(catalog.enabled_names(request))

    scope_error, result = await _invoke(
        catalog,
        state,
        "proposeCreativeBrief",
        {
            "title": "雾港电影创作简报",
            "brief": _book_adaptation_brief(),
            "contentText": "# 创作简报\n\n以异常广播作为叙事线索。",
        },
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
    assert (
        effect.payload["contentJson"]["brief"]["formatPlan"]["targetFormat"]
        == "电影"
    )
    assert (
        effect.payload["contentJson"]["brief"]["adaptationDecisions"][0]["id"]
        == "decision-radio-inciting"
    )
    documents = await screenplay_crud.list_documents(source_db, project_id)
    assert len(documents) == 2


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

    _, result = await _invoke(
        catalog,
        state,
        "proposeCreativeBrief",
        {
            "brief": brief,
            "contentText": "# 无效创作简报",
        },
    )

    assert result.error_code == "tool_execution_failed"
    assert "不存在的证据" in result.content


@pytest.mark.asyncio
async def test_accepting_book_brief_revalidates_adaptation_contract(source_db):
    created = await _seed_source_project(source_db)
    analysis = await _accept_source_analysis(source_db, created)
    document = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="creative_brief",
        title="绕过 Agent 的不完整简报",
        content_json={
            "sourceAnalysisId": analysis["id"],
            "brief": {
                "logline": "维修员追查异常广播。",
                "coreConflict": "真相与执念之间的选择。",
            },
        },
        content_text="# 不完整简报",
        derived_from_ids=[analysis["id"]],
    )

    with pytest.raises(AppError, match="结构化成片规模"):
        await screenplay_crud.accept_document(source_db, document["id"])


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

    scope_error, result = await _invoke(
        catalog,
        state,
        "proposeBeatSheet",
        {
            "beats": [{
                "id": "beat-inciting-radio",
                "order": 1,
                "label": "开场",
                "summary": "雾中广播响起。",
            }],
            "decisionCoverage": [{
                "decisionId": "decision-radio-inciting",
                "structureUnitIds": ["beat-inciting-radio"],
                "implementation": "以广播作为第一幕激励事件。",
            }],
            "contentText": "# 节拍表",
        },
    )

    assert scope_error is None
    assert result.error_code == "tool_execution_failed"
    assert "accepted creative brief" in result.content


@pytest.mark.asyncio
async def test_structure_tool_enforces_project_format_and_parent(source_db):
    created = await _seed_source_project(source_db)
    project_id = created["project"]["id"]
    brief = await _advance_book_to_structure(source_db, created)
    initial_id = brief["id"]
    request = _request(project_id, active_stage="structure")
    state = ScreenplayExecutionStateFactory().create(request)
    catalog = build_screenplay_tool_catalog(source_db)

    episode_error, episode_result = await _invoke(
        catalog,
        state,
        "proposeEpisodeOutline",
        {
            "episodes": [{
                "number": 1,
                "title": "雾中来信",
                "summary": "广播第一次响起。",
            }],
            "contentText": "# 分集",
        },
    )
    assert episode_result is None
    assert "Non-series" in episode_error

    scope_error, result = await _invoke(
        catalog,
        state,
        "proposeBeatSheet",
        {
            "beats": [{
                "id": "beat-inciting-radio",
                "order": 1,
                "label": "开场",
                "summary": "雾中广播响起。",
            }],
            "decisionCoverage": [{
                "decisionId": "decision-radio-inciting",
                "structureUnitIds": ["beat-inciting-radio"],
                "implementation": "以广播作为第一幕激励事件。",
            }],
            "contentText": "# 节拍表",
        },
    )
    assert scope_error is None
    assert result.error_code is None
    assert result.effects[0].payload["kind"] == "beat_sheet"
    assert result.effects[0].payload["derivedFromIds"] == [initial_id]
    assert (
        result.effects[0].payload["contentJson"]["creativeBriefId"]
        == initial_id
    )
    assert result.effects[0].payload["contentJson"]["decisionCoverage"] == [{
        "decisionId": "decision-radio-inciting",
        "structureUnitIds": ["beat-inciting-radio"],
        "implementation": "以广播作为第一幕激励事件。",
    }]


@pytest.mark.asyncio
async def test_structure_acceptance_revalidates_decision_coverage(source_db):
    created = await _seed_source_project(source_db)
    brief = await _advance_book_to_structure(source_db, created)
    document = await screenplay_crud.create_document(
        source_db,
        project_id=created["project"]["id"],
        kind="beat_sheet",
        title="遗漏改编决策的节拍表",
        content_json={
            "creativeBriefId": brief["id"],
            "beats": [{
                "id": "beat-1",
                "order": 1,
                "label": "开场",
                "summary": "异常广播出现。",
            }],
            "decisionCoverage": [],
        },
        content_text="# 节拍表",
        derived_from_ids=[brief["id"]],
    )

    with pytest.raises(AppError, match="全部改编决策"):
        await screenplay_crud.accept_document(source_db, document["id"])


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

    _, short_result = await _invoke(
        catalog,
        state,
        "proposeEpisodeOutline",
        {
            "episodes": [{
                "id": "episode-1",
                "number": 1,
                "title": "雾中来信",
                "summary": "林岚第一次听见哥哥的声音。",
            }],
            "decisionCoverage": coverage,
            "contentText": "# 分集结构",
        },
    )
    assert short_result.error_code == "tool_execution_failed"
    assert "集数" in short_result.content

    _, result = await _invoke(
        catalog,
        state,
        "proposeEpisodeOutline",
        {
            "episodes": [
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
            "decisionCoverage": coverage,
            "contentText": "# 分集结构",
        },
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
    _, invalid_scene_result = await _invoke(
        catalog,
        scene_state,
        "proposeSceneList",
        {
            "scenes": invalid_scenes,
            "contentText": "# 场景表",
        },
    )
    assert invalid_scene_result.error_code == "tool_execution_failed"
    assert "episodeNumber" in invalid_scene_result.content

    _, scene_result = await _invoke(
        catalog,
        scene_state,
        "proposeSceneList",
        {
            "scenes": scenes,
            "contentText": "# 场景表",
        },
    )
    assert scene_result.error_code is None
    scene_proposal = thaw_json_mapping(scene_result.effects[0].payload)
    assert scene_proposal["contentJson"]["structureId"] == structure["id"]


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
    assert "proposeSceneList" in scene_enabled
    assert "proposeSceneDraft" not in scene_enabled
    scope_error, scene_result = await _invoke(
        catalog,
        scene_state,
        "proposeSceneList",
        {
            "scenes": [
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
            "contentText": "# 场景表\n\n1. 电台\n2. 雾港",
        },
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
    assert "proposeSceneList" not in draft_enabled
    _, first_result = await _invoke(
        catalog,
        first_state,
        "proposeSceneDraft",
        {
            "sceneId": "scene-1",
            "sceneHeading": "内景·电台·夜",
            "execution": _scene_execution_input(1),
            "completedSceneIds": ["scene-1"],
            "isComplete": False,
            "contentText": "INT. 电台 - 夜\n\n林岚调试设备。",
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
            "sceneId": "scene-2",
            "sceneHeading": "外景·雾港·夜",
            "execution": _scene_execution_input(2),
            "completedSceneIds": ["scene-1", "scene-2"],
            "isComplete": True,
            "contentText": (
                "INT. 电台 - 夜\n\n林岚调试设备。\n\n"
                "EXT. 雾港 - 夜\n\n她走进浓雾。"
            ),
        },
    )
    assert second_result.error_code is None
    second_proposal = thaw_json_mapping(second_result.effects[0].payload)
    assert set(second_proposal["derivedFromIds"]) == {
        scene_list["id"],
        first_draft["id"],
    }
    assert [
        item["sceneId"]
        for item in second_proposal["contentJson"]["sceneExecutions"]
    ] == ["scene-1", "scene-2"]
    tampered_content = json.loads(json.dumps(
        second_proposal["contentJson"],
        ensure_ascii=False,
    ))
    tampered_content["sceneExecutions"][0]["turnResult"] = "改写上一场转折"
    tampered = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_draft",
        title="篡改历史执行记录的整稿",
        content_json=tampered_content,
        content_text=str(second_proposal["contentText"]),
        derived_from_ids=list(second_proposal["derivedFromIds"]),
    )
    with pytest.raises(AppError, match="不能改写"):
        await screenplay_crud.accept_document(source_db, tampered["id"])

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
async def test_scene_list_acceptance_revalidates_structure_mapping(source_db):
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
    content = _book_scene_list(structure["id"])
    content["scenes"][0]["structureUnitIds"] = ["missing-beat"]
    scene_list = await screenplay_crud.create_document(
        source_db,
        project_id=project_id,
        kind="scene_list",
        title="错误映射的场景表",
        content_json=content,
        content_text="场景表",
        derived_from_ids=[structure["id"]],
    )

    with pytest.raises(AppError, match="不存在的结构单元"):
        await screenplay_crud.accept_document(source_db, scene_list["id"])


@pytest.mark.asyncio
async def test_rolling_draft_rejects_skipped_or_dropped_scenes(source_db):
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
            "sceneId": "scene-2",
            "sceneHeading": "第二场",
            "execution": _scene_execution_input(2),
            "completedSceneIds": ["scene-2"],
            "isComplete": False,
            "contentText": "跳过第一场",
        },
    )
    assert result.error_code == "tool_execution_failed"
    assert "scene-list order" in result.content


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
            **_book_draft_content(scene_list["id"], ["scene-1"]),
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
        "proposeScreenplayReview",
        "proposeScreenplayRevision",
    }.issubset(enabled)

    _, blocked_revision = await _invoke(
        catalog,
        state,
        "proposeScreenplayRevision",
        {
            "issueResolutions": [{
                "issueId": "issue-1",
                "status": "resolved",
                "resolutionEvidence": "对白已改写。",
            }],
            "executionUpdates": [{
                "sceneId": "scene-1",
                **_scene_execution_input(1),
            }],
            "revisionSummary": "调整对白。",
            "contentText": "修订稿",
        },
    )
    assert blocked_revision.error_code == "tool_execution_failed"
    assert "accepted screenplay review" in blocked_revision.content

    _, review_result = await _invoke(
        catalog,
        state,
        "proposeScreenplayReview",
        {
            "summary": "整体结构清晰，对白仍可收紧。",
            "strengths": ["广播意象统一"],
            "issues": [{
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
            "verdict": "revise",
            "contentText": "# 审阅报告\n\n需要收紧对白。",
        },
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

    _, revision_result = await _invoke(
        catalog,
        state,
        "proposeScreenplayRevision",
        {
            "issueResolutions": [{
                "issueId": "issue-1",
                "status": "resolved",
                "resolutionEvidence": "林岚用“又是你”确认识别结果，不再口述背景。",
            }],
            "executionUpdates": [{
                "sceneId": "scene-1",
                "objectiveResult": "林岚确认异常广播来自熟悉的人。",
                "conflictResult": "短句暴露她认识声音来源，却隐藏具体关系。",
                "turnResult": "她从追踪信号转为直接回应声音。",
                "continuityState": "林岚进入主动对话，身份悬念留待下一场。",
                "unresolvedNotes": ["声音来源的真实身份仍未揭示。"],
            }],
            "revisionSummary": "将说明性对白改为带潜台词的短句。",
            "contentText": "INT. 电台 - 夜\n\n@林岚\n又是你。",
        },
    )
    assert revision_result.error_code is None
    revision = thaw_json_mapping(revision_result.effects[0].payload)
    assert revision["kind"] == "scene_draft"
    assert revision["contentJson"]["isComplete"] is True
    assert revision["contentJson"]["resolvedIssueIds"] == ["issue-1"]
    assert revision["contentJson"]["partiallyResolvedIssueIds"] == []
    assert revision["contentJson"]["reassessedSceneIds"] == ["scene-1"]
    assert (
        revision["contentJson"]["sceneExecutions"][0]["turnResult"]
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

    _, rereview_result = await _invoke(
        catalog,
        state,
        "proposeScreenplayReview",
        {
            "summary": "说明性对白问题已经修复。",
            "strengths": ["潜台词更集中"],
            "issues": [],
            "verificationResults": [{
                "issueId": "issue-1",
                "status": "verified",
                "verificationEvidence": "“又是你”以潜台词替代了背景解释。",
            }],
            "verdict": "ready",
            "contentText": "# 复审报告\n\n上一轮问题已验证解决。",
        },
    )

    assert rereview_result.error_code is None
    rereview = thaw_json_mapping(rereview_result.effects[0].payload)
    assert (
        rereview["contentJson"]["verificationOfReviewId"]
        == review["id"]
    )
    assert rereview["contentJson"]["verifiedIssueIds"] == ["issue-1"]
    assert rereview["contentJson"]["failedVerificationIssueIds"] == []


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
    assert result.error_code == "tool_execution_failed"
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
    assert outside_passage.error_code == "tool_execution_failed"
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
    assert global_search.error_code == "tool_execution_failed"
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
