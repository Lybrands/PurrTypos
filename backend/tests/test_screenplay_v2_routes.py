from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import routers.screenplay_v2 as screenplay_v2_routes
from fastapi import FastAPI

from agents.screenplay.project_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from exceptions import AppError, app_error_handler
from routers.screenplay_v2 import (
    adjudicate_screenplay_v2_review,
    accept_screenplay_v2_revision,
    archive_screenplay_v2_project,
    create_screenplay_v2_project,
    create_screenplay_v2_session,
    create_screenplay_v2_working_copy_from_revision,
    delete_screenplay_v2_project,
    ensure_current_screenplay_v2_session,
    finalize_screenplay_v2_project,
    get_screenplay_v2_revision,
    get_screenplay_v2_workspace,
    list_screenplay_v2_projects,
    list_screenplay_v2_sessions,
    list_screenplay_v2_revision_history,
    publish_screenplay_v2_working_copy,
    restore_screenplay_v2_project,
    update_screenplay_v2_project,
    update_screenplay_v2_working_copy,
)
from schemas.screenplay_v2 import (
    AdjudicateScreenplayV2ReviewRequest,
    AcceptScreenplayV2RevisionRequest,
    ChangeScreenplayV2ProjectLifecycleRequest,
    CreateScreenplayV2ProjectRequest,
    CreateScreenplayV2WorkingCopyFromRevisionRequest,
    DeleteScreenplayV2ProjectRequest,
    FinalizeScreenplayV2ProjectRequest,
    PublishScreenplayV2WorkingCopyRequest,
    UpdateScreenplayV2ProjectRequest,
    UpdateScreenplayV2WorkingCopyRequest,
)
from tests.support.screenplay_v2_driver import accept_revision, seed_revision


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


async def _insert_book_with_chapter(db: DatabaseConnection) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('book-v2', 'V2 来源书')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing-v2', '写作目录', 'writing', 'book-v2')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-v2', 'writing-v2', '第一章', 1)"
    )


def _original_request(*, title: str = "原创 v2"):
    return CreateScreenplayV2ProjectRequest.model_validate({
        "title": title,
        "format": "featureFilm",
        "source": {"type": "original"},
        "brief": {
            "approach": "从人物关系开始",
            "premise": "一个人必须决定是否公开真相。",
        },
    })


async def _seed_project_with_review(
    db: DatabaseConnection,
    *,
    issues: list[dict[str, object]],
    verdict: str = "major_rework",
    failed_episodes: list[dict[str, object]] | None = None,
) -> tuple[str, str, str, dict[str, object]]:
    created = await create_screenplay_v2_project(
        _original_request(title="人工审阅定稿"),
        idempotency_key="create-review-adjudication-project",
    )
    project_id = str(created["data"]["project"]["id"])
    chain = [
        (
            "creative_brief",
            {"approach": "人物驱动", "premise": "公开真相"},
        ),
        (
            "beat_sheet",
            {"beats": [{"id": "beat-1", "summary": "真相浮现"}]},
        ),
        (
            "scene_list",
            {
                "scenes": [{
                    "id": "scene-1",
                    "episodeNumber": 1,
                    "heading": "审讯室",
                    "objective": "逼问真相",
                    "conflict": "双方对峙",
                    "turn": "证据出现",
                    "synopsis": "主角看到关键证据。",
                }],
            },
        ),
    ]
    accepted_ids: list[str] = []
    for kind, content in chain:
        revision_id = await seed_revision(
            db,
            project_id=project_id,
            kind=kind,
            title=kind,
            content_json=content,
            content_text=kind,
            derived_from_ids=accepted_ids[-1:],
        )
        accepted_ids.append(revision_id)
        await accept_revision(db, revision_id)

    draft_id = await seed_revision(
        db,
        project_id=project_id,
        kind="scene_draft",
        title="完整剧本",
        content_json={
            "isComplete": True,
            "episodeDrafts": [{
                "episodeNumber": 1,
                "title": "第一集",
                "sceneIds": ["scene-1"],
                "contentText": "INT. 审讯室 - 日",
            }],
        },
        content_text="INT. 审讯室 - 日",
        derived_from_ids=accepted_ids[-1:],
    )
    await accept_revision(db, draft_id)
    review_id = await seed_revision(
        db,
        project_id=project_id,
        kind="review",
        title="审阅报告",
        content_json={
            "reviewedDraftId": draft_id,
            "verdict": verdict,
            "issues": issues,
            "issueCount": len(issues),
            "completedEpisodes": [] if failed_episodes else [1],
            **(
                {"failedEpisodes": failed_episodes}
                if failed_episodes is not None
                else {}
            ),
            "inputContractVersion": 2,
        },
        content_text="# 审阅报告",
        derived_from_ids=[draft_id],
    )
    await accept_revision(db, review_id)
    workspace = (await get_screenplay_v2_workspace(project_id))["data"]
    return project_id, draft_id, review_id, workspace


