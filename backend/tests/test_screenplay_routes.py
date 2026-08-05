from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.screenplay_source_refs import record_source_refs
from dependencies import clear_db, set_db
from exceptions import AppError, NotFoundError
from routers.books import delete_book
from routers.screenplay import (
    accept_screenplay_document,
    create_screenplay_agent_session,
    create_screenplay_document,
    create_screenplay_project,
    delete_screenplay_project,
    export_screenplay_pdf,
    get_screenplay_project,
    get_or_create_screenplay_agent_session,
    list_screenplay_documents,
    list_screenplay_agent_sessions,
    list_screenplay_projects,
    list_screenplay_source_refs,
    restore_screenplay_document,
    update_screenplay_document,
    update_screenplay_project,
)
from schemas.screenplay import (
    CreateScreenplayDocumentRequest,
    CreateScreenplayProjectRequest,
    ScreenplaySourceScopeRequest,
    UpdateScreenplayDocumentRequest,
    UpdateScreenplayProjectRequest,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        clear_db(db)
        await db.close()


async def _insert_book(
    db: DatabaseConnection,
    *,
    book_id: str = "book1",
    title: str = "测试书",
) -> str:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        [book_id, title],
    )
    return book_id


async def _insert_chapter(
    db: DatabaseConnection,
    *,
    book_id: str = "book1",
    suffix: str = "1",
) -> str:
    outline_id = f"writing-{suffix}"
    chapter_id = f"chapter-{suffix}"
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, '写作目录', 'writing', ?)",
        [outline_id, book_id],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, sort) VALUES (?, ?, '第一章', 1)",
        [chapter_id, outline_id],
    )
    return chapter_id


async def _create_original_project(title: str = "原创剧本"):
    return await create_screenplay_project(CreateScreenplayProjectRequest(
        title=title,
        sourceKind="original",
        format="单集剧",
        approach="先找人物",
        premise="一个关于选择的故事",
    ))


async def _record_agent_proposal(
    db: DatabaseConnection,
    *,
    project_id: str,
    run_id: str,
    proposal: dict,
) -> None:
    session = (
        await get_or_create_screenplay_agent_session(project_id)
    )["data"]
    await db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, prompt) "
        "VALUES (?, ?, 'done', '生成正式提案')",
        [run_id, session["id"]],
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'screenplay.document_proposal', ?)",
        [run_id, json.dumps(proposal, ensure_ascii=False)],
    )


def _original_beat_structure(brief_id: str) -> dict:
    return {
        "creativeBriefId": brief_id,
        "beats": [{
            "id": "beat-choice",
            "order": 1,
            "label": "关键选择",
            "summary": "主角面对不可回避的选择。",
        }],
        "decisionCoverage": [],
    }


def _original_scene_list(structure_id: str, *, count: int = 1) -> dict:
    return {
        "structureId": structure_id,
        "scenes": [{
            "id": f"scene-{index}",
            "order": index,
            "heading": f"内景·房间·夜·{index}",
            "structureUnitIds": ["beat-choice"],
            "objective": "迫使主角面对选择",
            "conflict": "主角试图逃避代价",
            "turn": "新的事实让逃避失效",
            "synopsis": "主角在房间里作出关键决定。",
        } for index in range(1, count + 1)],
    }


def _original_draft_content(
    scene_list_id: str,
    completed_scene_ids: list[str],
    *,
    is_complete: bool,
) -> dict:
    scene_executions = []
    for scene_id in completed_scene_ids:
        index = int(scene_id.rsplit("-", 1)[-1])
        scene_executions.append({
            "sceneId": scene_id,
            "structureUnitIds": ["beat-choice"],
            "objectiveResult": f"第 {index} 场迫使主角面对选择。",
            "conflictResult": f"第 {index} 场让逃避选择的代价升级。",
            "turnResult": f"第 {index} 场以新事实改变主角行动。",
            "continuityState": f"第 {index} 场结束后选择压力延续。",
            "unresolvedNotes": [],
        })
    last_id = completed_scene_ids[-1]
    last_index = int(last_id.rsplit("-", 1)[-1])
    return {
        "sceneListId": scene_list_id,
        "sceneId": last_id,
        "sceneHeading": f"内景·房间·夜·{last_index}",
        "completedSceneIds": completed_scene_ids,
        "sceneExecutions": scene_executions,
        "isComplete": is_complete,
    }


