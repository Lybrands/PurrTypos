from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.long_tasks import (
    LongTaskCoordinator,
    LongTaskCreateCommand,
    LongTaskStatus,
    LongTaskUnitResult,
    LongTaskUnitSpec,
    LongTaskUnitStatus,
)
from purra.recovery import (
    FailureCategory,
    FailureDisposition,
    FailureSignal,
    RecoveryEffectState,
    decide_failure,
)
from purra.work_items.contracts import WorkItemCreateCommand
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


@pytest_asyncio.fixture
async def long_task_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_long_task_runs_dependency_order_and_retries_one_unit(long_task_db):
    work_items = SqliteWorkItemRepository(long_task_db)
    await work_items.create("work-1", WorkItemCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        created_by_run_id="run-parent",
    ))
    repository = SqliteLongTaskRepository(long_task_db)
    created = await repository.create("task-1", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        units=(
            LongTaskUnitSpec(id="batch-1", position=0),
            LongTaskUnitSpec(
                id="batch-2",
                position=1,
                dependencies=("batch-1",),
            ),
        ),
    ))
    assert created.status is LongTaskStatus.PENDING

    class _Runner:
        def __init__(self):
            self.calls = []

        def classify_unit_failure(self, task, unit, error):
            return FailureSignal(
                category=FailureCategory.TRANSIENT_PROVIDER,
                code=str(error),
                retryable=str(error) == "temporary_provider_failure",
            )

        async def run_unit(self, task, unit, signal=None):
            self.calls.append((unit.id, unit.attempt))
            if unit.id == "batch-1" and unit.attempt == 1:
                raise RuntimeError("temporary_provider_failure")
            return LongTaskUnitResult(
                output_ref=f"artifact://{unit.id}",
                run_id=f"run-{unit.id}-{unit.attempt}",
            )

    runner = _Runner()
    completed = await LongTaskCoordinator(
        repository,
        worker_id="worker-1",
    ).run(created.id, runner)

    assert completed.status is LongTaskStatus.COMPLETED
    assert completed.completed_units == 2
    assert runner.calls == [
        ("batch-1", 1),
        ("batch-1", 2),
        ("batch-2", 1),
    ]
    units = await repository.list_units(created.id)
    assert [unit.output_ref for unit in units] == [
        "artifact://batch-1",
        "artifact://batch-2",
    ]


def test_long_task_contract_rejects_dependency_cycles():
    with pytest.raises(ValueError, match="cycle"):
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id="work-1",
            created_by_run_id="run-parent",
            units=(
                LongTaskUnitSpec(
                    id="batch-1",
                    position=0,
                    dependencies=("batch-2",),
                ),
                LongTaskUnitSpec(
                    id="batch-2",
                    position=1,
                    dependencies=("batch-1",),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_long_task_runs_independent_planner_branches_concurrently(
    long_task_db,
):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-parallel",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-parallel", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-parallel",
        created_by_run_id="run-parent",
        max_parallelism=2,
        units=(
            LongTaskUnitSpec(id="writer-a", position=0),
            LongTaskUnitSpec(id="writer-b", position=1),
            LongTaskUnitSpec(
                id="assemble",
                position=2,
                dependencies=("writer-a", "writer-b"),
            ),
        ),
    ))
    release = asyncio.Event()

    class _Runner:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.calls = []

        async def run_unit(self, task, unit, signal=None):
            self.calls.append(unit.id)
            if unit.id.startswith("writer-"):
                self.active += 1
                self.peak = max(self.peak, self.active)
                if self.active == 2:
                    release.set()
                await asyncio.wait_for(release.wait(), timeout=1)
                self.active -= 1
            return LongTaskUnitResult(output_ref=f"artifact://{unit.id}")

    runner = _Runner()
    completed = await LongTaskCoordinator(
        repository,
        worker_id="worker-parallel",
    ).run(task.id, runner)

    assert completed.status is LongTaskStatus.COMPLETED
    assert runner.peak == 2
    assert set(runner.calls[:2]) == {"writer-a", "writer-b"}
    assert runner.calls[-1] == "assemble"


