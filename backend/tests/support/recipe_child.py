"""Claim an ordinary operation; model calls explicitly create their own Agent."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from purra.agent_tree import (
    AgentCapabilityGrant,
    BeginRootAgentCommand,
)
from purra.long_tasks import (
    LongTaskCreateCommand,
    LongTaskRunRelation,
    LongTaskUnitSpec,
)

from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_run_tree_repository import (
    SqliteRunTreeRepository,
)
from infrastructure.persistence.run_store import create_run


@dataclass(frozen=True)
class BoundRecipeChild:
    root_run_id: str
    task: object
    unit: object
    bind_run: object


@asynccontextmanager
async def bound_recipe_child(
    db,
    *,
    task_id: str,
    unit_id: str,
    task_metadata=None,
    unit_metadata=None,
    on_bind=None,
    allowed_tools=(),
    allowed_models=("model", "deepseek-v4-flash"),
):
    suffix = uuid4().hex
    root_agent_id = f"test-root-agent-{suffix}"
    worker_id = f"test-unit-worker-{suffix}"
    tree = SqliteRunTreeRepository(db)
    tasks = SqliteLongTaskRepository(db)
    task = await tasks.load(task_id)
    root_run_id = (
        task.created_by_run_id
        if task is not None
        else f"test-root-{suffix}"
    )
    if task is not None and not any(
        binding.run_id == root_run_id
        for binding in await tasks.list_run_bindings(task.id)
    ):
        await tasks.bind_run(
            task.id,
            root_run_id,
            relation=LongTaskRunRelation.CREATED,
        )
    await tree.begin_root(BeginRootAgentCommand(
        run_id=root_run_id,
        agent_id=root_agent_id,
        name="test-root",
        title="Test Root",
        instruction="Own the synthetic Recipe.",
        objective="Exercise one bound Recipe Child.",
        capability_grant=AgentCapabilityGrant(
            can_spawn_agents=True,
            allowed_tools=tuple(allowed_tools),
            allowed_models=tuple(allowed_models),
        ),
        idempotency_key=f"root:{suffix}",
    ))
    if await db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE id = ?", [root_run_id],
    ) is None:
        from purra.contracts import RunCreateParams
        from purra.events import AgentEvent
        from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
        from infrastructure.persistence.sqlite_agent_output_repository import SqliteAgentOutputRepository
        repository = SqliteRunRepository(db)
        await SqliteAgentOutputRepository(db, run_repository=repository).begin_run_lifecycle(
            RunCreateParams(session_id=None, prompt="Synthetic Root", mode="test_recipe_root",
                            requested_run_id=root_run_id, root_run_id=root_run_id, agent_id=root_agent_id,
                            turn_id="synthetic-turn"), AgentEvent("run.started"),
        )
    if task is None:
        task = await tasks.create(task_id, LongTaskCreateCommand(
            namespace="test.recipe",
            kind="test.recipe",
            owner_id=f"test-{suffix}",
            created_by_run_id=root_run_id,
            units=(LongTaskUnitSpec(
                id=unit_id,
                position=0,
                max_attempts=1,
                metadata=unit_metadata or {},
            ),),
            metadata=task_metadata or {},
        ))
    if task.status.value == "pending":
        task = await tasks.start(task.id, expected_revision=task.revision)
    unit = await tasks.claim_unit(
        task.id,
        unit_id,
        worker_id=worker_id,
        lease_duration_ms=30_000,
    )
    assert unit is not None, (
        task.status.value,
        [(item.id, item.status.value) for item in await tasks.list_units(task.id)],
    )
    notified = False

    async def bind_run(run_id: str):
        nonlocal notified
        assert run_id == root_run_id
        assert await tree.list_descendants(root_run_id) == ()
        if not notified and on_bind is not None:
            notified = True
            await on_bind(run_id)

    from application.durable_agent_run import operation_run_scope
    with operation_run_scope(root_run_id):
        yield BoundRecipeChild(root_run_id, task, unit, bind_run)


__all__ = ["BoundRecipeChild", "bound_recipe_child"]