async def _advance_to_review():
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    brief_id = created["data"]["initialDocument"]["id"]
    await accept_screenplay_document(brief_id)
    structure = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="beat_sheet",
            title="节拍表",
            contentJson=_original_beat_structure(brief_id),
            contentText="开场、转折、结局",
            derivedFromIds=[brief_id],
        ),
    )
    await accept_screenplay_document(structure["data"]["id"])
    scene_list = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_list",
            title="场景表",
            contentJson=_original_scene_list(structure["data"]["id"]),
            contentText="第一场",
            derivedFromIds=[structure["data"]["id"]],
        ),
    )
    await accept_screenplay_document(scene_list["data"]["id"])
    draft = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="完整剧本",
            contentJson=_original_draft_content(
                scene_list["data"]["id"],
                ["scene-1"],
                is_complete=True,
            ),
            contentText="INT. 房间 - 夜\n\n一个选择发生了。",
            derivedFromIds=[scene_list["data"]["id"]],
        ),
    )
    await accept_screenplay_document(draft["data"]["id"])
    return project_id, draft["data"]


async def test_schema_creates_screenplay_tables(temp_db: DatabaseConnection):
    project_table = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_projects'"
    )
    document_table = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_documents'"
    )
    source_refs_table = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_source_refs'"
    )
    document_source_refs_table = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_document_source_refs'"
    )

    assert project_table is not None
    assert document_table is not None
    assert source_refs_table is not None
    assert document_source_refs_table is not None


async def test_create_original_project_atomically_creates_initial_brief(
    temp_db: DatabaseConnection,
):
    response = await _create_original_project()

    assert response["success"] is True
    project = response["data"]["project"]
    document = response["data"]["initialDocument"]
    assert project["source_kind"] == "original"
    assert project["source_book_id"] is None
    assert project["active_stage"] == "orientation"
    assert document["project_id"] == project["id"]
    assert document["kind"] == "creative_brief"
    assert document["version"] == 1
    assert document["status"] == "draft"
    assert document["content_json"]["premise"] == "一个关于选择的故事"
    assert project["source_scope"]["mode"] == "whole_book"


async def test_book_project_resolves_first_volume_to_stable_chapter_snapshot(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title, enable_volume) VALUES (?, ?, 1)",
        ["book-volume", "分卷原作"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-volume", "写作目录", "book-volume"],
    )
    for row in [
        ("volume-1", "writing-volume", "第一卷", 1, None),
        ("chapter-1", "writing-volume", "第一章", 1, "volume-1"),
        ("chapter-2", "writing-volume", "第二章", 2, "volume-1"),
        ("volume-2", "writing-volume", "第二卷", 2, None),
        ("chapter-3", "writing-volume", "第三章", 1, "volume-2"),
    ]:
        await temp_db.execute(
            "INSERT INTO outline_chapters "
            "(id, outline_id, title, sort, parent_id) VALUES (?, ?, ?, ?, ?)",
            list(row),
        )

    response = await create_screenplay_project(
        CreateScreenplayProjectRequest(
            title="第一卷改编",
            sourceKind="book",
            sourceBookId="book-volume",
            sourceScope=ScreenplaySourceScopeRequest(
                mode="first_volumes",
                count=1,
            ),
            format="电影",
            approach="结构重组",
        )
    )

    scope = response["data"]["project"]["source_scope"]
    assert scope["mode"] == "first_volumes"
    assert scope["volumeIds"] == ["volume-1"]
    assert scope["chapterIds"] == ["chapter-1", "chapter-2"]
    assert [item["title"] for item in scope["chapters"]] == ["第一章", "第二章"]
    assert (
        response["data"]["initialDocument"]["content_json"]["sourceScope"]
        == scope
    )


