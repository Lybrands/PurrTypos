from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import RunBinding, RunCreateParams, RunLineage
from agent_core.events import AgentEvent, CoreEventType
from agent_core.artifacts import ArtifactCreateCommand, ArtifactScope
from agent_core.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec
from agent_core.work_items import WorkItemCreateCommand
from application.screenplay_v2_proposal_projector import (
    ScreenplayV2ProposalProjector,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from database.crud.screenplay_project_deletion import delete_screenplay_project_data
from database.crud.screenplay_drafts import assemble_draft_document
from database.crud.screenplay_head_projection import (
    get_current_document,
    get_document as get_project_document,
    list_current_documents,
)
from dependencies import clear_db, set_db
from exceptions import AppError
from infrastructure.persistence.run_store import get_run_events
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from agent_core.ports import RunCommit
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from routers.screenplay_v2 import (
    get_screenplay_v2_operation,
    get_screenplay_v2_revision,
    list_screenplay_v2_operation_events,
)
from schemas.screenplay_v2 import (
    CreateScreenplayV2ProjectRequest,
    StartScreenplayV2OperationRequest,
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


async def _project_and_run(db: DatabaseConnection):
    service = ScreenplayV2ProjectService(db)
    workspace = await service.create_project(
        command_id="create-agent-projection-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Agent v2 投影",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "测试"},
        }),
    )
    project_id = workspace["project"]["id"]
    started = await service.start_operation(
        command_id="start-agent-projection-operation",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": workspace["project"]["revision"],
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    session_id = await db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["Agent v2", project_id],
    )
    repository = SqliteRunRepository(
        db,
        event_projector=ScreenplayV2ProposalProjector(db),
    )
    begun = await repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="生成候选版本",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=begun.run_id,
    )
    return project_id, begun.run_id, repository


async def test_native_projection_returns_reference_to_live_transport(
    temp_db: DatabaseConnection,
):
    project_id, run_id, repository = await _project_and_run(temp_db)
    proposal = AgentEvent(
        type="screenplay.document_proposal",
        run_id=run_id,
        payload={
            "kind": "creative_brief",
            "title": "引用式候选",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "brief": {"premise": "正文只属于 Revision"},
            },
            "contentText": "不会进入 SSE 的全文",
            "derivedFromIds": [],
        },
    )

    persisted = await repository.commit(
        run_id,
        RunCommit(events=(proposal,)),
    )

    assert len(persisted) == 1
    assert persisted[0].type == "screenplay.revision_ready"
    assert persisted[0].payload["projectId"] == project_id
    assert "contentJson" not in persisted[0].payload
    assert "contentText" not in persisted[0].payload


async def test_precreated_operation_owns_run_and_candidate_without_revision_bump(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-operation-first-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Operation-first 投影",
            "format": "singleEpisode",
            "source": {"type": "original"},
            "brief": {"approach": "悬疑", "premise": "一次失踪"},
        }),
    )
    project_id = workspace["project"]["id"]
    started = await service.start_operation(
        command_id="start-operation-first-brief",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {
                "type": "generate",
                "instruction": "生成可供确认的创作简报",
            },
        }),
    )
    operation_id = started["operation"]["id"]
    assert started["projectRevision"] == 2

    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["Operation-first", project_id],
    )
    run_repository = SqliteRunRepository(
        temp_db,
        event_projector=ScreenplayV2ProposalProjector(temp_db),
    )
    begun = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="生成创作简报",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=begun.run_id,
    )
    await run_repository.append_event(begun.run_id, AgentEvent(
        type="screenplay.document_proposal",
        run_id=begun.run_id,
        payload={
            "kind": "creative_brief",
            "title": "创作简报候选",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "logline": "她必须在天亮前找到失踪者。",
            },
            "contentText": "# 创作简报\n\n她必须在天亮前找到失踪者。",
            "derivedFromIds": [],
        },
    ))

    operation = await service.get_operation(operation_id)
    assert operation["status"] == "succeeded"
    assert operation["resultRevisionId"]
    assert await temp_db.fetch_one(
        "SELECT binding_namespace, binding_aggregate_id, binding_command_id "
        "FROM ai_agent_runs WHERE id = ?",
        [begun.run_id],
    ) == {
        "binding_namespace": "screenplay.operation",
        "binding_aggregate_id": project_id,
        "binding_command_id": operation_id,
    }
    assert await temp_db.fetch_one(
        "SELECT operation_id FROM screenplay_revisions WHERE id = ?",
        [operation["resultRevisionId"]],
    ) == {"operation_id": operation_id}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT revision FROM screenplay_projects WHERE id = ?",
        [project_id],
    ) == {"revision": 2}
    projected_workspace = await service.get_workspace(project_id)
    projected_candidate = next(
        candidate for candidate in projected_workspace["candidates"]
        if candidate["id"] == operation["resultRevisionId"]
    )
    assert projected_candidate["operationId"] == operation_id
    assert projected_candidate["rootRunId"] == begun.run_id
    assert projected_candidate["finalizingRunId"] == begun.run_id
    events = await service.list_operation_events(
        operation_id,
        after=0,
        limit=10,
    )
    assert [event["type"] for event in events["events"]] == [
        "screenplay.operation.queued",
        "screenplay.operation.started",
        "screenplay.candidate.ready",
        "screenplay.operation.succeeded",
    ]