@pytest.mark.asyncio
async def test_concurrent_coordinators_wait_for_the_same_leased_task(
    long_task_db,
):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-shared-lease",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-shared-lease", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-shared-lease",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="write", position=0),),
    ))
    started = asyncio.Event()
    release = asyncio.Event()

    class _Runner:
        def __init__(self):
            self.calls = 0

        async def run_unit(self, task, unit, signal=None):
            self.calls += 1
            started.set()
            await release.wait()
            return LongTaskUnitResult(output_ref="artifact://write")

    runner = _Runner()
    first = asyncio.create_task(LongTaskCoordinator(
        repository,
        worker_id="worker-first",
        idle_poll_ms=5,
    ).run(task.id, runner))
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(LongTaskCoordinator(
        repository,
        worker_id="worker-second",
        idle_poll_ms=5,
    ).run(task.id, runner))
    await asyncio.sleep(0.02)
    assert not second.done()

    release.set()
    results = await asyncio.gather(first, second)

    assert [result.status for result in results] == [
        LongTaskStatus.COMPLETED,
        LongTaskStatus.COMPLETED,
    ]
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_binding_child_run_persists_only_run_attempt_identity(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-run-history",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-run-history", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-run-history",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))
    task = await repository.start(task.id, expected_revision=task.revision)
    unit = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert unit is not None

    bound = await repository.bind_unit_run(
        task.id,
        unit.id,
        worker_id="worker-1",
        run_id="run-child-1",
    )

    assert [dict(item) for item in bound.metadata["runHistory"]] == [
        {"attempt": 1, "runId": "run-child-1"},
    ]
    assert "liveConversation" not in bound.metadata


@pytest.mark.asyncio
async def test_long_task_retry_backoff_waits_without_spending_all_attempts_at_once(
    long_task_db,
    monkeypatch,
):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-backoff",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-backoff", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-backoff",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))
    delays = []

    async def _sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("purra.long_tasks.coordinator.asyncio.sleep", _sleep)

    class _Runner:
        def classify_unit_failure(self, task, unit, error):
            return FailureSignal(
                category=FailureCategory.TRANSIENT_PROVIDER,
                code=str(error),
                retryable=str(error) == "temporary_connection_failure",
            )

        async def run_unit(self, task, unit, signal=None):
            if unit.attempt < 3:
                raise RuntimeError("temporary_connection_failure")
            return LongTaskUnitResult(output_ref="artifact://done")

    completed = await LongTaskCoordinator(
        repository,
        worker_id="worker-backoff",
        retry_backoff_ms=(5_000, 20_000),
    ).run(task.id, _Runner())

    assert completed.status is LongTaskStatus.COMPLETED
    assert delays == [5.0, 20.0]


@pytest.mark.asyncio
async def test_long_task_does_not_retry_an_unclassified_failure(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-no-implicit-retry",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create(
        "task-no-implicit-retry",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id="work-no-implicit-retry",
            created_by_run_id="run-parent",
            units=(LongTaskUnitSpec(id="batch-1", position=0),),
        ),
    )

    class _Runner:
        def __init__(self):
            self.calls = 0

        async def run_unit(self, task, unit, signal=None):
            self.calls += 1
            raise RuntimeError("model_output_truncated")

    runner = _Runner()
    failed = await LongTaskCoordinator(
        repository,
        worker_id="worker-no-implicit-retry",
    ).run(task.id, runner)

    assert failed.status is LongTaskStatus.FAILED
    assert runner.calls == 1
    unit = (await repository.list_units(task.id))[0]
    assert unit.error_code == "model_output_truncated"
    assert unit.attempt == 1


@pytest.mark.asyncio
async def test_coordinator_pauses_exhausted_recoverable_unit(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-exhausted-recoverable",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create(
        "task-exhausted-recoverable",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id="work-exhausted-recoverable",
            created_by_run_id="run-parent",
            units=(LongTaskUnitSpec(
                id="batch-1",
                position=0,
                max_attempts=1,
            ),),
        ),
    )

    class _Runner:
        def classify_unit_failure(self, task, unit, error):
            return FailureSignal(
                category=FailureCategory.MODEL_OUTPUT_INVALID,
                code=str(error),
                retryable=True,
            )

        async def run_unit(self, task, unit, signal=None):
            raise RuntimeError("model_output_truncated")

    paused = await LongTaskCoordinator(
        repository,
        worker_id="worker-exhausted-recoverable",
    ).run(task.id, _Runner())

    assert paused.status is LongTaskStatus.PAUSED
    unit = (await repository.list_units(task.id))[0]
    assert unit.status is LongTaskUnitStatus.BLOCKED
    assert unit.error_code == "model_output_truncated"