@pytest_asyncio.fixture
async def pdf_client(temp_db: DatabaseConnection):
    app = FastAPI()
    app.include_router(screenplay_v2_routes.router, prefix="/api")
    app.add_exception_handler(AppError, app_error_handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_pdf_export_uses_accepted_main_document_not_newer_candidates(
    temp_db: DatabaseConnection, pdf_client, monkeypatch,
):
    project_id, _, _, workspace = await _seed_project_with_review(
        temp_db, issues=[],
    )
    await seed_revision(
        temp_db,
        project_id=project_id,
        kind="scene_draft",
        title="未接受的候选",
        content_json={"isComplete": True},
        content_text="这段候选正文不能被导出",
        derived_from_ids=[workspace["workflow"]["heads"]["sceneList"]["id"]],
    )
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        return b"%PDF-1.4\naccepted screenplay\n%%EOF"

    monkeypatch.setattr(
        "agents.screenplay.project_service.build_screenplay_pdf", render,
    )
    response = await pdf_client.post(
        f"/api/screenplay/v2/projects/{project_id}/export/pdf",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.content == b"%PDF-1.4\naccepted screenplay\n%%EOF"
    assert calls == [{
        "title": "人工审阅定稿",
        "screenplay_format": "电影",
        "content": "INT. 审讯室 - 日",
    }]


@pytest.mark.parametrize("state, expected_status, message", [
    ("missing", 404, "剧本项目不存在"),
    ("unaccepted", 409, "项目尚无已接受的剧本正文"),
    ("empty", 422, "当前剧本版本没有可导出的正文"),
])
async def test_pdf_export_rejects_missing_or_unexportable_drafts(
    temp_db: DatabaseConnection, pdf_client, monkeypatch,
    state: str, expected_status: int, message: str,
):
    project_id, draft_id, _, _ = await _seed_project_with_review(
        temp_db, issues=[],
    )
    if state == "missing":
        project_id = "missing-project"
    elif state == "unaccepted":
        await temp_db.execute(
            "DELETE FROM screenplay_project_heads WHERE revision_id = ?",
            [draft_id],
        )
    else:
        await temp_db.execute(
            "UPDATE screenplay_revision_parts SET content_text = '  ' "
            "WHERE revision_id = ? AND part_type = 'document' "
            "AND part_key = 'main'",
            [draft_id],
        )

    def unexpected_render(**kwargs):
        pytest.fail("PDF renderer must not run without exportable accepted content")

    monkeypatch.setattr(
        "agents.screenplay.project_service.build_screenplay_pdf",
        unexpected_render,
    )
    response = await pdf_client.post(
        f"/api/screenplay/v2/projects/{project_id}/export/pdf",
    )

    assert response.status_code == expected_status
    assert response.json()["success"] is False
    assert message in response.json()["error"]


async def test_legacy_review_execution_failure_is_not_materialized_as_review_content(
    temp_db: DatabaseConnection,
):
    _, _, review_id, workspace = await _seed_project_with_review(
        temp_db,
        issues=[],
        failed_episodes=[{
            "episodeNumber": 1,
            "code": "model_output_truncated",
            "message": "第 1 集审阅失败",
            "retryable": True,
        }],
    )

    result = await get_screenplay_v2_revision(review_id, view="full")
    assert [
        part for part in result["data"]["parts"]
        if part["type"] == "episode"
    ] == []
    assert workspace["workflow"]["review"]["findings"] == []
    assert workspace["workflow"]["review"]["hardChecks"] == [{
        "code": "review_execution_contaminated",
        "message": "当前审阅报告混入了执行故障，需要重新审阅",
    }]


async def test_latest_review_lookup_is_bound_to_the_exact_draft_revision(
    temp_db: DatabaseConnection,
):
    project_id, draft_id, first_review_id, _ = await _seed_project_with_review(
        temp_db,
        issues=[],
        verdict="ready",
    )
    second_review_id = await seed_revision(
        temp_db,
        project_id=project_id,
        kind="review",
        title="第二次审阅",
        content_json={
            "reviewedDraftId": draft_id,
            "verdict": "ready",
            "issues": [],
            "completedEpisodes": [1],
            "inputContractVersion": 2,
        },
        content_text="# 第二次审阅",
        derived_from_ids=[draft_id],
    )

    service = ScreenplayV2ProjectService(temp_db)
    latest = await service.get_latest_review_for_draft(
        project_id=project_id,
        draft_revision_id=draft_id,
    )
    missing = await service.get_latest_review_for_draft(
        project_id=project_id,
        draft_revision_id="draft-without-review",
    )

    assert latest is not None
    assert latest["id"] == second_review_id
    assert latest["id"] != first_review_id
    assert latest["role"] == "review"
    assert latest["inputRevisions"]["screenplayDraft"] == draft_id
    assert missing is None

    route = getattr(
        screenplay_v2_routes,
        "get_latest_screenplay_v2_review_for_draft",
    )
    response = await route(project_id, draft_id)
    assert response["success"] is True
    assert response["data"]["id"] == second_review_id


async def test_v2_project_creation_starts_with_working_copy_not_fake_revision(
    temp_db: DatabaseConnection,
):
    result = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-original-v2",
    )
    workspace = result["data"]
    project_id = workspace["project"]["id"]

    assert workspace["project"]["format"] == "featureFilm"
    assert workspace["project"]["stage"] == "brief"
    assert workspace["project"]["revision"] == 1
    assert len(workspace["deliverables"]) == 5
    assert workspace["workflow"]["heads"]["creativeBrief"] is None
    assert workspace["workflow"]["nextActions"] == [{
        "type": "generateDeliverable",
        "targetRole": "creativeBrief",
    }]
    assert workspace["workingCopies"][0]["role"] == "creativeBrief"
    assert workspace["workingCopies"][0]["content"]["fields"]["premise"]

    revision_count = await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions WHERE project_id = ?",
        [project_id],
    )
    assert revision_count == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'screenplay_documents'"
    ) is None


