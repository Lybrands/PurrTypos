from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from exceptions import AppError
from routers.screenplay_v2 import (
    accept_screenplay_v2_revision,
    archive_screenplay_v2_project,
    cancel_screenplay_v2_operation,
    create_screenplay_v2_project,
    create_screenplay_v2_session,
    create_screenplay_v2_working_copy_from_revision,
    delete_screenplay_v2_project,
    ensure_current_screenplay_v2_session,
    get_screenplay_v2_workspace,
    list_screenplay_v2_projects,
    list_screenplay_v2_sessions,
    list_screenplay_v2_operation_events,
    list_screenplay_v2_revision_history,
    pause_screenplay_v2_operation,
    publish_screenplay_v2_working_copy,
    restore_screenplay_v2_project,
    resume_screenplay_v2_operation,
    start_screenplay_v2_operation,
    update_screenplay_v2_project,
    update_screenplay_v2_working_copy,
)
from schemas.screenplay_v2 import (
    AcceptScreenplayV2RevisionRequest,
    ChangeScreenplayV2ProjectLifecycleRequest,
    CreateScreenplayV2ProjectRequest,
    CreateScreenplayV2WorkingCopyFromRevisionRequest,
    DeleteScreenplayV2ProjectRequest,
    PublishScreenplayV2WorkingCopyRequest,
    StartScreenplayV2OperationRequest,
    UpdateScreenplayV2ProjectRequest,
    UpdateScreenplayV2WorkingCopyRequest,
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

async def test_native_project_lifecycle_blocks_active_operation_and_is_replay_safe(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-lifecycle-project",
    )
    project_id = created["data"]["project"]["id"]
    started = await start_screenplay_v2_operation(
        project_id,
        StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate", "instruction": "生成简报"},
        }),
        idempotency_key="start-lifecycle-operation",
    )
    operation_id = started["data"]["operation"]["id"]

    with pytest.raises(AppError, match="活动 Operation") as active_archive:
        await archive_screenplay_v2_project(
            project_id,
            ChangeScreenplayV2ProjectLifecycleRequest(
                expectedProjectRevision=2,
            ),
            idempotency_key="archive-active-project",
        )
    assert active_archive.value.status_code == 409

    with pytest.raises(AppError, match="活动 Operation") as active_delete:
        await delete_screenplay_v2_project(
            project_id,
            DeleteScreenplayV2ProjectRequest(expectedProjectRevision=2),
            idempotency_key="delete-active-project",
        )
    assert active_delete.value.status_code == 409

    await cancel_screenplay_v2_operation(
        operation_id,
        idempotency_key="cancel-lifecycle-operation",
    )
    archived = await archive_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=2,
        ),
        idempotency_key="archive-project",
    )
    assert archived["data"]["project"]["lifecycle"] == "archived"
    assert archived["data"]["project"]["revision"] == 3

    replay = await archive_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=2,
        ),
        idempotency_key="archive-project",
    )
    assert replay["data"]["project"]["revision"] == 3

    restored = await restore_screenplay_v2_project(
        project_id,
        ChangeScreenplayV2ProjectLifecycleRequest(
            expectedProjectRevision=3,
        ),
        idempotency_key="restore-project",
    )
    assert restored["data"]["project"]["lifecycle"] == "active"
    assert restored["data"]["project"]["revision"] == 4


async def test_native_project_delete_is_cas_guarded_and_replay_safe(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-delete-project",
    )
    project_id = created["data"]["project"]["id"]
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
        "SELECT command_type, project_id FROM screenplay_command_receipts "
        "WHERE command_id = 'delete-native-project'"
    ) == {"command_type": "deleteProject", "project_id": project_id}

    replay = await delete_screenplay_v2_project(
        project_id,
        request,
        idempotency_key="delete-native-project",
    )
    assert replay == deleted


async def test_operation_api_is_idempotent_and_enforces_one_active_target(
    temp_db: DatabaseConnection,
):
    created = await create_screenplay_v2_project(
        _original_request(),
        idempotency_key="create-operation-project",
    )
    project_id = created["data"]["project"]["id"]
    request = StartScreenplayV2OperationRequest.model_validate({
        "expectedProjectRevision": 1,
        "targetRole": "creativeBrief",
        "intent": {
            "type": "generate",
            "scope": {"section": "all"},
            "instruction": "生成第一版创作简报",
        },
        "conversation": {
            "sessionId": 7,
            "userMessageId": "message-1",
        },
    })

    started = await start_screenplay_v2_operation(
        project_id,
        request,
        idempotency_key="start-brief-operation",
    )
    operation = started["data"]["operation"]
    operation_id = operation["id"]
    assert operation["status"] == "queued"
    assert operation["baseProjectRevision"] == 1
    assert operation["intent"]["conversation"] == {
        "sessionId": 7,
        "userMessageId": "message-1",
    }
    assert started["data"]["projectRevision"] == 2
    assert started["data"]["workspace"]["activeOperations"][0]["id"] == (
        operation_id
    )

    replay = await start_screenplay_v2_operation(
        project_id,
        request,
        idempotency_key="start-brief-operation",
    )
    assert replay["data"]["operation"]["id"] == operation_id
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}

    with pytest.raises(AppError, match="Idempotency-Key") as changed_request:
        await start_screenplay_v2_operation(
            project_id,
            StartScreenplayV2OperationRequest.model_validate({
                **request.model_dump(mode="json"),
                "intent": {"type": "regenerate", "instruction": "换一种写法"},
            }),
            idempotency_key="start-brief-operation",
        )
    assert changed_request.value.status_code == 409

    with pytest.raises(AppError, match="同一目标已有") as active_conflict:
        await start_screenplay_v2_operation(
            project_id,
            StartScreenplayV2OperationRequest.model_validate({
                **request.model_dump(mode="json"),
                "expectedProjectRevision": 2,
            }),
            idempotency_key="start-second-brief-operation",
        )
    assert active_conflict.value.status_code == 409

    paused = await pause_screenplay_v2_operation(
        operation_id,
        idempotency_key="pause-brief-operation",
    )
    assert paused["data"]["operation"]["status"] == "paused"
    resumed = await resume_screenplay_v2_operation(
        operation_id,
        idempotency_key="resume-brief-operation",
    )
    assert resumed["data"]["operation"]["status"] == "queued"
    canceled = await cancel_screenplay_v2_operation(
        operation_id,
        idempotency_key="cancel-brief-operation",
    )
    assert canceled["data"]["operation"]["status"] == "canceled"

    events = await list_screenplay_v2_operation_events(
        operation_id,
        after=0,
        limit=10,
    )
    assert [event["sequence"] for event in events["data"]["events"]] == [
        1,
        2,
        3,
        4,
    ]
    assert [event["type"] for event in events["data"]["events"]] == [
        "screenplay.operation.queued",
        "screenplay.operation.paused",
        "screenplay.operation.queued",
        "screenplay.operation.canceled",
    ]


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