async def test_continued_artifact_keeps_historical_run_operation_ownership(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-cross-operation-artifact-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "跨 Operation 接续 Artifact",
            "format": "singleEpisode",
            "source": {"type": "original"},
            "brief": {"approach": "悬疑", "premise": "接续测试"},
        }),
    )
    project_id = workspace["project"]["id"]
    first_operation = await service.start_operation(
        command_id="start-first-artifact-operation",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    first_operation_id = first_operation["operation"]["id"]
    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["跨 Operation 接续", project_id],
    )
    run_repository = SqliteRunRepository(
        temp_db,
        event_projector=ScreenplayV2ProposalProjector(temp_db),
    )
    first_run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="开始生成",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=first_operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )

    work_item_id = "cross-operation-work-item"
    artifact_id = "cross-operation-artifact"
    await SqliteWorkItemRepository(temp_db).create(
        work_item_id,
        WorkItemCreateCommand(
            namespace="purrtypos.screenplay",
            kind="creative_brief_batches",
            owner_id=project_id,
            created_by_run_id=first_run.run_id,
        ),
    )
    artifact = await SqliteArtifactRepository(temp_db).create(
        artifact_id,
        ArtifactCreateCommand(
            namespace="purrtypos.screenplay",
            kind="creative_brief_batches",
            owner_id=project_id,
            run_id=first_run.run_id,
            scope=ArtifactScope.WORK_ITEM,
            work_item_id=work_item_id,
            created_by_run_id=first_run.run_id,
        ),
    )
    artifact_ref = (
        "artifact://purrtypos.screenplay/creative_brief_batches/"
        f"{artifact.id}"
    )
    await temp_db.execute(
        "UPDATE ai_agent_artifacts SET status = 'finalized', "
        "resource_ref = ? WHERE id = ?",
        [artifact_ref, artifact.id],
    )
    await temp_db.execute(
        "UPDATE ai_agent_work_items SET status = 'completed' WHERE id = ?",
        [work_item_id],
    )
    await service.activate_bound_run(
        operation_id=first_operation_id,
        project_id=project_id,
        run_id=first_run.run_id,
    )
    await service.settle_operation_from_root_run(
        operation_id=first_operation_id,
        project_id=project_id,
        run_id=first_run.run_id,
        run_status="failed",
        error="first attempt failed",
    )

    second_operation = await service.start_operation(
        command_id="start-continuation-artifact-operation",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 2,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    second_operation_id = second_operation["operation"]["id"]
    second_run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="接续生成",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=second_operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=second_operation_id,
        project_id=project_id,
        run_id=second_run.run_id,
    )
    await run_repository.append_event(second_run.run_id, AgentEvent(
        type="screenplay.document_proposal",
        run_id=second_run.run_id,
        payload={
            "kind": "creative_brief",
            "title": "接续完成的创作简报",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "artifactRef": artifact_ref,
                "logline": "共享 Artifact 在后续 Operation 中完成。",
            },
            "contentText": "共享 Artifact 在后续 Operation 中完成。",
            "derivedFromIds": [],
        },
    ))

    run_bindings = await temp_db.fetch_all(
        "SELECT id, binding_command_id FROM ai_agent_runs "
        "WHERE id IN (?, ?) ORDER BY id",
        [first_run.run_id, second_run.run_id],
    )
    assert {row["binding_command_id"] for row in run_bindings} == {
        first_operation_id,
        second_operation_id,
    }
    assert await temp_db.fetch_one(
        "SELECT result_ref FROM ai_agent_artifact_projections "
        "WHERE artifact_id = ?",
        [artifact.id],
    ) == {
        "result_ref": "screenplay-revision://"
        + (await service.get_operation(second_operation_id))["resultRevisionId"],
    }
    assert (await service.get_operation(second_operation_id))["status"] == (
        "succeeded"
    )