async def test_v2_project_creation_is_idempotent(
    temp_db: DatabaseConnection,
):
    first = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="stable-create-key",
    )
    second = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="stable-create-key",
    )

    assert second["data"]["project"]["id"] == first["data"]["project"]["id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_projects"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_working_copies"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_outbox_events"
    ) == {"count": 1}

    with pytest.raises(AppError, match="Idempotency-Key") as error:
        await create_screenplay_v2_project(
            _original_request(title="不同项目"),
            idempotency_key="stable-create-key",
        )
    assert error.value.status_code == 409


async def test_native_project_list_and_sessions_use_only_v2_routes(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(title="原生项目列表"),
        idempotency_key="create-native-list-project",
    )
    project_id = created["data"]["project"]["id"]

    listed = await list_screenplay_v2_projects(includeArchived=True)
    assert [project["id"] for project in listed["data"]] == [project_id]
    assert listed["data"][0]["brief"] == {
        "approach": "从人物关系开始",
        "premise": "一个人必须决定是否公开真相。",
    }

    current = await ensure_current_screenplay_v2_session(project_id)
    replayed_current = await ensure_current_screenplay_v2_session(project_id)
    assert replayed_current["data"]["id"] == current["data"]["id"]
    # 新会话标题走 ai_sessions.title 列默认值，与写作会话命名约定一致。
    assert current["data"]["title"] == "新对话"

    created_session = await create_screenplay_v2_session(
        project_id,
        idempotency_key="create-second-native-session",
    )
    replayed_session = await create_screenplay_v2_session(
        project_id,
        idempotency_key="create-second-native-session",
    )
    assert replayed_session["data"]["id"] == created_session["data"]["id"]

    sessions = await list_screenplay_v2_sessions(
        project_id,
        includeClosed=False,
    )
    assert [session["id"] for session in sessions["data"]] == [
        created_session["data"]["id"],
        current["data"]["id"],
    ]


async def test_native_project_metadata_uses_idempotent_project_cas(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-project-metadata",
    )
    project_id = created["data"]["project"]["id"]
    request = UpdateScreenplayV2ProjectRequest(
        expectedProjectRevision=1,
        title="重命名后的项目",
    )

    updated = await update_screenplay_v2_project(
        project_id,
        request,
        idempotency_key="rename-native-project",
    )
    assert updated["data"]["project"]["title"] == "重命名后的项目"
    assert updated["data"]["project"]["revision"] == 2

    replay = await update_screenplay_v2_project(
        project_id,
        request,
        idempotency_key="rename-native-project",
    )
    assert replay["data"]["project"]["revision"] == 2
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_command_receipts "
        "WHERE command_id = 'rename-native-project'"
    ) == {"count": 1}

    with pytest.raises(AppError, match="其他操作更新") as stale:
        await update_screenplay_v2_project(
            project_id,
            UpdateScreenplayV2ProjectRequest(
                expectedProjectRevision=1,
                title="过期写入",
            ),
            idempotency_key="rename-native-project-stale",
        )
    assert stale.value.status_code == 409

