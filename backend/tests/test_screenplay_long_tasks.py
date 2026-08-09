from __future__ import annotations

import asyncio
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
    LongTaskCoordinator,
    LongTaskCreateCommand,
    LongTaskStatus,
    LongTaskUnitStatus,
    LongTaskUnitResult,
    LongTaskUnitSpec,
)
from agent_core.task_admission import (
    ExecutionMode,
    TaskAdmissionDecision,
)
from agent_core.work_items import WorkItemLifecycle
from agent_core.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemTransitionCommand,
)
from application.screenplay_long_tasks import ScreenplayLongTaskDispatcher
from database.connection import DatabaseConnection
from domains.screenplay.contracts import SCREENPLAY_DOMAIN_NAMESPACE
from domains.screenplay.task_admission import SCREENPLAY_DRAFT_LONG_TASK_KIND
from domains.screenplay.workflow_compiler import build_screenplay_draft_workflow
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


def _decision(
    *,
    scene_list_document_id: str = "scene-list-1",
    workflow_ids: tuple[str, str, str] = (
        "write-001",
        "review-001",
        "finalize",
    ),
) -> TaskAdmissionDecision:
    write_id, review_id, finalize_id = workflow_ids
    return TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="screenplay_draft_requires_multiple_runs",
        estimated_units=6,
        estimated_model_calls=2,
        covered_step_ids=("dispatch",),
        metadata={
            "namespace": SCREENPLAY_DOMAIN_NAMESPACE,
            "kind": SCREENPLAY_DRAFT_LONG_TASK_KIND,
            "projectId": "project-1",
            "sceneListDocumentId": scene_list_document_id,
            "baseDraftDocumentId": "draft-1",
            "scope": "count",
            "targetSceneIds": [f"s{index:02d}" for index in range(1, 7)],
            "maxParallelism": 1,
            "workflowUnits": [
                {
                    "id": write_id,
                    "kind": "scene_generation",
                    "position": 0,
                    "dependsOn": [],
                    "sceneIds": [f"s{index:02d}" for index in range(1, 7)],
                    "sceneHeadings": ["一", "二", "三", "四", "五", "六"],
                },
                {
                    "id": review_id,
                    "kind": "continuity_review",
                    "position": 1,
                    "dependsOn": [write_id],
                    "sceneIds": [f"s{index:02d}" for index in range(1, 7)],
                    "sceneHeadings": ["一", "二", "三", "四", "五", "六"],
                },
                {
                    "id": finalize_id,
                    "kind": "finalize",
                    "position": 2,
                    "dependsOn": [review_id],
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
async def test_three_episode_draft_runs_writers_then_global_review_then_revisions(
    dispatcher_db,
):
    scenes = [
        {
            "id": f"s{episode:02d}-01",
            "heading": f"第 {episode} 集",
            "episodeNumber": episode,
        }
        for episode in range(5, 8)
    ]
    workflow = build_screenplay_draft_workflow(scenes)
    decision = TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="screenplay_draft_requires_multiple_runs",
        estimated_units=3,
        estimated_model_calls=workflow.model_call_count,
        covered_step_ids=("dispatch",),
        metadata={
            "namespace": SCREENPLAY_DOMAIN_NAMESPACE,
            "kind": SCREENPLAY_DRAFT_LONG_TASK_KIND,
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
            "baseDraftDocumentId": "draft-1",
            "scope": "next_3_episodes",
            "targetSceneIds": [scene["id"] for scene in scenes],
            "maxParallelism": workflow.max_parallelism,
            "workflowUnits": workflow.to_metadata(),
        },
    )
    repository = SqliteLongTaskRepository(dispatcher_db)
    dispatcher = ScreenplayLongTaskDispatcher(
        work_items=WorkItemLifecycle(
            SqliteWorkItemRepository(dispatcher_db),
            id_factory=lambda: "work-parallel",
        ),
        long_tasks=repository,
        id_factory=lambda: "task-parallel",
    )
    receipt = await dispatcher.dispatch(
        object(),
        _plan(),
        decision,
        parent_run_id="run-parent",
    )
    task = await repository.load(receipt.task_id)
    assert task is not None
    assert task.max_parallelism == 3

    releases = {"write": asyncio.Event(), "revise": asyncio.Event()}

    class _Runner:
        def __init__(self):
            self.active = {"write": 0, "revise": 0}
            self.peak = {"write": 0, "revise": 0}
            self.calls: list[str] = []

        async def run_unit(self, task, unit, signal=None):
            del task, signal
            self.calls.append(unit.id)
            phase = (
                "write" if unit.id.startswith("write_")
                else "revise" if unit.id.startswith("revise_")
                else None
            )
            if phase is not None:
                self.active[phase] += 1
                self.peak[phase] = max(
                    self.peak[phase],
                    self.active[phase],
                )
                if self.active[phase] == 3:
                    releases[phase].set()
                await asyncio.wait_for(releases[phase].wait(), timeout=1)
                self.active[phase] -= 1
            return LongTaskUnitResult(output_ref=f"artifact://{unit.id}")

    runner = _Runner()
    completed = await LongTaskCoordinator(
        repository,
        worker_id="screenplay-parallel",
    ).run(receipt.task_id, runner)

    assert completed.status is LongTaskStatus.COMPLETED
    assert runner.peak == {"write": 3, "revise": 3}
    assert set(runner.calls[:3]) == {
        "write_s05-01",
        "write_s06-01",
        "write_s07-01",
    }
    assert runner.calls[3] == "review_draft_continuity"
    assert set(runner.calls[4:7]) == {
        "revise_s05-01",
        "revise_s06-01",
        "revise_s07-01",
    }
    assert runner.calls[-1] == "propose_draft"


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
    assert task.max_parallelism == 1
    units = await repository.list_units(first.task_id)
    assert [unit.id for unit in units] == [
        "write-001",
        "review-001",
        "finalize",
    ]
    assert [unit.dependencies for unit in units] == [
        (),
        ("write-001",),
        ("review-001",),
    ]
    assert [unit.metadata["unitKind"] for unit in units] == [
        "scene_generation",
        "continuity_review",
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
async def test_resume_rebinds_equivalent_workflow_to_new_planner_step_ids(
    dispatcher_db,
):
    repository = SqliteLongTaskRepository(dispatcher_db)
    work_items = WorkItemLifecycle(
        SqliteWorkItemRepository(dispatcher_db),
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
    task = await repository.start(task.id, expected_revision=task.revision)
    await repository.pause(task.id)

    second = await dispatcher.dispatch(
        object(),
        _plan(),
        _decision(workflow_ids=(
            "writer-episode-08",
            "reviewer-consolidation",
            "submit-draft",
        )),
        parent_run_id="run-parent-2",
    )

    assert second.task_id == first.task_id
    assert second.metadata["durableStepAliases"] == {
        "write-001": "writer-episode-08",
        "review-001": "reviewer-consolidation",
        "finalize": "submit-draft",
    }


@pytest.mark.asyncio
async def test_completed_same_scope_task_recovers_persisted_result(
    dispatcher_db,
):
    repository = SqliteLongTaskRepository(dispatcher_db)
    work_items = WorkItemLifecycle(
        SqliteWorkItemRepository(dispatcher_db),
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

    class _Runner:
        async def run_unit(self, task, unit, signal=None):
            del task, signal
            return LongTaskUnitResult(output_ref=f"artifact://{unit.id}")

    completed = await LongTaskCoordinator(
        repository,
        worker_id="screenplay-recovery",
    ).run(first.task_id, _Runner())
    assert completed.status is LongTaskStatus.COMPLETED
    work_item = await work_items.get(completed.work_item_id)
    await work_items.complete(WorkItemTransitionCommand(
        work_item_id=work_item.id,
        expected_revision=work_item.revision,
    ))

    second = await dispatcher.dispatch(
        object(),
        _plan(),
        _decision(workflow_ids=(
            "writer-episode-08",
            "reviewer-consolidation",
            "submit-draft",
        )),
        parent_run_id="run-parent-2",
    )

    assert second.task_id == first.task_id
    assert second.metadata["recovered"] is True
    assert second.metadata["resumed"] is False
    assert second.metadata["completedUnits"] == 3


@pytest.mark.asyncio
async def test_active_task_with_different_scope_cannot_complete_new_root_plan(
    dispatcher_db,
):
    repository = SqliteLongTaskRepository(dispatcher_db)
    work_item_repository = SqliteWorkItemRepository(dispatcher_db)
    dispatcher = ScreenplayLongTaskDispatcher(
        work_items=WorkItemLifecycle(
            work_item_repository,
            id_factory=iter(("work-1", "work-2")).__next__,
        ),
        long_tasks=repository,
        id_factory=iter(("task-1", "task-2")).__next__,
    )
    first = await dispatcher.dispatch(
        object(),
        _plan(),
        _decision(),
        parent_run_id="run-parent-1",
    )

    with pytest.raises(RuntimeError, match="scope_conflict"):
        await dispatcher.dispatch(
            object(),
            _plan(),
            _decision(scene_list_document_id="scene-list-2"),
            parent_run_id="run-parent-2",
        )

    task = await repository.load(first.task_id)
    assert task is not None
    links = await work_item_repository.list_run_links(task.work_item_id)
    assert [link.run_id for link in links] == ["run-parent-1"]


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


@pytest.mark.asyncio
async def test_terminal_unit_failure_atomically_cancels_and_resume_reopens_siblings(
    dispatcher_db,
):
    work_items = SqliteWorkItemRepository(dispatcher_db)
    await work_items.create("work-failure", WorkItemCreateCommand(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
        owner_id="project-failure",
        created_by_run_id="run-parent",
    ))
    repository = SqliteLongTaskRepository(dispatcher_db)
    task = await repository.create("task-failure", LongTaskCreateCommand(
        namespace=SCREENPLAY_DOMAIN_NAMESPACE,
        kind=SCREENPLAY_DRAFT_LONG_TASK_KIND,
        owner_id="project-failure",
        work_item_id="work-failure",
        created_by_run_id="run-parent",
        max_parallelism=2,
        units=(
            LongTaskUnitSpec(id="writer-checkpoint", position=0),
            LongTaskUnitSpec(id="writer-a", position=1),
            LongTaskUnitSpec(id="writer-b", position=2),
            LongTaskUnitSpec(
                id="review",
                position=3,
                dependencies=(
                    "writer-checkpoint",
                    "writer-a",
                    "writer-b",
                ),
            ),
        ),
    ))
    task = await repository.start(task.id, expected_revision=task.revision)
    checkpoint = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-checkpoint",
        lease_duration_ms=60_000,
    )
    assert checkpoint is not None and checkpoint.id == "writer-checkpoint"
    await repository.complete_unit(
        task.id,
        checkpoint.id,
        worker_id="worker-checkpoint",
        result=LongTaskUnitResult(output_ref="artifact://writer-checkpoint"),
    )
    writer_a = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-a",
        lease_duration_ms=60_000,
    )
    writer_b = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-b",
        lease_duration_ms=60_000,
    )
    assert writer_a is not None and writer_b is not None

    failed = await repository.fail_unit(
        task.id,
        writer_a.id,
        worker_id="worker-a",
        error_code="model_output_truncated",
        retryable=False,
    )

    assert failed.status is LongTaskStatus.FAILED
    assert {
        unit.id: unit.status
        for unit in await repository.list_units(task.id)
    } == {
        checkpoint.id: LongTaskUnitStatus.COMPLETED,
        writer_a.id: LongTaskUnitStatus.FAILED,
        writer_b.id: LongTaskUnitStatus.CANCELED,
        "review": LongTaskUnitStatus.CANCELED,
    }

    resumed = await repository.resume(task.id)

    assert resumed.status is LongTaskStatus.RUNNING
    resumed_statuses = {
        unit.id: unit.status
        for unit in await repository.list_units(task.id)
    }
    assert resumed_statuses[checkpoint.id] is LongTaskUnitStatus.COMPLETED
    assert {
        status
        for unit_id, status in resumed_statuses.items()
        if unit_id != checkpoint.id
    } == {LongTaskUnitStatus.PENDING}