async def test_book_project_rejects_range_outside_source_structure(
    temp_db: DatabaseConnection,
):
    await _insert_book(temp_db)

    with pytest.raises(AppError, match="没有可用于改编的正文章节"):
        await create_screenplay_project(
            CreateScreenplayProjectRequest(
                title="无效范围",
                sourceKind="book",
                sourceBookId="book1",
                sourceScope=ScreenplaySourceScopeRequest(
                    mode="first_chapters",
                    count=1,
                ),
            )
        )


async def test_whole_book_adaptation_requires_source_chapters(
    temp_db: DatabaseConnection,
):
    await _insert_book(temp_db)

    with pytest.raises(AppError, match="没有可用于改编的正文章节"):
        await create_screenplay_project(
            CreateScreenplayProjectRequest(
                title="空作品整本改编",
                sourceKind="book",
                sourceBookId="book1",
                sourceScope=ScreenplaySourceScopeRequest(
                    mode="whole_book",
                ),
            )
        )


async def test_book_adaptation_requires_existing_source(
    temp_db: DatabaseConnection,
):
    with pytest.raises(NotFoundError, match="来源书籍不存在"):
        await create_screenplay_project(CreateScreenplayProjectRequest(
            title="不存在的改编",
            sourceKind="book",
            sourceBookId="missing",
            format="电影",
            approach="忠实改编",
        ))

    await _insert_book(temp_db, title="真实原作")
    await _insert_chapter(temp_db)
    response = await create_screenplay_project(CreateScreenplayProjectRequest(
        title="真实原作改编",
        sourceKind="book",
        sourceBookId="book1",
        format="电影",
        approach="结构重组",
    ))
    assert response["data"]["project"]["source_book_id"] == "book1"
    assert response["data"]["initialDocument"]["content_json"]["sourceBookTitle"] == "真实原作"


async def test_documents_increment_versions_and_accept_supersedes_previous(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial = created["data"]["initialDocument"]

    accepted_initial = await accept_screenplay_document(initial["id"])
    assert accepted_initial["data"]["status"] == "accepted"
    project_after_brief = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_brief["active_stage"] == "structure"

    second = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="creative_brief",
            title="创作简报 v2",
            contentJson={"theme": "选择"},
            contentText="第二版",
            derivedFromIds=[initial["id"]],
        ),
    )
    assert second["data"]["version"] == 2
    assert second["data"]["derived_from_ids"] == [initial["id"]]

    accepted_second = await accept_screenplay_document(second["data"]["id"])
    assert accepted_second["data"]["status"] == "accepted"
    documents = (await list_screenplay_documents(project_id))["data"]
    by_version = {item["version"]: item for item in documents}
    assert by_version[1]["status"] == "superseded"
    assert by_version[2]["status"] == "accepted"
    project_after_revision = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_revision["active_stage"] == "structure"

    structure = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="beat_sheet",
            title="节拍表 v1",
            contentJson=_original_beat_structure(second["data"]["id"]),
            contentText="开场、转折、结局",
            derivedFromIds=[second["data"]["id"]],
        ),
    )
    await accept_screenplay_document(structure["data"]["id"])
    project_after_structure = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_structure["active_stage"] == "scenes"

    scene_list = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_list",
            title="场景表 v1",
            contentJson=_original_scene_list(
                structure["data"]["id"],
                count=2,
            ),
            contentText="第一场、第二场",
            derivedFromIds=[structure["data"]["id"]],
        ),
    )
    await accept_screenplay_document(scene_list["data"]["id"])
    project_after_scenes = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_scenes["active_stage"] == "draft"

    partial_draft = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="场景正文 v1",
            contentJson=_original_draft_content(
                scene_list["data"]["id"],
                ["scene-1"],
                is_complete=False,
            ),
            contentText="第一场正文",
            derivedFromIds=[scene_list["data"]["id"]],
        ),
    )
    await accept_screenplay_document(partial_draft["data"]["id"])
    project_after_partial = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_partial["active_stage"] == "draft"

    complete_draft = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="场景正文 v2",
            contentJson=_original_draft_content(
                scene_list["data"]["id"],
                ["scene-1", "scene-2"],
                is_complete=True,
            ),
            contentText="第一场正文\n第二场正文",
            derivedFromIds=[
                scene_list["data"]["id"],
                partial_draft["data"]["id"],
            ],
        ),
    )
    await accept_screenplay_document(complete_draft["data"]["id"])
    project_after_draft = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project_after_draft["active_stage"] == "review"