async def test_native_project_lifecycle_is_replay_safe(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-lifecycle-project",
    )
    project_id = created["data"]["project"]["id"]
    archived = await archive_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=1,
        ),
        idempotency_key="archive-project",
    )
    assert archived["data"]["project"]["lifecycle"] == "archived"
    assert archived["data"]["project"]["revision"] == 2

    replay = await archive_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=1,
        ),
        idempotency_key="archive-project",
    )
    assert replay["data"]["project"]["revision"] == 2

    restored = await restore_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=2,
        ),
        idempotency_key="restore-project",
    )
    assert restored["data"]["project"]["lifecycle"] == "active"
    assert restored["data"]["project"]["revision"] == 3


async def test_native_project_delete_is_cas_guarded_and_replay_safe(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-delete-project",
    )
    project_id = created["data"]["project"]["id"]
    session = await ensure_current_screenplay_v2_session(project_id)
    session_id = int(session["data"]["id"])
    conversation_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, prompt, response, client_turn_id) "
        "VALUES (?, '本地问题', '本地回答', 'screenplay-local-turn')",
        [session_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_local_conversation_turn_receipts "
        "(session_id, client_turn_id, payload_digest, status, conversation_id) "
        "VALUES (?, 'screenplay-local-turn', 'sha256:screenplay', "
        "'persisted', ?)",
        [session_id, conversation_id],
    )
    request = DeleteScreenplayV2ProjectRequest(expectedProjectRevision=1)

    deleted = await delete_screenplay_v2_project(
        project_id,
        request,
        idempotency_key="delete-native-project",
    )
    assert deleted["data"] == {"projectId": project_id, "deleted": True}
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_projects WHERE id = ?",
        [project_id],
    ) is None
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = ?",
        [session_id],
    ) is None
    assert await temp_db.fetch_one(
        "SELECT command_type, project_id FROM screenplay_command_receipts "
        "WHERE command_id = 'delete-native-project'"
    ) == {"command_type": "deleteProject", "project_id": project_id}

    replay = await delete_screenplay_v2_project(
        project_id,
        request,
        idempotency_key="delete-native-project",
    )
    assert replay == deleted


async def test_v2_book_project_freezes_source_snapshot(
    temp_db: DatabaseConnection,
):
    await _insert_book_with_chapter(temp_db)
    body = CreateScreenplayV2ProjectRequest.model_validate({
        "title": "改编 v2",
        "format": "series",
        "source": {
            "type": "book",
            "bookId": "book-v2",
            "scope": {
                "mode": "selectedChapters",
                "chapterIds": ["chapter-v2"],
            },
        },
        "brief": {"approach": "保留主线", "premise": ""},
    })
    result = await create_screenplay_v2_project(
        body,
        idempotency_key="create-book-v2",
    )
    workspace = result["data"]

    assert workspace["project"]["stage"] == "orientation"
    assert workspace["project"]["source"]["bookTitle"] == "V2 来源书"
    assert workspace["project"]["source"]["scope"]["chapterIds"] == [
        "chapter-v2"
    ]
    assert workspace["project"]["source"]["scope"]["mode"] == (
        "selectedChapters"
    )
    assert len(workspace["deliverables"]) == 6
    assert "sourceAnalysis" in workspace["workflow"]["heads"]

    await temp_db.execute("DELETE FROM books WHERE id = 'book-v2'")
    replay = await create_screenplay_v2_project(
        body,
        idempotency_key="create-book-v2",
    )
    assert replay["data"]["project"]["id"] == workspace["project"]["id"]
    assert replay["data"]["project"]["source"]["bookTitle"] == "V2 来源书"