@pytest.mark.asyncio
async def test_pause_signal_does_not_turn_paused_task_into_canceled(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-pause-race",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-pause-race", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-pause-race",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))
    signal = asyncio.Event()

    class _Runner:
        async def run_unit(self, task, unit, signal=None):
            await repository.pause(task.id)
            signal.set()
            raise RuntimeError("child_run_canceled_after_pause")

    paused = await LongTaskCoordinator(
        repository,
        worker_id="worker-pause-race",
    ).run(task.id, _Runner(), signal)

    assert paused.status is LongTaskStatus.PAUSED
    paused_unit = (await repository.list_units(task.id))[0]
    assert paused_unit.status.value == "pending"
    assert paused_unit.worker_id is None


@pytest.mark.asyncio
async def test_canceled_worker_checkpoints_active_unit_for_resume(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-interrupted",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-interrupted", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-interrupted",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0, max_attempts=1),),
    ))

    class _Runner:
        async def run_unit(self, task, unit, signal=None):
            raise asyncio.CancelledError

    interrupted = await LongTaskCoordinator(
        repository,
        worker_id="worker-interrupted",
    ).run(task.id, _Runner())

    assert interrupted.status is LongTaskStatus.PAUSED
    unit = (await repository.list_units(task.id))[0]
    assert unit.status.value == "pending"
    assert unit.attempt == 1
    assert unit.max_attempts == 2
    assert unit.worker_id is None
    assert unit.lease_expires_at_ms is None
    assert unit.error_code == "execution_interrupted"


@pytest.mark.asyncio
async def test_long_task_pause_resume_and_cancel_are_persistent(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-2",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-2", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-2",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0),),
    ))
    task = await repository.start(task.id, expected_revision=task.revision)
    claimed = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=300_000,
    )
    assert claimed is not None
    assert (await repository.pause(task.id)).status is LongTaskStatus.PAUSED
    paused_unit = (await repository.list_units(task.id))[0]
    assert paused_unit.status.value == "pending"
    assert paused_unit.worker_id is None
    assert (await repository.resume(task.id)).status is LongTaskStatus.RUNNING
    assert (await repository.cancel(task.id)).status is LongTaskStatus.CANCELED
    assert (await repository.list_units(task.id))[0].status.value == "canceled"


@pytest.mark.asyncio
async def test_failed_long_task_can_be_explicitly_retried(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-3",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create("task-3", LongTaskCreateCommand(
        namespace="test",
        kind="large_write",
        owner_id="owner-1",
        work_item_id="work-3",
        created_by_run_id="run-parent",
        units=(LongTaskUnitSpec(id="batch-1", position=0, max_attempts=1),),
    ))
    task = await repository.start(task.id, expected_revision=task.revision)
    unit = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert unit is not None
    failed = await repository.settle_unit_failure(
        task.id,
        unit.id,
        worker_id="worker-1",
        decision=decide_failure(
            FailureSignal(
                category=FailureCategory.BUSINESS_INVARIANT,
                code="provider_failed",
                retryable=False,
            ),
            attempts_remaining=0,
        ),
    )
    assert failed.status is LongTaskStatus.FAILED

    with pytest.raises(ValueError, match="requires additional attempts"):
        await repository.resume(task.id)

    resumed = await repository.resume(task.id, additional_attempts=1)
    assert resumed.status is LongTaskStatus.RUNNING
    assert resumed.failed_units == 0
    retried = (await repository.list_units(task.id))[0]
    assert retried.status.value == "pending"
    assert retried.max_attempts == 2


@pytest.mark.asyncio
async def test_retry_claim_preserves_previous_failure_reason(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-retry-reason",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create(
        "task-retry-reason",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id="work-retry-reason",
            created_by_run_id="run-parent",
            units=(LongTaskUnitSpec(
                id="batch-1",
                position=0,
                max_attempts=3,
            ),),
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    first = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert first is not None
    await repository.settle_unit_failure(
        task.id,
        first.id,
        worker_id="worker-1",
        decision=decide_failure(
            FailureSignal(
                category=FailureCategory.MODEL_OUTPUT_INVALID,
                code="screenplay.batch.invalid_json",
                retryable=True,
            ),
            attempts_remaining=2,
        ),
    )

    second = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )

    assert second is not None
    assert second.attempt == 2
    assert second.error_code == "screenplay.batch.invalid_json"