async def test_operation_candidate_keeps_captured_upstream_head(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-captured-head-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "固定上游基线",
            "format": "series",
            "source": {"type": "original"},
        }),
    )
    project_id = workspace["project"]["id"]
    brief = await temp_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = ? AND role = 'creativeBrief'",
        [project_id],
    )
    assert brief is not None
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('brief-base', ?, ?, 1, 'brief-base-digest', 'user')",
        [project_id, brief["id"]],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, 'brief-base')",
        [project_id, brief["id"]],
    )

    started = await service.start_operation(
        command_id="start-captured-head-structure",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "structure",
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    assert started["operation"]["baseHeads"]["creativeBrief"] == "brief-base"

    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('brief-new', ?, ?, 2, 'brief-new-digest', 'user')",
        [project_id, brief["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_project_heads SET revision_id = 'brief-new' "
        "WHERE project_id = ? AND deliverable_id = ?",
        [project_id, brief["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_projects SET revision = 3 WHERE id = ?",
        [project_id],
    )

    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["固定基线", project_id],
    )
    run_repository = SqliteRunRepository(
        temp_db,
        event_projector=ScreenplayV2ProposalProjector(temp_db),
    )
    begun = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="生成结构",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=begun.run_id,
    )
    await run_repository.append_event(begun.run_id, AgentEvent(
        type="screenplay.document_proposal",
        run_id=begun.run_id,
        payload={
            "kind": "episode_outline",
            "title": "结构候选",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "episode_outline",
                "episodes": [{"number": 1, "title": "第一集"}],
            },
            "contentText": "第一集",
            "derivedFromIds": [],
        },
    ))
    operation = await service.get_operation(operation_id)
    assert await temp_db.fetch_one(
        "SELECT input_revision_id FROM screenplay_revision_inputs "
        "WHERE revision_id = ? AND input_role = 'creativeBrief'",
        [operation["resultRevisionId"]],
    ) == {"input_revision_id": "brief-base"}
    assert await temp_db.fetch_one(
        "SELECT revision FROM screenplay_projects WHERE id = ?",
        [project_id],
    ) == {"revision": 3}
    current_workspace = await service.get_workspace(project_id)
    candidate = next(
        item for item in current_workspace["candidates"]
        if item["id"] == operation["resultRevisionId"]
    )
    assert candidate["applicability"] == "stale"


async def test_operation_control_checkpoints_the_entire_runtime_tree(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-operation-runtime-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Operation 运行树",
            "format": "series",
            "source": {"type": "original"},
        }),
    )
    project_id = workspace["project"]["id"]
    started = await service.start_operation(
        command_id="start-operation-runtime",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["Operation 运行树", project_id],
    )
    run_repository = SqliteRunRepository(temp_db)
    first_run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="开始",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=first_run.run_id,
    )

    await SqliteWorkItemRepository(temp_db).create(
        "operation-work",
        WorkItemCreateCommand(
            namespace="purrtypos.screenplay",
            kind="creative_brief",
            owner_id=project_id,
            created_by_run_id=first_run.run_id,
        ),
    )
    long_tasks = SqliteLongTaskRepository(temp_db)
    task = await long_tasks.create(
        "operation-long-task",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="creative_brief",
            owner_id=project_id,
            work_item_id="operation-work",
            created_by_run_id=first_run.run_id,
            units=(LongTaskUnitSpec(id="unit-1", position=0),),
        ),
    )
    task = await long_tasks.start(task.id, expected_revision=task.revision)
    claimed = await long_tasks.claim_ready_unit(
        task.id,
        worker_id="operation-worker",
        lease_duration_ms=300_000,
    )
    assert claimed is not None
    await SqliteArtifactRepository(temp_db).create(
        "operation-artifact",
        ArtifactCreateCommand(
            namespace="purrtypos.screenplay",
            kind="creative_brief",
            owner_id=project_id,
            run_id=first_run.run_id,
            scope=ArtifactScope.WORK_ITEM,
            work_item_id="operation-work",
            created_by_run_id=first_run.run_id,
        ),
    )
    paused = await service.control_operation(
        command_id="pause-operation-runtime",
        operation_id=operation_id,
        action="pause",
    )
    assert paused["operation"]["status"] == "paused"
    assert (await long_tasks.load(task.id)).status.value == "paused"
    paused_unit = (await long_tasks.list_units(task.id))[0]
    assert paused_unit.status.value == "pending"
    assert paused_unit.worker_id is None
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_agent_artifacts WHERE id = 'operation-artifact'"
    ) == {"status": "open"}
    assert (await temp_db.fetch_one(
        "SELECT cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
        [first_run.run_id],
    ))["cancel_requested_at_ms"] is not None

    resumed = await service.control_operation(
        command_id="resume-operation-runtime",
        operation_id=operation_id,
        action="resume",
    )
    assert resumed["operation"]["status"] == "queued"
    second_run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="继续",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=second_run.run_id,
    )
    assert (await service.get_operation(operation_id))["status"] == "running"

    canceled = await service.control_operation(
        command_id="cancel-operation-runtime",
        operation_id=operation_id,
        action="cancel",
    )
    assert canceled["operation"]["status"] == "canceled"
    assert (await long_tasks.load(task.id)).status.value == "canceled"
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_agent_work_items WHERE id = 'operation-work'"
    ) == {"status": "canceled"}
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_agent_artifacts WHERE id = 'operation-artifact'"
    ) == {"status": "aborted"}