async def test_working_copy_publish_and_accept_form_one_version_lifecycle(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-version-lifecycle",
    )
    workspace = created["data"]
    project_id = workspace["project"]["id"]
    working_copy = workspace["workingCopies"][0]

    updated = await update_screenplay_v2_working_copy(
        working_copy["id"],
        UpdateScreenplayV2WorkingCopyRequest(
            expectedRevision=1,
            content={
                "schemaVersion": 1,
                "role": "creativeBrief",
                "fields": {
                    "approach": "人物驱动",
                    "premise": "公开真相会失去最重要的人。",
                },
                "contentText": "# 创作简报\n\n人物必须在真相与关系之间作出选择。",
            },
        ),
    )
    assert updated["data"]["revision"] == 2
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions WHERE project_id = ?",
        [project_id],
    ) == {"count": 0}

    with pytest.raises(AppError, match="Working Copy 已被") as stale_copy:
        await update_screenplay_v2_working_copy(
            working_copy["id"],
            UpdateScreenplayV2WorkingCopyRequest(
                expectedRevision=1,
                content={"contentText": "stale"},
            ),
        )
    assert stale_copy.value.status_code == 409

    published = await publish_screenplay_v2_working_copy(
        working_copy["id"],
        PublishScreenplayV2WorkingCopyRequest(
            expectedProjectRevision=1,
            expectedWorkingCopyRevision=2,
        ),
        idempotency_key="publish-brief-v1",
    )
    candidate = published["data"]["revision"]
    assert candidate["revisionNo"] == 1
    assert published["data"]["workspace"]["project"]["revision"] == 2
    assert published["data"]["workspace"]["project"]["stage"] == "brief"
    assert published["data"]["workspace"]["workingCopies"][0]["revision"] == 3

    replayed_publish = await publish_screenplay_v2_working_copy(
        working_copy["id"],
        PublishScreenplayV2WorkingCopyRequest(
            expectedProjectRevision=1,
            expectedWorkingCopyRevision=2,
        ),
        idempotency_key="publish-brief-v1",
    )
    assert replayed_publish["data"]["revision"]["id"] == candidate["id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_parts "
        "WHERE revision_id = ?",
        [candidate["id"]],
    ) == {"count": 1}

    accepted = await accept_screenplay_v2_revision(
        project_id,
        candidate["id"],
        AcceptScreenplayV2RevisionRequest(expectedProjectRevision=2),
        idempotency_key="accept-brief-v1",
    )
    accepted_workspace = accepted["data"]["workspace"]
    assert accepted["data"]["acceptedRevisionId"] == candidate["id"]
    assert accepted["data"]["workflow"]["stage"] == "structure"
    assert accepted_workspace["project"]["revision"] == 3
    assert accepted_workspace["workflow"]["heads"]["creativeBrief"]["id"] == (
        candidate["id"]
    )
    assert accepted_workspace["candidates"] == []

    replayed_accept = await accept_screenplay_v2_revision(
        project_id,
        candidate["id"],
        AcceptScreenplayV2RevisionRequest(expectedProjectRevision=2),
        idempotency_key="accept-brief-v1",
    )
    assert replayed_accept["data"]["acceptanceEventId"] == (
        accepted["data"]["acceptanceEventId"]
    )
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_acceptance_events "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'screenplay_documents'"
    ) is None


async def test_accepting_new_upstream_revision_requires_atomic_invalidation(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-invalidation-project",
    )
    workspace = created["data"]
    project_id = workspace["project"]["id"]
    copy_id = workspace["workingCopies"][0]["id"]

    first_publish = await publish_screenplay_v2_working_copy(
        copy_id,
        PublishScreenplayV2WorkingCopyRequest(
            expectedProjectRevision=1,
            expectedWorkingCopyRevision=1,
        ),
        idempotency_key="publish-invalidation-brief-1",
    )
    first_brief_id = first_publish["data"]["revision"]["id"]
    await accept_screenplay_v2_revision(
        project_id,
        first_brief_id,
        AcceptScreenplayV2RevisionRequest(expectedProjectRevision=2),
        idempotency_key="accept-invalidation-brief-1",
    )

    structure_deliverable = await temp_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = ? AND role = 'structure'",
        [project_id],
    )
    assert structure_deliverable is not None
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "created_by) VALUES ('structure-head', ?, ?, 1, 'structure-digest', 'user')",
        [project_id, structure_deliverable["id"]],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_inputs "
        "(revision_id, input_role, input_revision_id) "
        "VALUES ('structure-head', 'creativeBrief', ?)",
        [first_brief_id],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) "
        "VALUES (?, ?, 'structure-head')",
        [project_id, structure_deliverable["id"]],
    )

    updated = await update_screenplay_v2_working_copy(
        copy_id,
        UpdateScreenplayV2WorkingCopyRequest(
            expectedRevision=2,
            content={
                "schemaVersion": 1,
                "role": "creativeBrief",
                "fields": {
                    "approach": "新版方向",
                    "premise": "新版前提",
                },
            },
        ),
    )
    assert updated["data"]["revision"] == 3
    second_publish = await publish_screenplay_v2_working_copy(
        copy_id,
        PublishScreenplayV2WorkingCopyRequest(
            expectedProjectRevision=3,
            expectedWorkingCopyRevision=3,
        ),
        idempotency_key="publish-invalidation-brief-2",
    )
    second_brief_id = second_publish["data"]["revision"]["id"]

    with pytest.raises(AppError, match="下游版本失效") as confirmation:
        await accept_screenplay_v2_revision(
            project_id,
            second_brief_id,
            AcceptScreenplayV2RevisionRequest(expectedProjectRevision=4),
            idempotency_key="accept-invalidation-brief-2-preview",
        )
    assert confirmation.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT revision_id FROM screenplay_project_heads "
        "WHERE project_id = ? AND deliverable_id = ?",
        [project_id, structure_deliverable["id"]],
    ) == {"revision_id": "structure-head"}

    accepted = await accept_screenplay_v2_revision(
        project_id,
        second_brief_id,
        AcceptScreenplayV2RevisionRequest(
            expectedProjectRevision=4,
            confirmInvalidation=True,
        ),
        idempotency_key="accept-invalidation-brief-2",
    )
    assert accepted["data"]["invalidatedHeads"] == [{
        "role": "structure",
        "revisionId": "structure-head",
    }]
    assert accepted["data"]["workflow"]["stage"] == "structure"
    assert await temp_db.fetch_one(
        "SELECT revision_id FROM screenplay_project_heads "
        "WHERE project_id = ? AND deliverable_id = ?",
        [project_id, structure_deliverable["id"]],
    ) is None

    first_page = await list_screenplay_v2_revision_history(
        project_id,
        "creativeBrief",
        cursor=None,
        limit=1,
    )
    assert [(item["id"], item["status"]) for item in first_page["data"]["items"]] == [
        (second_brief_id, "current"),
    ]
    assert first_page["data"]["nextCursor"] == "2"
    second_page = await list_screenplay_v2_revision_history(
        project_id,
        "creativeBrief",
        cursor=first_page["data"]["nextCursor"],
        limit=10,
    )
    assert [(item["id"], item["status"]) for item in second_page["data"]["items"]] == [
        (first_brief_id, "historical"),
    ]
    assert second_page["data"]["nextCursor"] is None


