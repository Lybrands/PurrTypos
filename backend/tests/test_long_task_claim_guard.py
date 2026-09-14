from __future__ import annotations

import pytest

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository
from purra.errors import ContractViolationError
from purra.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["allow", "wait", "fail"])
@pytest.mark.parametrize("targeted", [False, True])
async def test_claim_admission_is_atomic_and_preserves_guard_failures(tmp_path, decision, targeted):
    db = DatabaseConnection(tmp_path)
    await db.init()
    calls = []
    original_error = ContractViolationError("admission failed", code="admission_failed")

    async def guard(task, candidate):
        assert db.current_task_owns_transaction()
        row = await db.fetch_one(
            "SELECT status, attempt FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND unit_id = ?", [task.id, candidate.id],
        )
        assert row["status"] == "pending"
        assert row["attempt"] == 0
        calls.append((task.id, candidate.id))
        if decision == "fail":
            raise original_error
        return decision == "allow"

    repository = SqliteLongTaskRepository(db, claim_guard=guard)
    try:
        task = await repository.create("guarded-task", LongTaskCreateCommand(
            namespace="test.guard", kind="test", owner_id="test-owner",
            created_by_run_id="test-root",
            units=(LongTaskUnitSpec(id="unit-1", position=0),),
        ))
        await repository.start(task.id, expected_revision=task.revision)
        async def claim():
            if targeted:
                return await repository.claim_unit(task.id, "unit-1", worker_id="worker", lease_duration_ms=30_000)
            return await repository.claim_ready_unit(task.id, worker_id="worker", lease_duration_ms=30_000)

        if decision == "fail":
            with pytest.raises(ContractViolationError) as failure:
                await claim()
            assert failure.value is original_error
        else:
            claimed = await claim()
            assert (claimed is not None) is (decision == "allow")
        assert calls == [(task.id, "unit-1")]
        row = await db.fetch_one(
            "SELECT status, attempt, worker_id FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND unit_id = ?", [task.id, "unit-1"],
        )
        assert row == {
            "status": "claimed" if decision == "allow" else "pending",
            "attempt": 1 if decision == "allow" else 0,
            "worker_id": "worker" if decision == "allow" else None,
        }
    finally:
        await db.close()
