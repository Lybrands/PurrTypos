from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import (
    StepExecutor,
    StepType,
    TaskPlan,
    TaskSpec,
    TaskStep,
)
from agent_core.long_tasks import (
    LongTaskCreateCommand,
    LongTaskStatus,
    LongTaskUnitSpec,
)
from agent_core.task_admission import (
    ExecutionMode,
    TaskAdmissionDecision,
)
from agent_core.work_items import WorkItemLifecycle
from agent_core.work_items.contracts import WorkItemCreateCommand
from application.screenplay_long_tasks import ScreenplayLongTaskDispatcher
from database.connection import DatabaseConnection
from domains.screenplay.contracts import SCREENPLAY_DOMAIN_NAMESPACE
from domains.screenplay.task_admission import SCREENPLAY_DRAFT_LONG_TASK_KIND
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


@pytest_asyncio.fixture
async def dispatcher_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _decision() -> TaskAdmissionDecision:
    return TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="screenplay_draft_requires_multiple_runs",
        estimated_units=6,
        estimated_model_calls=2,
        metadata={
            "namespace": SCREENPLAY_DOMAIN_NAMESPACE,
            "kind": SCREENPLAY_DRAFT_LONG_TASK_KIND,
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
            "baseDraftDocumentId": "draft-1",
            "scope": "count",
            "targetSceneIds": [f"s{index:02d}" for index in range(1, 7)],
            "maxParallelism": 2,
            "executionUnits": [
                {
                    "id": "planner-opening",
                    "kind": "scene_generation",
                    "position": 0,
                    "dependsOn": [],
                    "sceneIds": ["s01", "s02", "s03"],
                    "sceneHeadings": ["一", "二", "三"],
                },
                {
                    "id": "planner-closing",
                    "kind": "scene_generation",
                    "position": 1,
                    "dependsOn": ["planner-opening"],
                    "sceneIds": ["s04", "s05", "s06"],
                    "sceneHeadings": ["四", "五", "六"],
                },
                {
                    "id": "planner-finalize",
                    "kind": "finalize",
                    "position": 2,
                    "dependsOn": ["planner-closing"],
                },
            ],
        },
    )


def _plan() -> TaskPlan:
    return TaskPlan(
        title="批量创作剧本正文",
        steps=(TaskStep(
            id="dispatch",
            title="调度",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
        ),),
        task_spec=TaskSpec(
            goal="连续创作六场",
            operation="write",
        ),
    )


@pytest.mark.asyncio
async def test_repeat_dispatch_resumes_same_task_without_creating_duplicate(
    dispatcher_db,
):
    repository = SqliteLongTaskRepository(dispatcher_db)
    work_item_repository = SqliteWorkItemRepository(dispatcher_db)
    work_items = WorkItemLifecycle(
        work_item_repository,
        id_factory=iter(("work-1", "work-2")).__next__,
    )
    dispatcher = ScreenplayLongTaskDispatcher(
        work_items=work_items,
        long_tasks=repository,
        id_factory=iter(("task-1", "task-2")).__next__,
    )

    first = await dispatcher.dispatch(
        object(),
        _plan(),
        _decision(),
        parent_run_id="run-parent-1",
    )
    task = await repository.load(first.task_id)
    assert task is not None
    assert task.max_parallelism == 2
    units = await repository.list_units(first.task_id)
    assert [unit.id for unit in units] == [
        "planner-opening",
        "planner-closing",
        "planner-finalize",
    ]
    assert [unit.dependencies for unit in units] == [
        (),
        ("planner-opening",),
        ("planner-closing",),
    ]
    assert [unit.metadata["unitKind"] for unit in units] == [
        "scene_generation",
        "scene_generation",
        "finalize",
    ]
    task = await repository.start(task.id, expected_revision=task.revision)
    task = await repository.pause(task.id)
    assert task.status is LongTaskStatus.PAUSED

    second = await dispatcher.dispatch(
        object(),
        _plan(),
        _decision(),
        parent_run_id="run-parent-2",
    )

    assert second.task_id == first.task_id
    assert second.metadata["deduplicated"] is True
    assert second.metadata["resumed"] is True
    assert (await repository.load(first.task_id)).status is LongTaskStatus.RUNNING
    tasks = await repository.list_for_owner(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        owner_id="project-1",
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
    )
    assert [item.id for item in tasks] == [first.task_id]
    links = await work_item_repository.list_run_links(task.work_item_id)
    assert [link.run_id for link in links] == [
        "run-parent-1",
        "run-parent-2",
    ]


@pytest.mark.asyncio
async def test_new_session_never_inherits_or_resumes_another_sessions_task(
    dispatcher_db,
):
    repository = SqliteLongTaskRepository(dispatcher_db)
    dispatcher = ScreenplayLongTaskDispatcher(
        work_items=WorkItemLifecycle(
            SqliteWorkItemRepository(dispatcher_db),
            id_factory=iter(("work-1", "work-2")).__next__,
        ),
        long_tasks=repository,
        id_factory=iter(("task-1", "task-2")).__next__,
    )
    first_request = type("Request", (), {"session_id": 101})()
    second_request = type("Request", (), {"session_id": 202})()

    first = await dispatcher.dispatch(
        first_request,
        _plan(),
        _decision(),
        parent_run_id="run-session-101",
    )
    first_task = await repository.load(first.task_id)
    assert first_task is not None
    first_task = await repository.start(
        first_task.id,
        expected_revision=first_task.revision,
    )
    await repository.pause(first_task.id)

    second = await dispatcher.dispatch(
        second_request,
        _plan(),
        _decision(),
        parent_run_id="run-session-202",
    )

    assert second.task_id != first.task_id
    assert second.metadata.get("deduplicated") is not True
    assert (await repository.load(first.task_id)).status is LongTaskStatus.PAUSED
    second_task = await repository.load(second.task_id)
    assert second_task is not None
    assert second_task.metadata["sessionId"] == 202


@pytest.mark.asyncio
async def test_storage_returns_canonical_task_during_dispatch_race(dispatcher_db):
    work_item_repository = SqliteWorkItemRepository(dispatcher_db)
    for work_id, run_id in (("work-1", "run-1"), ("work-2", "run-2")):
        await work_item_repository.create(work_id, WorkItemCreateCommand(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
            owner_id="project-1",
            created_by_run_id=run_id,
        ))
    repository = SqliteLongTaskRepository(dispatcher_db)
    first = await repository.create("task-1", LongTaskCreateCommand(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-1",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))

    raced = await repository.create("task-2", LongTaskCreateCommand(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
        owner_id="project-1",
        work_item_id="work-2",
        created_by_run_id="run-2",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))

    assert raced.id == first.id
    tasks = await repository.list_for_owner(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        owner_id="project-1",
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
    )
    assert [item.id for item in tasks] == ["task-1"]