async def test_revision_can_seed_a_working_copy_without_losing_parts_or_sources(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(title="历史版本编辑"),
        idempotency_key="create-history-edit-project",
    )
    project_id = created["data"]["project"]["id"]
    structure_deliverable = f"spdel:{project_id}:structure"
    scenes_deliverable = f"spdel:{project_id}:sceneList"
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('structure-current', ?, ?, 1, 'structure-digest', 'agent')",
        [project_id, structure_deliverable],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, "
        "content_text, content_digest) VALUES "
        "('structure-current', 'document', 'main', 0, '{}', '', 'structure-part')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) "
        "VALUES (?, ?, 'structure-current')",
        [project_id, structure_deliverable],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('scenes-history', ?, ?, 1, 'scenes-digest', 'agent')",
        [project_id, scenes_deliverable],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_inputs "
        "(revision_id, input_role, input_revision_id) "
        "VALUES ('scenes-history', 'structure', 'structure-current')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, "
        "content_text, content_digest) VALUES "
        "('scenes-history', 'document', 'main', 0, ?, '旧场景表', 'main-digest')",
        [json.dumps({"structureId": "structure-current", "episodeCount": 2})],
    )
    for episode_number in (1, 2):
        await temp_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES "
            "('scenes-history', 'episode', ?, ?, ?, ?, ?)",
            [
                str(episode_number),
                episode_number,
                json.dumps({
                    "episodeNumber": episode_number,
                    "scenes": [{"id": f"scene-{episode_number}"}],
                }),
                f"第 {episode_number} 集场景",
                f"episode-{episode_number}-digest",
            ],
        )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_source_refs "
        "(revision_id, source_type, source_id, source_revision, excerpt) "
        "VALUES ('scenes-history', 'bookChapter', 'chapter-1', 'rev-1', '证据')"
    )

    seeded = await create_screenplay_v2_working_copy_from_revision(
        project_id,
        "scenes-history",
        CreateScreenplayV2WorkingCopyFromRevisionRequest(
            expectedProjectRevision=1,
        ),
        idempotency_key="seed-scenes-working-copy",
    )
    working_copy = seeded["data"]
    assert working_copy["baseRevisionId"] == "scenes-history"
    assert working_copy["content"]["contentText"] == "旧场景表"
    assert [part["key"] for part in working_copy["content"]["parts"]] == [
        "1",
        "2",
    ]

    replayed = await create_screenplay_v2_working_copy_from_revision(
        project_id,
        "scenes-history",
        CreateScreenplayV2WorkingCopyFromRevisionRequest(
            expectedProjectRevision=1,
        ),
        idempotency_key="seed-scenes-working-copy",
    )
    assert replayed["data"] == working_copy

    with pytest.raises(AppError, match="Working Copy 已被") as stale_seed:
        await create_screenplay_v2_working_copy_from_revision(
            project_id,
            "scenes-history",
            CreateScreenplayV2WorkingCopyFromRevisionRequest(
                expectedProjectRevision=1,
                expectedWorkingCopyRevision=working_copy["revision"] + 1,
            ),
            idempotency_key="seed-scenes-working-copy-stale",
        )
    assert stale_seed.value.status_code == 409

    updated_content = dict(working_copy["content"])
    updated_content["contentText"] = "编辑后的场景表"
    updated = await update_screenplay_v2_working_copy(
        working_copy["id"],
        UpdateScreenplayV2WorkingCopyRequest(
            expectedRevision=working_copy["revision"],
            content=updated_content,
        ),
    )
    assert updated["data"]["revision"] == 2

    published = await publish_screenplay_v2_working_copy(
        working_copy["id"],
        PublishScreenplayV2WorkingCopyRequest(
            expectedProjectRevision=1,
            expectedWorkingCopyRevision=2,
        ),
        idempotency_key="publish-edited-scenes",
    )
    revision_id = published["data"]["revision"]["id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_parts "
        "WHERE revision_id = ?",
        [revision_id],
    ) == {"count": 3}
    assert await temp_db.fetch_one(
        "SELECT content_text FROM screenplay_revision_parts "
        "WHERE revision_id = ? AND part_type = 'document' AND part_key = 'main'",
        [revision_id],
    ) == {"content_text": "编辑后的场景表"}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_source_refs "
        "WHERE revision_id = ?",
        [revision_id],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT input_revision_id FROM screenplay_revision_inputs "
        "WHERE revision_id = ? AND input_role = 'structure'",
        [revision_id],
    ) == {"input_revision_id": "structure-current"}