async def test_accepted_document_is_immutable(temp_db: DatabaseConnection):
    created = await _create_original_project()
    document_id = created["data"]["initialDocument"]["id"]
    await accept_screenplay_document(document_id)

    with pytest.raises(AppError, match="只有草稿文档可以直接修改"):
        await update_screenplay_document(
            document_id,
            UpdateScreenplayDocumentRequest(contentText="静默覆盖"),
        )


async def test_project_stage_cannot_be_changed_through_generic_update(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    body = UpdateScreenplayProjectRequest.model_validate({
        "activeStage": "review",
        "title": "仍由状态机推进",
    })

    updated = await update_screenplay_project(project_id, body)

    assert updated["data"]["title"] == "仍由状态机推进"
    assert updated["data"]["active_stage"] == "orientation"


async def test_accept_rejects_document_that_skips_workflow_stage(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    draft = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="越级完整稿",
            contentJson={"isComplete": True},
            contentText="不能越级接受",
            derivedFromIds=[initial_id],
        ),
    )

    with pytest.raises(AppError, match="文档类型与当前剧本阶段不匹配"):
        await accept_screenplay_document(draft["data"]["id"])


async def test_review_revision_rereview_closes_into_completed(
    temp_db: DatabaseConnection,
):
    project_id, draft = await _advance_to_review()
    review = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="review",
            title="第一轮审阅",
            contentJson={
                "reviewedDraftId": draft["id"],
                "verdict": "revise",
                "issues": [{
                    "id": "issue-1",
                    "severity": "major",
                    "category": "dialogue",
                    "sceneIds": ["scene-1"],
                    "executionFields": [
                        "conflictResult",
                        "turnResult",
                    ],
                    "problem": "对白过直",
                    "recommendation": "用沉默和动作替代解释。",
                    "acceptanceCriteria": "选择压力通过行动呈现。",
                }],
            },
            contentText="需要收紧对白。",
            derivedFromIds=[draft["id"]],
        ),
    )
    await accept_screenplay_document(review["data"]["id"])

    revision = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="修订稿",
            contentJson={
                "sceneListId": draft["content_json"]["sceneListId"],
                "completedSceneIds": draft["content_json"]["completedSceneIds"],
                "sceneExecutions": [{
                    **draft["content_json"]["sceneExecutions"][0],
                    "conflictResult": "沉默让逃避选择的代价变得可见。",
                    "turnResult": "主角以不回答作出暂时选择。",
                }],
                "isComplete": True,
                "revisionOf": draft["id"],
                "reviewId": review["data"]["id"],
                "issueResolutions": [{
                    "issueId": "issue-1",
                    "status": "resolved",
                    "sceneIds": ["scene-1"],
                    "executionFields": [
                        "conflictResult",
                        "turnResult",
                    ],
                    "resolutionEvidence": "删去解释对白，以沉默完成选择。",
                }],
                "reassessedSceneIds": ["scene-1"],
                "resolvedIssueIds": ["issue-1"],
                "partiallyResolvedIssueIds": [],
            },
            contentText="INT. 房间 - 夜\n\n他没有回答。",
            derivedFromIds=[draft["id"], review["data"]["id"]],
        ),
    )
    await accept_screenplay_document(revision["data"]["id"])

    project = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    old_review = await temp_db.fetch_one(
        "SELECT status FROM screenplay_documents WHERE id = ?",
        [review["data"]["id"]],
    )
    assert project["active_stage"] == "review"
    assert old_review["status"] == "superseded"

    stale_revision = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="错误复用旧审阅",
            contentJson={
                "sceneListId": revision["data"]["content_json"]["sceneListId"],
                "completedSceneIds": (
                    revision["data"]["content_json"]["completedSceneIds"]
                ),
                "sceneExecutions": (
                    revision["data"]["content_json"]["sceneExecutions"]
                ),
                "isComplete": True,
                "revisionOf": revision["data"]["id"],
                "reviewId": review["data"]["id"],
            },
            contentText="不能复用旧审阅。",
            derivedFromIds=[revision["data"]["id"], review["data"]["id"]],
        ),
    )
    with pytest.raises(AppError, match="已接受审阅"):
        await accept_screenplay_document(stale_revision["data"]["id"])

    incomplete_rereview = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="review",
            title="遗漏核验的复审",
            contentJson={
                "reviewedDraftId": revision["data"]["id"],
                "verdict": "ready",
                "issues": [],
            },
            contentText="没有核验上一轮问题。",
            derivedFromIds=[revision["data"]["id"]],
        ),
    )
    with pytest.raises(AppError, match="verificationResults"):
        await accept_screenplay_document(incomplete_rereview["data"]["id"])

    dropped_issue_rereview = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="review",
            title="错误丢弃未通过问题",
            contentJson={
                "reviewedDraftId": revision["data"]["id"],
                "verdict": "revise",
                "issues": [],
                "verificationOfReviewId": review["data"]["id"],
                "verificationResults": [{
                    "issueId": "issue-1",
                    "status": "still_open",
                    "sceneIds": ["scene-1"],
                    "executionFields": [
                        "conflictResult",
                        "turnResult",
                    ],
                    "acceptanceCriteria": "选择压力通过行动呈现。",
                    "priorResolutionStatus": "resolved",
                    "resolutionEvidence": "删去解释对白，以沉默完成选择。",
                    "verificationEvidence": "沉默存在，但尚未改变人物行动。",
                }],
                "verifiedIssueIds": [],
                "failedVerificationIssueIds": ["issue-1"],
            },
            contentText="核验未通过，但错误地没有延续问题。",
            derivedFromIds=[revision["data"]["id"]],
        ),
    )
    with pytest.raises(AppError, match="必须延续"):
        await accept_screenplay_document(dropped_issue_rereview["data"]["id"])

    final_review = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="review",
            title="复审通过",
            contentJson={
                "reviewedDraftId": revision["data"]["id"],
                "verdict": "ready",
                "issues": [],
                "verificationOfReviewId": review["data"]["id"],
                "verificationResults": [{
                    "issueId": "issue-1",
                    "status": "verified",
                    "sceneIds": ["scene-1"],
                    "executionFields": [
                        "conflictResult",
                        "turnResult",
                    ],
                    "acceptanceCriteria": "选择压力通过行动呈现。",
                    "priorResolutionStatus": "resolved",
                    "resolutionEvidence": "删去解释对白，以沉默完成选择。",
                    "verificationEvidence": "沉默已取代解释对白并形成选择。",
                }],
                "verifiedIssueIds": ["issue-1"],
                "failedVerificationIssueIds": [],
            },
            contentText="当前版本可以交付。",
            derivedFromIds=[revision["data"]["id"]],
        ),
    )
    await accept_screenplay_document(final_review["data"]["id"])
    completed = await temp_db.fetch_one(
        "SELECT active_stage FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert completed["active_stage"] == "completed"
    completed_project = await get_screenplay_project(project_id)
    manifest = completed_project["data"]["delivery_manifest"]
    assert manifest["lineage"]["finalDraftId"] == revision["data"]["id"]
    assert manifest["lineage"]["finalReviewId"] == final_review["data"]["id"]
    assert manifest["qualityGate"] == {
        "verdict": "ready",
        "openIssueCount": 0,
        "verifiedPriorIssueCount": 1,
        "sceneCount": 1,
        "sceneExecutionCount": 1,
    }
    assert len(manifest["documents"]) == 5
    assert len(manifest["packageDigest"]) == 64


async def test_screenplay_agent_session_is_stable_and_deleted_with_project(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project("会话项目")
    project_id = created["data"]["project"]["id"]
    first = await get_or_create_screenplay_agent_session(project_id)
    second = await get_or_create_screenplay_agent_session(project_id)
    session_id = first["data"]["id"]
    assert second["data"]["id"] == session_id
    assert first["data"]["scope"] == "screenplay"
    assert first["data"]["screenplay_project_id"] == project_id

    await temp_db.execute(
        "INSERT INTO ai_conversations "
        "(session_id, prompt, response) VALUES (?, '继续', '好的')",
        [session_id],
    )
    await delete_screenplay_project(project_id)

    assert await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE id = ?",
        [session_id],
    ) is None
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE session_id = ?",
        [session_id],
    ) is None


