"""Representative end-to-end acceptance scenarios for the screenplay workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud import screenplay as screenplay_crud
from database.crud.screenplay_source_refs import record_source_refs
from domains.screenplay.review_trace import normalize_review_verifications
from exceptions import AppError
from tests.test_screenplay_tools import (
    _book_adaptation_brief,
    _book_beat_structure,
    _book_draft_content,
    _book_scene_list,
)


@pytest_asyncio.fixture
async def acceptance_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _create_and_accept(
    db: DatabaseConnection,
    *,
    project_id: str,
    kind: str,
    title: str,
    content_json: dict,
    content_text: str,
    derived_from_ids: list[str],
    source_run_id: str | None = None,
) -> dict:
    document = await screenplay_crud.create_document(
        db,
        project_id=project_id,
        kind=kind,
        title=title,
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
        source_run_id=source_run_id,
    )
    await screenplay_crud.accept_document(db, document["id"])
    accepted = await screenplay_crud.get_document(db, document["id"])
    assert accepted is not None
    return accepted


def _film_structure(brief_id: str) -> dict:
    return {
        "creativeBriefId": brief_id,
        "beats": [{
            "id": "beat-choice",
            "order": 1,
            "label": "必须选择",
            "summary": "主角被迫在离开与留下之间作出选择。",
        }],
        "decisionCoverage": [],
    }


def _film_scene_list(structure_id: str) -> dict:
    return {
        "structureId": structure_id,
        "scenes": [{
            "id": "scene-1",
            "order": 1,
            "heading": "内景·候车室·夜·1",
            "structureUnitIds": ["beat-choice"],
            "objective": "拿到离开的车票",
            "conflict": "最后一班车即将关闭",
            "turn": "主角发现同行者没有登车",
            "synopsis": "主角在候车室准备离开。",
        }, {
            "id": "scene-2",
            "order": 2,
            "heading": "外景·站台·夜·2",
            "structureUnitIds": ["beat-choice"],
            "objective": "决定是否独自离开",
            "conflict": "列车已经启动",
            "turn": "主角跳下列车选择留下",
            "synopsis": "主角在最后一刻作出选择。",
        }],
    }


def _execution(scene_id: str, unit_id: str, detail: str) -> dict:
    return {
        "sceneId": scene_id,
        "structureUnitIds": [unit_id],
        "objectiveResult": f"{detail}，场景目标得到明确结果。",
        "conflictResult": f"{detail}，阻力迫使人物采取行动。",
        "turnResult": f"{detail}，场尾状态发生改变。",
        "continuityState": f"{detail}，新行动方向进入下一场。",
        "unresolvedNotes": [],
    }


@pytest.mark.asyncio
async def test_scene_draft_batch_is_applied_atomically(
    acceptance_db: DatabaseConnection,
):
    created = await screenplay_crud.create_project(
        acceptance_db,
        title="批量正文应用",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先推情节",
        premise="一次创作并应用连续场景。",
    )
    project_id = created["project"]["id"]
    brief = await screenplay_crud.accept_document(
        acceptance_db,
        created["initialDocument"]["id"],
    )
    structure = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="beat_sheet",
        title="批量正文节拍表",
        content_json=_film_structure(brief["id"]),
        content_text="主角必须作出选择。",
        derived_from_ids=[brief["id"]],
    )
    scene_list = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_list",
        title="批量正文场景表",
        content_json=_film_scene_list(structure["id"]),
        content_text="候车室与站台两场。",
        derived_from_ids=[structure["id"]],
    )
    batch = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="一次完成两场",
        content_json={
            "generatedBy": "screenplay-agent-long-task",
            "sceneListId": scene_list["id"],
            "sceneId": "scene-1",
            "newSceneIds": ["scene-1", "scene-2"],
            "completedSceneIds": ["scene-1", "scene-2"],
            "sceneExecutions": [
                _execution("scene-1", "beat-choice", "拿到车票"),
                _execution("scene-2", "beat-choice", "跳下列车"),
            ],
            "isComplete": True,
        },
        content_text=(
            "INT. 候车室 - 夜\n\n最后一张车票。\n\n"
            "EXT. 站台 - 夜\n\n他跳下列车。"
        ),
        derived_from_ids=[scene_list["id"]],
    )

    assert batch["status"] == "accepted"
    project = await screenplay_crud.get_project(acceptance_db, project_id)
    assert project["active_stage"] == "review"


@pytest.mark.asyncio
async def test_scene_draft_acceptance_repairs_legacy_interleaved_scene_order(
    acceptance_db: DatabaseConnection,
):
    created = await screenplay_crud.create_project(
        acceptance_db,
        title="历史场景顺序修复",
        source_kind="original",
        source_book_id=None,
        screenplay_format="连续剧",
        approach="先推情节",
        premise="验证旧场景表中的迟到场景仍能按分集接受正文。",
    )
    project_id = created["project"]["id"]
    await screenplay_crud.update_document(
        acceptance_db,
        created["initialDocument"]["id"],
        {
            "contentJson": {
                "brief": {
                    "formatPlan": {
                        "targetFormat": "连续剧",
                        "episodeCount": 2,
                        "episodeDurationMinutes": 10,
                    },
                },
            },
            "contentText": "两集连续剧创作简报。",
        },
    )
    brief = await screenplay_crud.accept_document(
        acceptance_db,
        created["initialDocument"]["id"],
    )
    structure = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="episode_outline",
        title="两集结构",
        content_json={
            "creativeBriefId": brief["id"],
            "episodes": [{
                "id": "episode-1",
                "number": 1,
                "title": "进入",
                "summary": "主角进入异常空间。",
            }, {
                "id": "episode-2",
                "number": 2,
                "title": "追踪",
                "summary": "主角开始追踪真相。",
            }],
            "decisionCoverage": [],
        },
        content_text="第一集进入，第二集追踪。",
        derived_from_ids=[brief["id"]],
    )
    ordered_scenes = [{
        "id": "scene-1",
        "order": 1,
        "episodeNumber": 1,
        "heading": "外景·入口·夜",
        "structureUnitIds": ["episode-1"],
        "objective": "找到入口",
        "conflict": "入口正在关闭",
        "turn": "主角挤入缝隙",
        "synopsis": "主角进入异常空间。",
    }, {
        "id": "scene-1-late",
        "order": 2,
        "episodeNumber": 1,
        "heading": "内景·异常空间·夜",
        "structureUnitIds": ["episode-1"],
        "objective": "确认身处何处",
        "conflict": "出口已经消失",
        "turn": "掌心印记亮起",
        "synopsis": "第一集迟到的补充场景。",
    }, {
        "id": "scene-2",
        "order": 3,
        "episodeNumber": 2,
        "heading": "外景·街道·晨",
        "structureUnitIds": ["episode-2"],
        "objective": "追踪线索",
        "conflict": "线索突然中断",
        "turn": "陌生人主动来电",
        "synopsis": "第二集开始追踪。",
    }]
    scene_list = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_list",
        title="两集场景表",
        content_json={
            "structureId": structure["id"],
            "scenes": ordered_scenes,
        },
        content_text="两集三个场景。",
        derived_from_ids=[structure["id"]],
    )

    # Simulate an accepted pre-fix document: the provider omitted an episode-1
    # scene and appended it only after an episode-2 scene in its final batch.
    legacy_scenes = [
        {**ordered_scenes[0], "order": 1},
        {**ordered_scenes[2], "order": 2},
        {**ordered_scenes[1], "order": 3},
    ]
    await acceptance_db.execute(
        "UPDATE screenplay_documents SET content_json = ? WHERE id = ?",
        [
            json.dumps(
                {
                    "structureId": structure["id"],
                    "scenes": legacy_scenes,
                },
                ensure_ascii=False,
            ),
            scene_list["id"],
        ],
    )

    draft = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="第一集批量正文",
        content_json={
            "generatedBy": "screenplay-agent-long-task",
            "sceneListId": scene_list["id"],
            "sceneId": "scene-1",
            "newSceneIds": ["scene-1", "scene-1-late"],
            "completedSceneIds": ["scene-1", "scene-1-late"],
            "sceneExecutions": [
                _execution("scene-1", "episode-1", "进入异常空间"),
                _execution("scene-1-late", "episode-1", "发现掌心印记"),
            ],
            "isComplete": False,
        },
        content_text=(
            "EXT. 入口 - 夜\n\n主角挤入缝隙。\n\n"
            "INT. 异常空间 - 夜\n\n掌心印记亮起。"
        ),
        derived_from_ids=[scene_list["id"]],
    )

    assert draft["status"] == "accepted"
    project = await screenplay_crud.get_project(acceptance_db, project_id)
    assert project["active_stage"] == "draft"


@pytest.mark.asyncio
async def test_original_film_completes_and_failed_review_rolls_back(
    acceptance_db: DatabaseConnection,
):
    created = await screenplay_crud.create_project(
        acceptance_db,
        title="最后一班车",
        source_kind="original",
        source_book_id=None,
        screenplay_format="电影",
        approach="先找人物",
        premise="一个人必须决定离开还是留下。",
    )
    project_id = created["project"]["id"]
    brief = await screenplay_crud.accept_document(
        acceptance_db,
        created["initialDocument"]["id"],
    )
    structure = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="beat_sheet",
        title="电影节拍表",
        content_json=_film_structure(brief["id"]),
        content_text="主角在最后一班车前作出选择。",
        derived_from_ids=[brief["id"]],
    )
    scene_list = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_list",
        title="电影场景表",
        content_json=_film_scene_list(structure["id"]),
        content_text="候车室与站台两场。",
        derived_from_ids=[structure["id"]],
    )
    first_execution = _execution("scene-1", "beat-choice", "拿到车票")
    partial = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="第一场滚动整稿",
        content_json={
            "sceneListId": scene_list["id"],
            "sceneId": "scene-1",
            "sceneHeading": "内景·候车室·夜·1",
            "completedSceneIds": ["scene-1"],
            "sceneExecutions": [first_execution],
            "isComplete": False,
        },
        content_text="INT. 候车室 - 夜\n\n最后一张车票。",
        derived_from_ids=[scene_list["id"]],
    )
    final_draft = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="电影完整剧本",
        content_json={
            "sceneListId": scene_list["id"],
            "sceneId": "scene-2",
            "sceneHeading": "外景·站台·夜·2",
            "completedSceneIds": ["scene-1", "scene-2"],
            "sceneExecutions": [
                first_execution,
                _execution("scene-2", "beat-choice", "跳下列车"),
            ],
            "isComplete": True,
        },
        content_text=(
            "INT. 候车室 - 夜\n\n最后一张车票。\n\n"
            "EXT. 站台 - 夜\n\n他跳下列车。"
        ),
        derived_from_ids=[scene_list["id"], partial["id"]],
    )

    invalid_review = await screenplay_crud.create_document(
        acceptance_db,
        project_id=project_id,
        kind="review",
        title="错误的通过结论",
        content_json={
            "reviewedDraftId": final_draft["id"],
            "verdict": "ready",
            "issues": [{
                "id": "issue-open",
                "severity": "major",
                "category": "structure",
                "sceneIds": ["scene-2"],
                "executionFields": ["turnResult"],
                "problem": "选择尚未成立。",
                "recommendation": "强化人物付出的代价。",
                "acceptanceCriteria": "跳车行为产生不可逆后果。",
            }],
        },
        content_text="仍有问题却错误地判定通过。",
        derived_from_ids=[final_draft["id"]],
    )
    with pytest.raises(AppError, match="ready"):
        await screenplay_crud.accept_document(
            acceptance_db,
            invalid_review["id"],
        )
    after_failure = await screenplay_crud.get_project(
        acceptance_db,
        project_id,
    )
    assert after_failure["active_stage"] == "review"
    assert after_failure["delivery_manifest"] is None
    assert (
        await screenplay_crud.get_document(
            acceptance_db,
            invalid_review["id"],
        )
    )["status"] == "draft"

    final_review = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="review",
        title="最终审阅",
        content_json={
            "reviewedDraftId": final_draft["id"],
            "verdict": "ready",
            "issues": [],
        },
        content_text="结构、人物行动和连续性均可交付。",
        derived_from_ids=[final_draft["id"]],
    )
    completed = await screenplay_crud.get_project(
        acceptance_db,
        project_id,
    )
    manifest = completed["delivery_manifest"]
    assert completed["active_stage"] == "completed"
    assert manifest["lineage"]["finalReviewId"] == final_review["id"]
    assert manifest["qualityGate"]["sceneCount"] == 2
    assert len(manifest["documents"]) == 5


@pytest.mark.asyncio
async def test_first_chapter_adaptation_keeps_scope_in_delivery(
    acceptance_db: DatabaseConnection,
):
    await acceptance_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-source", "雾港来信"],
    )
    await acceptance_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-source", "写作目录", "book-source"],
    )
    for chapter_id, title, order, text in (
        ("chapter-source", "第一章 雾中广播", 1, "林岚听见哥哥的声音。"),
        ("chapter-outside", "第二章 广播真相", 2, "广播来自未来。"),
    ):
        await acceptance_db.execute(
            "INSERT INTO outline_chapters "
            "(id, outline_id, title, sort) VALUES (?, ?, ?, ?)",
            [chapter_id, "writing-source", title, order],
        )
        await acceptance_db.execute(
            "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
            [chapter_id, json.dumps({
                "root": {
                    "children": [{
                        "type": "paragraph",
                        "children": [{"type": "text", "text": text}],
                    }],
                },
            }, ensure_ascii=False)],
        )
    created = await screenplay_crud.create_project(
        acceptance_db,
        title="雾中广播前章改编",
        source_kind="book",
        source_book_id="book-source",
        screenplay_format="电影",
        approach="结构重组",
        premise="只改编第一章的悬疑启动。",
        source_scope={"mode": "first_chapters", "count": 1},
    )
    project_id = created["project"]["id"]
    run_id = "acceptance-source-run"
    await record_source_refs(
        acceptance_db,
        project_id=project_id,
        agent_run_id=run_id,
        tool_name="readSourcePassages",
        refs=[{
            "sourceType": "chapter",
            "sourceId": "chapter-source",
            "sourceRevision": "chapter-source-revision",
            "excerpt": "林岚听见哥哥的声音。",
        }],
    )
    analysis = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="source_analysis",
        title="第一章范围分析",
        content_json={
            "analysis": {
                "narrativeSummary": "林岚被异常广播引入调查。",
                "coverage": {
                    "selectedChapterCount": 1,
                    "readChapterIds": ["chapter-source"],
                    "sampledChapterIds": [],
                    "limitations": [],
                },
                "plotEvents": [{
                    "order": 1,
                    "event": "听见失踪哥哥的声音",
                    "consequence": "林岚开始追查广播",
                }],
                "evidence": [{
                    "sourceType": "chapter",
                    "sourceId": "chapter-source",
                    "claim": "异常广播触发主线",
                }],
            },
        },
        content_text="只分析第一章，不使用第二章真相。",
        derived_from_ids=[created["initialDocument"]["id"]],
        source_run_id=run_id,
    )
    brief = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="creative_brief",
        title="限定范围创作简报",
        content_json={
            "sourceAnalysisId": analysis["id"],
            "brief": _book_adaptation_brief(),
        },
        content_text="保留广播钩子，不提前揭示后续真相。",
        derived_from_ids=[analysis["id"]],
    )
    structure = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="beat_sheet",
        title="限定范围节拍表",
        content_json=_book_beat_structure(brief["id"]),
        content_text="广播作为激励事件。",
        derived_from_ids=[brief["id"]],
    )
    scene_list = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_list",
        title="限定范围场景表",
        content_json=_book_scene_list(structure["id"]),
        content_text="电台追踪场景。",
        derived_from_ids=[structure["id"]],
    )
    draft_content = _book_draft_content(scene_list["id"], ["scene-1"])
    draft_content["isComplete"] = True
    final_draft = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="限定范围完整剧本",
        content_json=draft_content,
        content_text="INT. 电台 - 夜\n\n@林岚\n你到底是谁？",
        derived_from_ids=[scene_list["id"]],
    )
    await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="review",
        title="限定范围最终审阅",
        content_json={
            "reviewedDraftId": final_draft["id"],
            "verdict": "ready",
            "issues": [],
        },
        content_text="没有泄露范围外的广播真相。",
        derived_from_ids=[final_draft["id"]],
    )

    completed = await screenplay_crud.get_project(
        acceptance_db,
        project_id,
    )
    manifest = completed["delivery_manifest"]
    assert manifest["projectSnapshot"]["sourceScope"]["chapterIds"] == [
        "chapter-source"
    ]
    assert "chapter-outside" not in json.dumps(
        manifest,
        ensure_ascii=False,
    )
    assert manifest["lineage"]["sourceAnalysisId"] == analysis["id"]
    assert len(manifest["documents"]) == 6
    assert manifest["sourceTrace"]["totalSourceRefCount"] >= 1


@pytest.mark.asyncio
async def test_series_revision_rereview_completes_with_episode_lineage(
    acceptance_db: DatabaseConnection,
):
    created = await screenplay_crud.create_project(
        acceptance_db,
        title="两夜之间",
        source_kind="original",
        source_book_id=None,
        screenplay_format="连续剧",
        approach="先推情节",
        premise="两集内完成一次追捕与反转。",
    )
    project_id = created["project"]["id"]
    await screenplay_crud.update_document(
        acceptance_db,
        created["initialDocument"]["id"],
        {
            "contentJson": {
                "brief": {
                    "formatPlan": {
                        "targetFormat": "连续剧",
                        "episodeCount": 2,
                        "episodeDurationMinutes": 45,
                    },
                },
            },
            "contentText": "两集连续剧创作简报。",
        },
    )
    brief = await screenplay_crud.accept_document(
        acceptance_db,
        created["initialDocument"]["id"],
    )
    structure = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="episode_outline",
        title="两集分集结构",
        content_json={
            "creativeBriefId": brief["id"],
            "episodes": [{
                "id": "episode-1",
                "number": 1,
                "title": "追踪",
                "summary": "主角追踪失踪目标。",
            }, {
                "id": "episode-2",
                "number": 2,
                "title": "反转",
                "summary": "失踪目标主动现身并反转关系。",
            }],
            "decisionCoverage": [],
        },
        content_text="第一集追踪，第二集反转。",
        derived_from_ids=[brief["id"]],
    )
    scene_list = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_list",
        title="两集场景表",
        content_json={
            "structureId": structure["id"],
            "scenes": [{
                "id": "scene-1",
                "order": 1,
                "episodeNumber": 1,
                "heading": "外景·夜路·夜·1",
                "structureUnitIds": ["episode-1"],
                "objective": "追上目标",
                "conflict": "目标进入封锁区",
                "turn": "主角收到目标的主动来电",
                "synopsis": "第一集以主动来电结束。",
            }, {
                "id": "scene-2",
                "order": 2,
                "episodeNumber": 2,
                "heading": "内景·仓库·夜·2",
                "structureUnitIds": ["episode-2"],
                "objective": "确认目标身份",
                "conflict": "目标拒绝解释",
                "turn": "主角发现自己才是被追踪者",
                "synopsis": "第二集完成关系反转。",
            }],
        },
        content_text="每集一个核心场景。",
        derived_from_ids=[structure["id"]],
    )
    first_execution = _execution("scene-1", "episode-1", "收到主动来电")
    partial = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="第一集滚动整稿",
        content_json={
            "sceneListId": scene_list["id"],
            "sceneId": "scene-1",
            "sceneHeading": "外景·夜路·夜·1",
            "completedSceneIds": ["scene-1"],
            "sceneExecutions": [first_execution],
            "isComplete": False,
        },
        content_text="EXT. 夜路 - 夜\n\n电话响了。",
        derived_from_ids=[scene_list["id"]],
    )
    second_execution = _execution("scene-2", "episode-2", "身份反转")
    complete_draft = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="两集完整剧本",
        content_json={
            "sceneListId": scene_list["id"],
            "sceneId": "scene-2",
            "sceneHeading": "内景·仓库·夜·2",
            "completedSceneIds": ["scene-1", "scene-2"],
            "sceneExecutions": [first_execution, second_execution],
            "isComplete": True,
        },
        content_text=(
            "EXT. 夜路 - 夜\n\n电话响了。\n\n"
            "INT. 仓库 - 夜\n\n照片上是主角自己。"
        ),
        derived_from_ids=[scene_list["id"], partial["id"]],
    )
    issue = {
        "id": "issue-turn-cost",
        "severity": "major",
        "category": "structure",
        "sceneIds": ["scene-2"],
        "executionFields": ["turnResult", "continuityState"],
        "problem": "身份反转没有产生后续代价。",
        "recommendation": "让主角失去安全身份。",
        "acceptanceCriteria": "场尾明确主角成为下一轮追捕目标。",
    }
    review = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="review",
        title="第一轮审阅",
        content_json={
            "reviewedDraftId": complete_draft["id"],
            "verdict": "revise",
            "issues": [issue],
        },
        content_text="反转成立，但代价不足。",
        derived_from_ids=[complete_draft["id"]],
    )
    revised_second_execution = {
        **second_execution,
        "turnResult": "身份曝光后，主角被正式列为追捕目标。",
        "continuityState": "主角失去安全身份并开始逃亡。",
    }
    issue_resolution = {
        "issueId": "issue-turn-cost",
        "status": "resolved",
        "sceneIds": ["scene-2"],
        "executionFields": ["turnResult", "continuityState"],
        "resolutionEvidence": "新增通缉广播，明确主角成为追捕目标。",
    }
    revision = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="scene_draft",
        title="两集修订完整稿",
        content_json={
            "sceneListId": scene_list["id"],
            "completedSceneIds": ["scene-1", "scene-2"],
            "sceneExecutions": [
                first_execution,
                revised_second_execution,
            ],
            "isComplete": True,
            "revisionOf": complete_draft["id"],
            "reviewId": review["id"],
            "issueResolutions": [issue_resolution],
            "reassessedSceneIds": ["scene-2"],
            "resolvedIssueIds": ["issue-turn-cost"],
            "partiallyResolvedIssueIds": [],
        },
        content_text=(
            "EXT. 夜路 - 夜\n\n电话响了。\n\n"
            "INT. 仓库 - 夜\n\n广播开始通缉主角。"
        ),
        derived_from_ids=[complete_draft["id"], review["id"]],
    )
    verification_results = normalize_review_verifications(
        previous_review_issues=[issue],
        issue_resolutions=[issue_resolution],
        verification_results=[{
            "issueId": "issue-turn-cost",
            "status": "verified",
            "verificationEvidence": "通缉广播兑现了身份反转的后续代价。",
        }],
    )
    final_review = await _create_and_accept(
        acceptance_db,
        project_id=project_id,
        kind="review",
        title="最终复审",
        content_json={
            "reviewedDraftId": revision["id"],
            "verdict": "ready",
            "issues": [],
            "verificationOfReviewId": review["id"],
            "verificationResults": verification_results,
            "verifiedIssueIds": ["issue-turn-cost"],
            "failedVerificationIssueIds": [],
        },
        content_text="上一轮问题已经验证解决。",
        derived_from_ids=[revision["id"]],
    )

    completed = await screenplay_crud.get_project(
        acceptance_db,
        project_id,
    )
    manifest = completed["delivery_manifest"]
    structure_entry = next(
        item for item in manifest["documents"]
        if item["role"] == "structure"
    )
    assert completed["active_stage"] == "completed"
    assert structure_entry["kind"] == "episode_outline"
    assert manifest["lineage"]["finalDraftId"] == revision["id"]
    assert manifest["lineage"]["finalReviewId"] == final_review["id"]
    assert manifest["qualityGate"]["verifiedPriorIssueCount"] == 1
    assert manifest["qualityGate"]["sceneCount"] == 2