async def test_terminal_root_reconciliation_pauses_operation_and_runtime_tree(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-orphan-reconciliation-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "失联 Run 恢复",
            "format": "series",
            "source": {"type": "original"},
        }),
    )
    project_id = workspace["project"]["id"]
    started = await service.start_operation(
        command_id="start-orphan-reconciliation-operation",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["失联 Run 恢复", project_id],
    )
    run_repository = SqliteRunRepository(temp_db)
    root_run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="连续创作",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=root_run.run_id,
    )
    await SqliteWorkItemRepository(temp_db).create(
        "orphan-reconciliation-work",
        WorkItemCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay_draft_generation",
            owner_id=project_id,
            created_by_run_id=root_run.run_id,
        ),
    )
    long_tasks = SqliteLongTaskRepository(temp_db)
    task = await long_tasks.create(
        "orphan-reconciliation-task",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay_draft_generation",
            owner_id=project_id,
            work_item_id="orphan-reconciliation-work",
            created_by_run_id=root_run.run_id,
            units=(LongTaskUnitSpec(id="review", position=0),),
        ),
    )
    task = await long_tasks.start(task.id, expected_revision=task.revision)
    claimed = await long_tasks.claim_ready_unit(
        task.id,
        worker_id="lost-worker",
        lease_duration_ms=300_000,
    )
    assert claimed is not None
    await long_tasks.interrupt_unit(
        task.id,
        "review",
        worker_id="lost-worker",
        reason_code="execution_interrupted",
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled', "
        "execution_owner_id = NULL, lease_expires_at_ms = NULL "
        "WHERE id = ?",
        [root_run.run_id],
    )

    repository = SqliteScreenplayV2Repository(temp_db)
    assert await repository.settle_terminal_root_operations() == (operation_id,)
    assert (await service.get_operation(operation_id))["status"] == "paused"
    assert (await long_tasks.load(task.id)).status.value == "paused"
    pending = (await long_tasks.list_units(task.id))[0]
    assert pending.status.value == "pending"
    assert pending.worker_id is None
    events = await service.list_operation_events(
        operation_id,
        after=0,
        limit=10,
    )
    assert [event["type"] for event in events["events"]] == [
        "screenplay.operation.queued",
        "screenplay.operation.started",
        "screenplay.operation.paused",
    ]
    assert await repository.settle_terminal_root_operations() == ()