async def test_review_workspace_requires_user_decisions_before_finalization(
    temp_db: DatabaseConnection,
):
    issues = [
        {
            "id": "pace-1",
            "severity": "major",
            "description": "中段节奏偏慢",
            "sceneIds": ["scene-1"],
        },
        {
            "id": "dialogue-1",
            "severity": "minor",
            "description": "对白存在重复",
            "sceneIds": ["scene-1"],
        },
    ]
    project_id, draft_id, review_id, workspace = await _seed_project_with_review(
        temp_db,
        issues=issues,
    )

    assert workspace["project"]["stage"] == "review"
    assert workspace["workflow"]["review"]["phase"] == "adjudicating"
    assert workspace["workflow"]["review"]["counts"]["pending"] == 2
    assert workspace["workflow"]["nextActions"] == []

    with pytest.raises(AppError, match="还有 2 条审阅意见待处理") as blocked:
        await finalize_screenplay_v2_project(
            project_id,
            FinalizeScreenplayV2ProjectRequest(
                expectedProjectRevision=workspace["project"]["revision"],
                draftRevisionId=draft_id,
                reviewRevisionId=review_id,
            ),
            idempotency_key="finalize-with-pending-findings",
        )
    assert blocked.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_finalization_events "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 0}


async def test_batch_review_decisions_enable_explicit_replay_safe_finalization(
    temp_db: DatabaseConnection,
):
    issues = [
        {
            "id": f"issue-{index}",
            "severity": "major",
            "description": f"审阅意见 {index}",
            "sceneIds": ["scene-1"],
        }
        for index in range(1, 7)
    ]
    project_id, draft_id, review_id, workspace = await _seed_project_with_review(
        temp_db,
        issues=issues,
    )
    decisions_request = AdjudicateScreenplayV2ReviewRequest.model_validate({
        "expectedProjectRevision": workspace["project"]["revision"],
        "reviewRevisionId": review_id,
        "decisions": [
            *[
                {
                    "issueId": f"issue-{index}",
                    "status": "riskAccepted",
                    "note": "用户明确接受风险",
                }
                for index in range(1, 6)
            ],
            {
                "issueId": "issue-6",
                "status": "dismissed",
                "note": "用户判断为误报",
            },
        ],
    })
    adjudicated = await adjudicate_screenplay_v2_review(
        project_id,
        decisions_request,
        idempotency_key="batch-adjudicate-six-findings",
    )
    review_state = adjudicated["data"]["workflow"]["review"]
    assert review_state["phase"] == "readyToFinalize"
    assert review_state["counts"] == {
        "total": 6,
        "pending": 0,
        "planned": 0,
        "resolved": 0,
        "dismissed": 1,
        "riskAccepted": 5,
    }
    assert review_state["canFinalize"] is True
    assert adjudicated["data"]["project"]["stage"] == "review"

    replayed_decisions = await adjudicate_screenplay_v2_review(
        project_id,
        decisions_request,
        idempotency_key="batch-adjudicate-six-findings",
    )
    assert replayed_decisions["data"]["project"]["revision"] == (
        adjudicated["data"]["project"]["revision"]
    )
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_review_decision_events "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 6}

    finalize_request = FinalizeScreenplayV2ProjectRequest(
        expectedProjectRevision=adjudicated["data"]["project"]["revision"],
        draftRevisionId=draft_id,
        reviewRevisionId=review_id,
    )
    finalized = await finalize_screenplay_v2_project(
        project_id,
        finalize_request,
        idempotency_key="user-finalize-reviewed-project",
    )
    assert finalized["data"]["project"]["stage"] == "completed"
    assert finalized["data"]["workflow"]["review"]["phase"] == "completed"
    assert finalized["data"]["workflow"]["review"]["completionSource"] == "user"

    replayed_finalization = await finalize_screenplay_v2_project(
        project_id,
        finalize_request,
        idempotency_key="user-finalize-reviewed-project",
    )
    assert replayed_finalization["data"]["project"]["revision"] == (
        finalized["data"]["project"]["revision"]
    )
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_finalization_events "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}

    with pytest.raises(AppError, match="项目已经定稿"):
        await adjudicate_screenplay_v2_review(
            project_id,
            AdjudicateScreenplayV2ReviewRequest.model_validate({
                "expectedProjectRevision": finalized["data"]["project"]["revision"],
                "reviewRevisionId": review_id,
                "decisions": [{
                    "issueId": "issue-6",
                    "status": "riskAccepted",
                    "note": "定稿后不能改写裁决快照",
                }],
            }),
            idempotency_key="reject-changing-finalized-decisions",
        )