async def test_screenplay_agent_sessions_can_be_created_and_listed(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project("多会话项目")
    project_id = created["data"]["project"]["id"]
    first = await get_or_create_screenplay_agent_session(project_id)
    second = await create_screenplay_agent_session(project_id)

    sessions = (await list_screenplay_agent_sessions(project_id))["data"]
    assert [session["id"] for session in sessions] == [
        first["data"]["id"],
        second["data"]["id"],
    ]
    assert second["data"]["title"] == "新对话"

    await temp_db.execute(
        "UPDATE ai_sessions SET closed = 1 WHERE id = ?",
        [first["data"]["id"]],
    )
    open_sessions = (await list_screenplay_agent_sessions(project_id))["data"]
    all_sessions = (
        await list_screenplay_agent_sessions(project_id, includeClosed=True)
    )["data"]
    assert [session["id"] for session in open_sessions] == [second["data"]["id"]]
    assert len(all_sessions) == 2


async def test_derived_document_must_belong_to_same_project(
    temp_db: DatabaseConnection,
):
    first = await _create_original_project("项目一")
    second = await _create_original_project("项目二")

    with pytest.raises(AppError, match="上游文档不属于当前剧本项目"):
        await create_screenplay_document(
            first["data"]["project"]["id"],
            CreateScreenplayDocumentRequest(
                kind="beat_sheet",
                title="错误引用",
                derivedFromIds=[second["data"]["initialDocument"]["id"]],
            ),
        )


async def test_deleting_source_book_detaches_but_preserves_screenplay_project(
    temp_db: DatabaseConnection,
):
    await _insert_book(temp_db)
    await _insert_chapter(temp_db)
    created = await create_screenplay_project(CreateScreenplayProjectRequest(
        title="保留的改编项目",
        sourceKind="book",
        sourceBookId="book1",
        format="短片",
        approach="自由改编",
    ))
    project_id = created["data"]["project"]["id"]
    session = await get_or_create_screenplay_agent_session(project_id)
    session_id = session["data"]["id"]
    await temp_db.execute(
        "INSERT INTO ai_conversations "
        "(session_id, prompt, response) VALUES (?, '继续改编', '好的')",
        [session_id],
    )

    await delete_book("book1")

    projects = (await list_screenplay_projects())["data"]
    preserved = next(item for item in projects if item["id"] == project_id)
    assert preserved["source_kind"] == "book"
    assert preserved["source_book_id"] is None
    documents = (await list_screenplay_documents(project_id))["data"]
    assert len(documents) == 1
    preserved_session = await temp_db.fetch_one(
        "SELECT book_id, screenplay_project_id FROM ai_sessions WHERE id = ?",
        [session_id],
    )
    assert preserved_session["book_id"] is None
    assert preserved_session["screenplay_project_id"] == project_id
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE session_id = ?",
        [session_id],
    ) is not None