async def test_root_run_completion_without_candidate_fails_operation(
    temp_db: DatabaseConnection,
):
    service = ScreenplayV2ProjectService(temp_db)
    workspace = await service.create_project(
        command_id="create-no-candidate-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "无候选终态",
            "format": "singleEpisode",
            "source": {"type": "original"},
        }),
    )
    project_id = workspace["project"]["id"]
    started = await service.start_operation(
        command_id="start-no-candidate-operation",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": 1,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions "
        "(title, scope, screenplay_project_id) VALUES (?, 'screenplay', ?)",
        ["无候选终态", project_id],
    )
    run_repository = SqliteRunRepository(temp_db)
    run = await run_repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt="只回复文本",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=run.run_id,
    )

    failed = await service.settle_operation_from_root_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=run.run_id,
        run_status="done",
        error=None,
    )
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "candidate_not_produced"
    replay = await service.settle_operation_from_root_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=run.run_id,
        run_status="done",
        error=None,
    )
    assert replay["status"] == "failed"
    events = await service.list_operation_events(
        operation_id,
        after=0,
        limit=10,
    )
    assert [event["type"] for event in events["events"]] == [
        "screenplay.operation.queued",
        "screenplay.operation.started",
        "screenplay.operation.failed",
    ]


async def _proposal_and_accept(
    db: DatabaseConnection,
    repository: SqliteRunRepository,
    *,
    session_id: str,
    project_id: str,
    kind: str,
    content_json: dict,
    content_text: str = "",
) -> str:
    role_by_kind = {
        "creative_brief": "creativeBrief",
        "episode_outline": "structure",
        "scene_list": "sceneList",
        "scene_draft": "screenplayDraft",
        "review": "review",
    }
    project = await db.fetch_one(
        "SELECT revision FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    service = ScreenplayV2ProjectService(db)
    started = await service.start_operation(
        command_id=f"start:{kind}:{project['revision']}",
        project_id=project_id,
        request=StartScreenplayV2OperationRequest.model_validate({
            "expectedProjectRevision": int(project["revision"]),
            "targetRole": role_by_kind[kind],
            "intent": {"type": "generate"},
        }),
    )
    operation_id = started["operation"]["id"]
    begun = await repository.begin(
        RunCreateParams(
            session_id=session_id,
            prompt=f"生成 {kind}",
            mode="agent",
            binding=RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await service.activate_bound_run(
        operation_id=operation_id,
        project_id=project_id,
        run_id=begun.run_id,
    )
    await repository.append_event(begun.run_id, AgentEvent(
        type="screenplay.document_proposal",
        run_id=begun.run_id,
        payload={
            "kind": kind,
            "title": kind,
            "contentJson": content_json,
            "contentText": content_text,
            "derivedFromIds": [],
        },
    ))
    operation = await db.fetch_one(
        "SELECT result_revision_id FROM screenplay_operations WHERE id = ?",
        [operation_id],
    )
    assert operation and operation["result_revision_id"]
    project = await db.fetch_one(
        "SELECT revision FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    revision_id = str(operation["result_revision_id"])
    command_id = f"accept:{revision_id}"
    await SqliteScreenplayV2Repository(db).accept_revision(
        command_id=command_id,
        request_digest=command_id,
        project_id=project_id,
        revision_id=revision_id,
        expected_project_revision=int(project["revision"]),
        confirm_invalidation=False,
    )
    return revision_id


async def test_agent_proposal_atomically_creates_one_replay_safe_candidate(
    temp_db: DatabaseConnection,
):
    project_id, run_id, repository = await _project_and_run(temp_db)
    await temp_db.execute(
        "INSERT INTO screenplay_source_receipts "
        "(project_id, agent_run_id, tool_name, source_type, source_id, "
        "source_revision, coverage_mode, excerpt) "
        "VALUES (?, ?, 'readSourcePassages', 'chapter', 'chapter-1', "
        "'sha256:chapter-1-v1', 'full', '证据摘录')",
        [project_id, run_id],
    )
    proposal = AgentEvent(
        type="screenplay.document_proposal",
        run_id=run_id,
        payload={
            "kind": "creative_brief",
            "title": "创作简报",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "logline": "两集测试故事",
            },
            "contentText": "两集测试故事",
            "derivedFromIds": [],
        },
    )

    await repository.append_event(run_id, proposal)
    await repository.append_event(run_id, proposal)

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}
    operation = await temp_db.fetch_one(
        "SELECT status, result_revision_id FROM screenplay_operations "
        "WHERE project_id = ?",
        [project_id],
    )
    assert operation and operation["status"] == "succeeded"
    assert operation["result_revision_id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operation_events"
    ) == {"count": 4}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_parts "
        "WHERE revision_id = ?",
        [operation["result_revision_id"]],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_source_refs "
        "WHERE revision_id = ?",
        [operation["result_revision_id"]],
    ) == {"count": 1}
    project = await temp_db.fetch_one(
        "SELECT revision FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project == {"revision": 2}
    events = await get_run_events(temp_db, run_id)
    reference_events = [
        row for row in events
        if row["eventType"] == "screenplay.revision_ready"
    ]
    assert len(reference_events) == 2
    assert all(
        row["payload"]["revisionId"] == operation["result_revision_id"]
        for row in reference_events
    )
    assert all(
        "contentJson" not in row["payload"]
        and "contentText" not in row["payload"]
        for row in reference_events
    )

    workspace = await ScreenplayV2ProjectService(temp_db).get_workspace(
        project_id
    )
    assert workspace["candidates"][0]["id"] == operation["result_revision_id"]
    assert workspace["candidates"][0]["role"] == "creativeBrief"

    revision_response = await get_screenplay_v2_revision(
        operation["result_revision_id"],
        view="full",
    )
    revision = revision_response["data"]
    assert revision["operationId"]
    assert revision["finalizingRunId"] == run_id
    assert [part["key"] for part in revision["parts"]] == [
        "main",
    ]
    assert revision["sources"] == [{
        "type": "chapter",
        "id": "chapter-1",
        "revision": "sha256:chapter-1-v1",
        "excerpt": "证据摘录",
    }]

    operation_response = await get_screenplay_v2_operation(
        revision["operationId"]
    )
    assert operation_response["data"]["status"] == "succeeded"
    event_response = await list_screenplay_v2_operation_events(
        revision["operationId"],
        after=1,
        limit=10,
    )
    assert [item["sequence"] for item in event_response["data"]["events"]] == [
        2,
        3,
        4,
    ]
    assert event_response["data"]["nextAfter"] == 4


async def test_proposal_event_and_candidate_roll_back_together_on_bad_runtime_ref(
    temp_db: DatabaseConnection,
):
    project_id, run_id, repository = await _project_and_run(temp_db)
    bad_proposal = AgentEvent(
        type="screenplay.document_proposal",
        run_id=run_id,
        payload={
            "kind": "creative_brief",
            "title": "坏引用",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "artifactRef": "agent-artifact://missing-artifact",
            },
            "contentText": "不会被提交",
            "derivedFromIds": [],
        },
    )

    with pytest.raises(AppError, match="Artifact 不存在"):
        await repository.append_event(run_id, bad_proposal)

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT status, result_revision_id FROM screenplay_operations "
        "WHERE project_id = ?",
        [project_id],
    ) == {"status": "running", "result_revision_id": None}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}
    events = await get_run_events(temp_db, run_id)
    assert [row["eventType"] for row in events] == [CoreEventType.RUN_STARTED]