async def test_planned_review_decision_selects_revision_not_finalization(
    temp_db: DatabaseConnection,
):
    project_id, draft_id, review_id, workspace = await _seed_project_with_review(
        temp_db,
        issues=[
            {
                "id": "arc-1",
                "severity": "critical",
                "description": "人物弧光需要补足",
                "sceneIds": ["scene-1"],
            },
            {
                "id": "pace-1",
                "severity": "major",
                "description": "节奏问题可以保留",
                "sceneIds": ["scene-1"],
            },
        ],
    )
    adjudicated = await adjudicate_screenplay_v2_review(
        project_id,
        AdjudicateScreenplayV2ReviewRequest.model_validate({
            "expectedProjectRevision": workspace["project"]["revision"],
            "reviewRevisionId": review_id,
            "decisions": [
                {"issueId": "arc-1", "status": "planned", "note": "进入修订"},
                {"issueId": "pace-1", "status": "riskAccepted", "note": "保留"},
            ],
        }),
        idempotency_key="plan-selected-review-finding",
    )
    review_state = adjudicated["data"]["workflow"]["review"]
    assert review_state["phase"] == "readyToRevise"
    assert adjudicated["data"]["workflow"]["nextActions"] == [{
        "type": "generateDeliverable",
        "targetRole": "screenplayDraft",
    }]
    assert review_state["canFinalize"] is False

    reloaded = await get_screenplay_v2_workspace(project_id)
    assert {
        finding["id"]: finding["status"]
        for finding in reloaded["data"]["workflow"]["review"]["findings"]
    } == {"arc-1": "planned", "pace-1": "riskAccepted"}
    review_document = await get_screenplay_v2_revision(review_id, view="full")
    document_part = next(
        part for part in review_document["data"]["parts"]
        if part["type"] == "document"
    )
    assert document_part["payload"]["reviewedDraftId"] == draft_id
    assert [issue["id"] for issue in document_part["payload"]["issues"]] == [
        "arc-1", "pace-1",
    ]

    with pytest.raises(AppError, match="还有 1 条审阅意见等待修订"):
        await finalize_screenplay_v2_project(
            project_id,
            FinalizeScreenplayV2ProjectRequest(
                expectedProjectRevision=adjudicated["data"]["project"]["revision"],
                draftRevisionId=draft_id,
                reviewRevisionId=review_id,
            ),
            idempotency_key="reject-finalize-with-planned-finding",
        )


async def test_review_decision_request_rejects_duplicate_issue_ids():
    with pytest.raises(ValueError, match="不能重复"):
        AdjudicateScreenplayV2ReviewRequest.model_validate({
            "expectedProjectRevision": 1,
            "reviewRevisionId": "review-v1",
            "decisions": [
                {"issueId": "issue-1", "status": "resolved", "note": "完成"},
                {"issueId": "issue-1", "status": "dismissed", "note": "误报"},
            ],
        })