async def test_delete_project_cascades_documents(temp_db: DatabaseConnection):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]

    response = await delete_screenplay_project(project_id)

    assert response["success"] is True
    project_count = await temp_db.fetch_one(
        "SELECT COUNT(*) AS c FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    document_count = await temp_db.fetch_one(
        "SELECT COUNT(*) AS c FROM screenplay_documents WHERE project_id = ?",
        [project_id],
    )
    assert int(project_count["c"]) == 0
    assert int(document_count["c"]) == 0


async def test_document_creation_attaches_and_lists_agent_source_refs(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    await record_source_refs(
        temp_db,
        project_id=project_id,
        agent_run_id="run-route",
        tool_name="searchSourceMaterial",
        refs=[{
            "sourceType": "outline",
            "sourceId": "outline-1",
            "sourceRevision": "a" * 64,
            "excerpt": "核心冲突",
        }],
    )
    proposal = {
        "kind": "creative_brief",
        "title": "带来源简报",
        "contentJson": {},
        "contentText": "新版简报",
        "derivedFromIds": [initial_id],
    }
    await _record_agent_proposal(
        temp_db,
        project_id=project_id,
        run_id="run-route",
        proposal=proposal,
    )

    response = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind=proposal["kind"],
            title=proposal["title"],
            contentJson=proposal["contentJson"],
            contentText=proposal["contentText"],
            derivedFromIds=proposal["derivedFromIds"],
            sourceRunId="run-route",
        ),
    )
    document_id = response["data"]["id"]
    refs = (
        await list_screenplay_source_refs(
            project_id,
            document_id=document_id,
            agent_run_id=None,
        )
    )["data"]

    assert len(refs) == 1
    assert refs[0]["document_id"] == document_id
    assert refs[0]["agent_run_id"] == "run-route"