async def test_long_task_proposal_links_runtime_tree_and_reuses_one_operation(
    temp_db: DatabaseConnection,
):
    project_id, parent_run_id, repository = await _project_and_run(temp_db)
    child = await repository.begin(
        RunCreateParams(
            session_id=None,
            prompt="批量正文子任务",
            mode="agent",
            lineage=RunLineage(
                parent_run_id=parent_run_id,
                root_run_id=parent_run_id,
                delegation_id=None,
                agent_role="screenplay_writer",
                depth=1,
            ),
        ),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id, status) "
        "VALUES ('work-v2-long', 'purrtypos.screenplay', 'draft', ?, ?, "
        "'completed')",
        [project_id, parent_run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, completed_units, max_parallelism) "
        "VALUES ('long-v2', 'work-v2-long', 'purrtypos.screenplay', 'draft', "
        "?, ?, 'completed', 1, 1, 1)",
        [project_id, parent_run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, position, status, dependencies_json, run_id) "
        "VALUES ('long-v2', 'unit-1', 0, 'completed', '[]', ?)",
        [child.run_id],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_source_receipts "
        "(project_id, agent_run_id, tool_name, source_type, source_id, "
        "source_revision, coverage_mode) "
        "VALUES (?, ?, 'readSourcePassages', 'chapter', 'chapter-long', "
        "'sha256:long-v1', 'full')",
        [project_id, child.run_id],
    )
    event = AgentEvent(
        type="screenplay.document_proposal",
        run_id=parent_run_id,
        payload={
            "kind": "creative_brief",
            "title": "长任务简报",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "longTaskId": "long-v2",
                "logline": "长任务生成的创作简报",
            },
            "contentText": "长任务生成的创作简报",
            "derivedFromIds": [],
        },
    )
    await repository.append_event(parent_run_id, event)
    operation = await temp_db.fetch_one(
        "SELECT id, result_revision_id FROM screenplay_operations "
        "WHERE project_id = ?",
        [project_id],
    )
    assert operation
    operation_id = operation["id"]
    run_links = await temp_db.fetch_all(
        "SELECT id, binding_command_id, root_run_id FROM ai_agent_runs "
        "WHERE id IN (?, ?) ORDER BY id ASC",
        [parent_run_id, child.run_id],
    )
    root = next(row for row in run_links if row["id"] == parent_run_id)
    child_link = next(row for row in run_links if row["id"] == child.run_id)
    assert root["binding_command_id"] == operation_id
    assert child_link["binding_command_id"] is None
    assert child_link["root_run_id"] == parent_run_id
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revision_source_refs "
        "WHERE revision_id = ? AND source_id = 'chapter-long'",
        [operation["result_revision_id"]],
    ) == {"count": 1}

    await repository.append_event(parent_run_id, event)
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE project_id = ?",
        [project_id],
    ) == {"count": 1}