async def _task_with_completed_and_active_unit(db, suffix: str):
    work_items = SqliteWorkItemRepository(db)
    await work_items.create(
        f"work-blocked-{suffix}",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(db)
    task = await repository.create(
        f"task-blocked-{suffix}",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id=f"work-blocked-{suffix}",
            created_by_run_id="run-parent",
            units=(
                LongTaskUnitSpec(id="completed", position=0),
                LongTaskUnitSpec(
                    id="recoverable",
                    position=1,
                    dependencies=("completed",),
                    max_attempts=1,
                ),
            ),
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    completed = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert completed is not None
    await repository.complete_unit(
        task.id,
        completed.id,
        worker_id="worker-1",
        result=LongTaskUnitResult(output_ref="artifact://completed"),
    )
    active = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert active is not None
    return repository, task, active


@pytest.mark.asyncio
async def test_exhausted_recoverable_unit_pauses_without_canceling_completed_units(
    long_task_db,
):
    repository, task, active = await _task_with_completed_and_active_unit(
        long_task_db,
        "preserve",
    )
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.MODEL_OUTPUT_INVALID,
            code="model_output_truncated",
            retryable=True,
            effect_state=RecoveryEffectState.NOT_STARTED,
            checkpoint_available=True,
        ),
        attempts_remaining=0,
    )
    assert decision.disposition is FailureDisposition.PAUSE_RECOVERABLE

    paused = await repository.settle_unit_failure(
        task.id,
        active.id,
        worker_id="worker-1",
        decision=decision,
    )

    assert paused.status is LongTaskStatus.PAUSED
    assert paused.completed_units == 1
    assert paused.failed_units == 0
    units = await repository.list_units(task.id)
    assert units[0].status is LongTaskUnitStatus.COMPLETED
    assert units[0].output_ref == "artifact://completed"
    assert units[1].status is LongTaskUnitStatus.BLOCKED
    assert units[1].error_code == "model_output_truncated"


@pytest.mark.asyncio
async def test_resume_only_requeues_blocked_units(long_task_db):
    repository, task, active = await _task_with_completed_and_active_unit(
        long_task_db,
        "resume",
    )
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.MODEL_OUTPUT_INVALID,
            code="max_model_rounds",
            retryable=True,
            effect_state=RecoveryEffectState.NOT_STARTED,
        ),
        attempts_remaining=0,
    )
    await repository.settle_unit_failure(
        task.id,
        active.id,
        worker_id="worker-1",
        decision=decision,
    )

    resumed = await repository.resume(task.id, additional_attempts=1)

    assert resumed.status is LongTaskStatus.RUNNING
    units = await repository.list_units(task.id)
    assert units[0].status is LongTaskUnitStatus.COMPLETED
    assert units[0].output_ref == "artifact://completed"
    assert units[0].max_attempts == 3
    assert units[1].status is LongTaskUnitStatus.PENDING
    assert units[1].max_attempts == 2


@pytest.mark.asyncio
async def test_permanent_unit_failure_still_terminalizes_task(long_task_db):
    await SqliteWorkItemRepository(long_task_db).create(
        "work-permanent",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(long_task_db)
    task = await repository.create(
        "task-permanent",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner-1",
            work_item_id="work-permanent",
            created_by_run_id="run-parent",
            units=(LongTaskUnitSpec(id="invalid", position=0),),
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    active = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-1",
        lease_duration_ms=30_000,
    )
    assert active is not None
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.BUSINESS_INVARIANT,
            code="candidate_schema_invalid",
            retryable=False,
        ),
        attempts_remaining=2,
    )

    failed = await repository.settle_unit_failure(
        task.id,
        active.id,
        worker_id="worker-1",
        decision=decision,
    )

    assert failed.status is LongTaskStatus.FAILED
    unit = (await repository.list_units(task.id))[0]
    assert unit.status is LongTaskUnitStatus.FAILED
