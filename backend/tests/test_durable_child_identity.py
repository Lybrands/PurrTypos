"""Durable model operations do not manufacture Agent identities."""
import asyncio
import pytest
from purra.api import AgentCoreRunOptions
from purra.contracts import AgentMessage, AgentRunRequest, AgentRuntimeResult, ModelRequest, DomainContext
from purra.model_protocol import generic_capability_snapshot
from purra.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec
from purra.errors import ContractViolationError
from application.durable_agent_run import run_durable_operation
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "expired", "foreign_result"])
async def test_operation_retains_owner_and_requires_live_unit(tmp_path, failure):
    db = DatabaseConnection(tmp_path)
    await db.init()
    tasks = SqliteLongTaskRepository(db)
    task = await tasks.create("task", LongTaskCreateCommand(namespace="test", kind="test", owner_id="test",
        created_by_run_id="root", units=(LongTaskUnitSpec(id="unit", position=0),)))
    await tasks.start(task.id, expected_revision=task.revision)
    await tasks.claim_unit(task.id, "unit", worker_id="test", lease_duration_ms=30000)
    calls = []
    class Runs:
        async def run_operation(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["options"].agent_tree_run_id is None
            return AgentRuntimeResult(run_id="foreign" if failure == "foreign_result" else kwargs["run_id"],
                                      outcome="completed", final_response="evidence", model="test", round_count=1)
    request = AgentRunRequest(messages=(AgentMessage(role="user", content="Work"),),
        model=ModelRequest(provider="test", model="test", capability_snapshot=generic_capability_snapshot()),
        domain_context=DomainContext(namespace="test"))
    async def bind(run_id):
        assert run_id == "root"
    if failure == "expired":
        await tasks.pause(task.id)
    try:
        if failure:
            with pytest.raises(ContractViolationError) as caught:
                await run_durable_operation(db=db, runs=Runs(), request=request, options=AgentCoreRunOptions(),
                    api_key="test", signal=asyncio.Event(), bind_run=bind, task_id="task", unit_id="unit")
            assert caught.value.code == ("long_task_unit_lease_lost" if failure == "expired" else "run_identity_conflict")
        else:
            result, operation_id = await run_durable_operation(db=db, runs=Runs(), request=request, options=AgentCoreRunOptions(),
                api_key="test", signal=asyncio.Event(), bind_run=bind, task_id="task", unit_id="unit")
            assert result.run_id == "root" and operation_id == "task:unit:1"
            assert calls[0]["request"].metadata["operationScopeId"] == operation_id
        assert (await tasks.list_units("task"))[0].run_id is None
        assert await db.fetch_one("SELECT name FROM sqlite_master WHERE name='ai_agent_tree_commands_v4'") is None
    finally:
        await db.close()