async def test_accepted_heads_drive_next_stage_and_draft_revisions_are_snapshots(
    temp_db: DatabaseConnection,
):
    project_id, initial_run_id, repository = await _project_and_run(temp_db)
    initial_binding = await temp_db.fetch_one(
        "SELECT binding_command_id FROM ai_agent_runs WHERE id = ?",
        [initial_run_id],
    )
    await ScreenplayV2ProjectService(temp_db).control_operation(
        command_id="cancel-initial-setup-operation",
        operation_id=str(initial_binding["binding_command_id"]),
        action="cancel",
    )
    session = await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE screenplay_project_id = ?",
        [project_id],
    )
    session_id = str(session["id"])

    brief_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="creative_brief",
        content_json={
            "schemaVersion": 1,
            "documentKind": "creative_brief",
            "logline": "两集测试故事",
        },
    )
    assert (await get_current_document(
        temp_db,
        project_id,
        kind="creative_brief",
    ))["id"] == brief_id

    structure_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="episode_outline",
        content_json={
            "schemaVersion": 1,
            "documentKind": "episode_outline",
            "episodes": [
                {"number": 1, "id": "episode-1", "title": "第一集"},
                {"number": 2, "id": "episode-2", "title": "第二集"},
            ],
        },
    )
    assert [row["part_key"] for row in await temp_db.fetch_all(
        "SELECT part_key FROM screenplay_revision_parts "
        "WHERE revision_id = ? ORDER BY position ASC",
        [structure_id],
    )] == ["main", "1", "2"]
    scene_list_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="scene_list",
        content_json={
            "schemaVersion": 1,
            "documentKind": "scene_list",
            "scenes": [
                {"id": "scene-1", "episodeNumber": 1, "heading": "第一场"},
                {"id": "scene-2", "episodeNumber": 2, "heading": "第二场"},
            ],
        },
    )
    first_draft_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="scene_draft",
        content_json={
            "schemaVersion": 1,
            "documentKind": "scene_draft",
            "sceneListId": scene_list_id,
            "completedSceneIds": ["scene-1"],
            "episodeDrafts": [{
                "episodeNumber": 1,
                "sceneIds": ["scene-1"],
                "sceneTexts": [{
                    "sceneId": "scene-1",
                    "contentText": "第一集正文",
                }],
                "sceneExecutions": [{"sceneId": "scene-1"}],
                "contentText": "第一集正文",
            }],
        },
        content_text="第一集正文",
    )

    second_draft_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="scene_draft",
        content_json={
            "schemaVersion": 1,
            "documentKind": "scene_draft",
            "sceneListId": scene_list_id,
            "completedSceneIds": ["scene-1", "scene-2"],
            "isComplete": True,
            "episodeDrafts": [{
                "episodeNumber": 2,
                "sceneIds": ["scene-2"],
                "sceneTexts": [{
                    "sceneId": "scene-2",
                    "contentText": "第二集正文",
                }],
                "sceneExecutions": [{"sceneId": "scene-2"}],
                "contentText": "第二集正文",
            }],
        },
        content_text="第二集正文",
    )

    parts = await temp_db.fetch_all(
        "SELECT part_key, payload_json, content_text "
        "FROM screenplay_revision_parts WHERE revision_id = ? "
        "ORDER BY position ASC",
        [second_draft_id],
    )
    assert [part["part_key"] for part in parts] == ["main", "1", "2"]
    assert parts[1]["content_text"] == "第一集正文"
    assert parts[2]["content_text"] == "第二集正文"

    current = await get_current_document(
        temp_db,
        project_id,
        kind="scene_draft",
    )
    assert current and current["id"] == second_draft_id
    assembled = await assemble_draft_document(
        temp_db,
        current,
        include_text=True,
    )
    assert assembled["content_json"]["completedSceneIds"] == [
        "scene-1",
        "scene-2",
    ]
    assert assembled["content_text"] == "第一集正文\n\n第二集正文"
    historical = await get_project_document(
        temp_db,
        project_id,
        first_draft_id,
    )
    assert historical and historical["status"] == "accepted"

    review_id = await _proposal_and_accept(
        temp_db,
        repository,
        session_id=session_id,
        project_id=project_id,
        kind="review",
        content_json={
            "schemaVersion": 1,
            "documentKind": "review",
            "reviewedDraftId": second_draft_id,
            "verdict": "revise",
            "issues": [{
                "id": "issue-across-episodes",
                "sceneIds": ["scene-1", "scene-2"],
                "problem": "两集衔接需要加强",
            }],
        },
    )
    review_parts = await temp_db.fetch_all(
        "SELECT part_key, payload_json FROM screenplay_revision_parts "
        "WHERE revision_id = ? ORDER BY position ASC",
        [review_id],
    )
    assert [part["part_key"] for part in review_parts] == ["main", "1", "2"]
    assert [
        json.loads(part["payload_json"])["issues"][0]["id"]
        for part in review_parts[1:]
    ] == ["issue-across-episodes", "issue-across-episodes"]

    projected_documents = await list_current_documents(temp_db, project_id)
    assert {document["id"] for document in projected_documents} == {
        brief_id,
        structure_id,
        scene_list_id,
        second_draft_id,
        review_id,
    }
    assert {document["status"] for document in projected_documents} == {
        "accepted"
    }
    projected_structure = next(
        document
        for document in projected_documents
        if document["id"] == structure_id
    )
    assert projected_structure["derived_from_ids"] == [brief_id]
    projected_draft = await get_project_document(
        temp_db,
        project_id,
        second_draft_id,
    )
    assert projected_draft and projected_draft["id"] == second_draft_id
    assert projected_draft["storage_model"] == "revision_parts"