async def test_document_creation_follows_artifact_source_run_across_continuation(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    artifact_id = "artifact-cross-run"
    artifact_ref = (
        "artifact://purrtypos.screenplay/creative_brief_entries/"
        f"{artifact_id}"
    )
    await record_source_refs(
        temp_db,
        project_id=project_id,
        agent_run_id="run-source-reader",
        tool_name="readSourcePassages",
        refs=[{
            "sourceType": "chapter",
            "sourceId": "chapter-cross-run",
            "sourceRevision": "c" * 64,
            "coverageMode": "full",
            "excerpt": "跨 Run 素材凭证",
        }],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, created_by_run_id, "
        "status, resource_ref) VALUES (?, 'purrtypos.screenplay', "
        "'creative_brief_entries', ?, ?, ?, 'finalized', ?)",
        [
            artifact_id,
            project_id,
            "run-source-reader",
            "run-source-reader",
            artifact_ref,
        ],
    )
    proposal = {
        "kind": "creative_brief",
        "title": "跨 Run 提案",
        "contentJson": {"artifactRef": artifact_ref},
        "contentText": "最终提案由恢复后的 Run 收口。",
        "derivedFromIds": [initial_id],
    }
    await _record_agent_proposal(
        temp_db,
        project_id=project_id,
        run_id="run-finalizer",
        proposal=proposal,
    )

    response = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind=proposal["kind"],
            title=proposal["title"],
            contentJson=proposal["contentJson"],
            contentText=proposal["contentText"],
            derivedFromIds=proposal["derivedFromIds"],
            sourceRunId="run-finalizer",
        ),
    )
    refs = (
        await list_screenplay_source_refs(
            project_id,
            document_id=response["data"]["id"],
            agent_run_id=None,
        )
    )["data"]

    assert len(refs) == 1
    assert refs[0]["document_id"] == response["data"]["id"]
    assert refs[0]["agent_run_id"] == "run-source-reader"


async def test_document_creation_rejects_agent_text_without_proposal_effect(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    session = (
        await get_or_create_screenplay_agent_session(project_id)
    )["data"]
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, final_response) "
        "VALUES ('run-text-only', ?, 'done', '生成提案', ?)",
        [
            session["id"],
            (
                "<tool_call><function=proposeSourceAnalysis>"
                "<parameter=contentText>伪提案</parameter>"
                "</function></tool_call>"
            ),
        ],
    )

    with pytest.raises(AppError, match="没有产生与当前内容一致的正式提案"):
        await create_screenplay_document(
            project_id,
            CreateScreenplayDocumentRequest(
                kind="creative_brief",
                title="不应保存的文本",
                contentText="伪提案",
                derivedFromIds=[initial_id],
                sourceRunId="run-text-only",
            ),
        )

    documents = await list_screenplay_documents(project_id)
    assert len(documents["data"]) == 1


