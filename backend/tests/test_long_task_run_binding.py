import pytest

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository
import pytest_asyncio


@pytest_asyncio.fixture
async def repository(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield SqliteLongTaskRepository(db)
    finally:
        await db.close()
from purra.errors import ContractViolationError
from purra.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec, LongTaskUnitResult


@pytest.mark.asyncio
async def test_unit_attempt_binding_is_immutable_and_completion_cannot_replace_it(repository):
    task = await repository.create("binding-task", LongTaskCreateCommand(
        namespace="test.binding", kind="test", owner_id="test", created_by_run_id="root",
        max_parallelism=2, units=(LongTaskUnitSpec(id="a", position=0), LongTaskUnitSpec(id="b", position=1)),
    ))
    await repository.start(task.id, expected_revision=task.revision)
    first = await repository.claim_ready_unit(task.id, worker_id="worker", lease_duration_ms=30000)
    second = await repository.claim_ready_unit(task.id, worker_id="worker", lease_duration_ms=30000)
    claim = dict(worker_id="worker", lease_epoch=first.lease_epoch)
    bound = await repository.bind_unit_run(task.id, first.id, run_id="child-a", **claim)
    revision = (await repository.load(task.id)).revision
    assert await repository.bind_unit_run(task.id, first.id, run_id="child-a", **claim) == bound
    assert (await repository.load(task.id)).revision == revision
    for identity in (first.id, second.id):
        with pytest.raises(ContractViolationError) as rejected:
            await repository.bind_unit_run(task.id, identity,
                run_id="different-child" if identity == first.id else "child-a", **claim)
        assert rejected.value.code == "long_task_unit_run_conflict"
    for identity in (first.id, second.id):
        with pytest.raises(ContractViolationError) as rejected:
            await repository.complete_unit(task.id, identity, **claim,
                result=LongTaskUnitResult(output_ref="test://result", run_id="different-child" if identity == first.id else "child-a"))
        assert rejected.value.code == "long_task_unit_run_conflict"
    assert (await repository.load(task.id)).revision == revision
    result = LongTaskUnitResult(output_ref="test://result", run_id="child-a")
    completed = await repository.complete_unit(task.id, first.id, result=result, **claim)
    assert await repository.complete_unit(task.id, first.id, result=result, **claim) == completed
    with pytest.raises(ContractViolationError):
        await repository.complete_unit(task.id, first.id, **claim,
            result=LongTaskUnitResult(output_ref="test://result", run_id="forged-child"))
    assert (await repository.list_units(task.id))[0].run_id == "child-a"