async def test_deleting_v2_project_cascades_aggregate_but_preserves_run_audit(
    temp_db: DatabaseConnection,
):
    project_id, run_id, repository = await _project_and_run(temp_db)
    await repository.append_event(run_id, AgentEvent(
        type="screenplay.document_proposal",
        run_id=run_id,
        payload={
            "kind": "creative_brief",
            "title": "待删除候选",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "brief": {"premise": "待删除"},
            },
            "contentText": "待删除",
            "derivedFromIds": [],
        },
    ))
    bound_run = await temp_db.fetch_one(
        "SELECT binding_command_id FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    operation_id = str(bound_run["binding_command_id"])

    assert await delete_screenplay_project_data(temp_db, project_id) is True
    for table in (
        "screenplay_deliverables",
        "screenplay_revisions",
        "screenplay_revision_parts",
        "screenplay_operations",
        "screenplay_operation_events",
        "screenplay_command_receipts",
        "screenplay_outbox_events",
    ):
        assert await temp_db.fetch_one(
            f"SELECT COUNT(*) AS count FROM {table}"
        ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT session_id, binding_namespace, binding_aggregate_id, "
        "binding_command_id FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {
        "session_id": None,
        "binding_namespace": "screenplay.operation",
        "binding_aggregate_id": project_id,
        "binding_command_id": operation_id,
    }