async def test_restore_document_creates_new_draft_and_copies_source_refs(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    await record_source_refs(
        temp_db,
        project_id=project_id,
        agent_run_id="run-restore",
        tool_name="searchSourceMaterial",
        refs=[{
            "sourceType": "outline",
            "sourceId": "outline-restore",
            "sourceRevision": "b" * 64,
            "excerpt": "被恢复版本的来源",
        }],
    )
    proposal = {
        "kind": "creative_brief",
        "title": "可恢复简报",
        "contentJson": {"theme": "重逢"},
        "contentText": "旧版本内容",
        "derivedFromIds": [initial_id],
    }
    await _record_agent_proposal(
        temp_db,
        project_id=project_id,
        run_id="run-restore",
        proposal=proposal,
    )
    historical = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind=proposal["kind"],
            title=proposal["title"],
            contentJson=proposal["contentJson"],
            contentText=proposal["contentText"],
            derivedFromIds=proposal["derivedFromIds"],
            sourceRunId="run-restore",
        ),
    )
    historical_id = historical["data"]["id"]
    await accept_screenplay_document(historical_id)

    restored = (await restore_screenplay_document(historical_id))["data"]

    assert restored["id"] != historical_id
    assert restored["version"] == 3
    assert restored["status"] == "draft"
    assert restored["title"] == "可恢复简报（恢复自 v2）"
    assert restored["content_json"] == {"theme": "重逢"}
    assert restored["content_text"] == "旧版本内容"
    assert restored["derived_from_ids"] == [historical_id]
    original = await temp_db.fetch_one(
        "SELECT status FROM screenplay_documents WHERE id = ?",
        [historical_id],
    )
    assert original["status"] == "accepted"
    copied_refs = (
        await list_screenplay_source_refs(
            project_id,
            document_id=restored["id"],
            agent_run_id=None,
        )
    )["data"]
    assert len(copied_refs) == 1
    assert copied_refs[0]["source_id"] == "outline-restore"
    assert copied_refs[0]["source_revision"] == "b" * 64
    assert copied_refs[0]["agent_run_id"] == "run-restore"


async def test_archived_project_is_read_only_until_restored(
    temp_db: DatabaseConnection,
):
    created = await _create_original_project()
    project_id = created["data"]["project"]["id"]
    document_id = created["data"]["initialDocument"]["id"]
    archived = await update_screenplay_project(
        project_id,
        UpdateScreenplayProjectRequest(status="archived"),
    )
    assert archived["data"]["status"] == "archived"

    with pytest.raises(AppError, match="项目已归档"):
        await update_screenplay_document(
            document_id,
            UpdateScreenplayDocumentRequest(contentText="不能修改"),
        )
    with pytest.raises(AppError, match="项目已归档"):
        await restore_screenplay_document(document_id)
    with pytest.raises(AppError, match="项目已归档"):
        await create_screenplay_document(
            project_id,
            CreateScreenplayDocumentRequest(
                kind="creative_brief",
                title="不能新增",
            ),
        )

    restored_project = await update_screenplay_project(
        project_id,
        UpdateScreenplayProjectRequest(status="active"),
    )
    assert restored_project["data"]["status"] == "active"
    updated = await update_screenplay_document(
        document_id,
        UpdateScreenplayDocumentRequest(contentText="恢复后可修改"),
    )
    assert updated["data"]["content_text"] == "恢复后可修改"


async def test_pdf_export_uses_latest_accepted_screenplay_draft(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await _create_original_project("PDF 剧本")
    project_id = created["data"]["project"]["id"]
    initial_id = created["data"]["initialDocument"]["id"]
    draft = await create_screenplay_document(
        project_id,
        CreateScreenplayDocumentRequest(
            kind="scene_draft",
            title="完整稿",
            contentJson={"isComplete": True},
            contentText="INT. 电台 - 夜\n\n@林岚\n广播开始了。",
            derivedFromIds=[initial_id],
        ),
    )
    await temp_db.execute(
        "UPDATE screenplay_documents SET status = 'accepted' WHERE id = ?",
        [draft["data"]["id"]],
    )

    from services import screenplay_pdf

    captured = {}

    def _fake_build(**kwargs):
        captured.update(kwargs)
        return b"%PDF-test"

    monkeypatch.setattr(screenplay_pdf, "build_screenplay_pdf", _fake_build)
    response = await export_screenplay_pdf(project_id)

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-test"
    assert captured == {
        "title": "PDF 剧本",
        "screenplay_format": "单集剧",
        "content": "INT. 电台 - 夜\n\n@林岚\n广播开始了。",
    }
