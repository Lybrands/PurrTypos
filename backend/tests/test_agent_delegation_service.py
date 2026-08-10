from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    AgentRunResult,
    ExecutionState,
    RunCreateParams,
    RunStatus,
    ToolExecutionMode,
)
from purra.events import CoreEventType
from application.agent_delegation_tool import build_delegation_tool_registration
from application.agent_delegation_service import AgentDelegationService
from application.agent_orchestrator import AgentOrchestrator
from database.connection import DatabaseConnection
from domains.agent_roles import AgentRoleDefinition, AgentRoleRegistry
from domains.writing.agent_roles import build_writing_agent_role_registry
from infrastructure.persistence import run_execution_store, run_store
from infrastructure.persistence import delegation_store
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _parent(db: DatabaseConnection) -> str:
    return await run_store.create_run(
        db,
        session_id=None,
        prompt="coordinate",
        mode="agent",
    )


def _service(db: DatabaseConnection, *, max_depth: int = 3) -> AgentDelegationService:
    return AgentDelegationService(
        SqliteDelegationRepository(db),
        max_depth=max_depth,
    )


@pytest.mark.asyncio
async def test_claim_respects_priority_and_parent_parallel_limit(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    low = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="researcher",
        objective="low priority",
        priority=1,
    )
    high = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="reviewer",
        objective="high priority",
        priority=10,
    )

    claims = await asyncio.gather(
        service.claim(
            parent_run_id=parent_run_id,
            worker_id="worker-a",
            max_parallel_children=1,
        ),
        service.claim(
            parent_run_id=parent_run_id,
            worker_id="worker-b",
            max_parallel_children=1,
        ),
    )
    winners = [claim for claim in claims if claim is not None]

    assert len(winners) == 1
    assert winners[0][0]["delegationId"] == high["delegationId"]
    snapshot = await service.snapshot(parent_run_id)
    assert snapshot["aggregate"]["counts"]["claimed"] == 1
    assert snapshot["aggregate"]["counts"]["queued"] == 1
    assert low["status"] == "queued"


@pytest.mark.asyncio
async def test_exact_claim_never_steals_an_older_same_role_delegation(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    older = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="screenplay_writer",
        objective="older unit",
    )
    target = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="screenplay_writer",
        objective="Planner target unit",
    )

    claimed = await service.claim_delegation(
        delegation_id=target["delegationId"],
        parent_run_id=parent_run_id,
        worker_id="worker-target",
        max_parallel_children=2,
    )

    assert claimed is not None
    assert claimed[0]["delegationId"] == target["delegationId"]
    snapshot = await service.snapshot(parent_run_id)
    statuses = {
        item["delegationId"]: item["status"]
        for item in snapshot["items"]
    }
    assert statuses[older["delegationId"]] == "queued"
    assert statuses[target["delegationId"]] == "claimed"


@pytest.mark.asyncio
async def test_claimed_delegation_atomically_attaches_child_run_and_result(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    delegation = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="researcher",
        objective="collect facts",
        input_payload={"chapterId": 7},
    )
    claimed = await service.claim(
        parent_run_id=parent_run_id,
        worker_id="worker-child",
        max_parallel_children=2,
    )
    assert claimed is not None
    _claimed_view, lineage = claimed
    repository = SqliteRunRepository(db, owner_id="worker-child")
    child_run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="collect facts",
        mode="agent",
        lineage=lineage,
    ))

    child = await run_store.get_run(db, child_run_id)
    running = await service.snapshot(parent_run_id)
    assert child is not None
    assert child["parent_run_id"] == parent_run_id
    assert child["root_run_id"] == parent_run_id
    assert child["delegation_id"] == delegation["delegationId"]
    assert child["agent_role"] == "researcher"
    assert child["run_depth"] == 1
    assert running["items"][0]["status"] == "running"

    await repository.transition(
        child_run_id,
        RunStatus.DONE,
        final_response="three verified facts",
    )
    assert await service.record_result(
        delegation_id=delegation["delegationId"],
        child_run_id=child_run_id,
        result=AgentRunResult(
            run_id=child_run_id,
            status=RunStatus.DONE,
            final_response="three verified facts",
        ),
    )
    completed = await service.snapshot(parent_run_id)
    assert completed["aggregate"]["state"] == "ready"
    assert completed["aggregate"]["results"][0]["summary"] == (
        "three verified facts"
    )


@pytest.mark.asyncio
async def test_required_child_failure_blocks_aggregate_but_optional_failure_does_not(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    required = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="reviewer",
        objective="required review",
        required=True,
        priority=2,
    )
    optional = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="critic",
        objective="optional critique",
        required=False,
        priority=1,
    )

    for delegation, worker in ((required, "required-worker"), (optional, "optional-worker")):
        claimed = await service.claim(
            parent_run_id=parent_run_id,
            worker_id=worker,
            max_parallel_children=2,
        )
        assert claimed is not None
        _, lineage = claimed
        repository = SqliteRunRepository(db, owner_id=worker)
        child_run_id = await repository.create(RunCreateParams(
            session_id=None,
            prompt=delegation["objective"],
            mode="agent",
            lineage=lineage,
        ))
        await repository.transition(
            child_run_id,
            RunStatus.FAILED,
            error="child failed",
        )
        assert await service.record_result(
            delegation_id=delegation["delegationId"],
            child_run_id=child_run_id,
            result=AgentRunResult(
                run_id=child_run_id,
                status=RunStatus.FAILED,
                error="child failed",
            ),
        )

    aggregate = (await service.snapshot(parent_run_id))["aggregate"]
    assert aggregate["state"] == "blocked"
    assert aggregate["requiredFailures"] == [required["delegationId"]]


@pytest.mark.asyncio
async def test_parent_cancel_cascades_to_queued_and_running_children(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    running_delegation = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="writer",
        objective="write",
        priority=2,
    )
    await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="reviewer",
        objective="review",
        priority=1,
    )
    claimed = await service.claim(
        parent_run_id=parent_run_id,
        worker_id="worker-child",
        max_parallel_children=1,
    )
    assert claimed is not None
    _, lineage = claimed
    child_run_id = await SqliteRunRepository(
        db,
        owner_id="worker-child",
    ).create(RunCreateParams(
        session_id=None,
        prompt="write",
        mode="agent",
        lineage=lineage,
    ))

    assert await service.cancel_children(parent_run_id) == 2
    snapshot = await service.snapshot(parent_run_id)
    child_execution = await run_execution_store.get_execution_state(db, child_run_id)
    assert {item["status"] for item in snapshot["items"]} == {"canceled"}
    assert child_execution is not None
    assert child_execution["cancel_requested_at_ms"] is not None
    assert snapshot["aggregate"]["state"] == "blocked"
    assert running_delegation["delegationId"] in snapshot["aggregate"]["requiredFailures"]


@pytest.mark.asyncio
async def test_depth_limit_rejects_unbounded_delegation(db):
    deep_parent = await run_store.create_run(
        db,
        session_id=None,
        prompt="too deep",
        mode="agent",
        parent_run_id="parent",
        root_run_id="root",
        delegation_id="delegation-parent",
        agent_role="worker",
        run_depth=3,
    )
    with pytest.raises(ValueError, match="depth limit"):
        await _service(db, max_depth=3).delegate(
            parent_run_id=deep_parent,
            agent_role="nested",
            objective="must not recurse",
        )


@pytest.mark.asyncio
async def test_orchestrator_runs_children_with_bounded_parallelism(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    for index in range(4):
        await service.delegate(
            parent_run_id=parent_run_id,
            agent_role="researcher",
            objective=f"task {index}",
            priority=index,
        )

    active = 0
    peak = 0

    async def runner(view, lineage):
        nonlocal active, peak
        repository = SqliteRunRepository(db, owner_id="pool-worker")
        child_run_id = await repository.create(RunCreateParams(
            session_id=None,
            prompt=view["objective"],
            mode="agent",
            lineage=lineage,
        ))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        await repository.transition(
            child_run_id,
            RunStatus.DONE,
            final_response=f"finished {view['objective']}",
        )
        return AgentRunResult(
            run_id=child_run_id,
            status=RunStatus.DONE,
            final_response=f"finished {view['objective']}",
        )

    snapshot = await AgentOrchestrator(service).run_queued(
        parent_run_id=parent_run_id,
        worker_id="pool-worker",
        max_parallel_children=2,
        runner=runner,
    )

    assert peak == 2
    assert snapshot["aggregate"]["state"] == "ready"
    assert snapshot["aggregate"]["counts"]["done"] == 4


@pytest.mark.asyncio
async def test_parent_delegation_tool_streams_lifecycle_and_returns_results(db):
    parent_run_id = await _parent(db)
    role_registry = build_writing_agent_role_registry()
    service = AgentDelegationService(
        SqliteDelegationRepository(db),
        role_registry=role_registry,
    )
    published = []
    active = 0
    peak = 0

    async def publish(event):
        published.append(event)

    async def runner(view, lineage):
        nonlocal active, peak
        repository = SqliteRunRepository(db, owner_id="parent-worker")
        child_run_id = await repository.create(RunCreateParams(
            session_id=None,
            prompt=view["objective"],
            mode="agent",
            lineage=lineage,
        ))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        response = f"verified: {view['objective']}"
        await repository.transition(
            child_run_id,
            RunStatus.DONE,
            final_response=response,
        )
        return AgentRunResult(
            run_id=child_run_id,
            status=RunStatus.DONE,
            final_response=response,
        )

    registration = build_delegation_tool_registration(
        service=service,
        role_registry=role_registry,
        worker_id="parent-worker",
        runner=runner,
        publish=publish,
        max_parallel_children=2,
    )
    role_enum = registration.schema.parameters["properties"]["delegations"][
        "items"
    ]["properties"]["agentRole"]["enum"]
    assert set(role_enum) == {"researcher", "reviewer", "analyst"}
    result = await registration.handler(
        ExecutionState(run_id=parent_run_id),
        {
            "delegations": [
                {
                    "agentRole": "researcher",
                    "objective": "collect evidence",
                    "input": {"chapterId": 7},
                    "priority": 2,
                },
                {
                    "agentRole": "reviewer",
                    "objective": "review the evidence",
                    "priority": 1,
                },
            ],
        },
    )

    payload = json.loads(result.content)
    assert result.error_code is None
    assert peak == 2
    assert payload["state"] == "ready"
    assert payload["counts"]["done"] == 2
    assert len(payload["results"]) == 2
    assert [event.type for event in published].count(
        CoreEventType.DELEGATION_CREATED
    ) == 2
    assert [event.type for event in published].count(
        CoreEventType.DELEGATION_CLAIMED
    ) == 2
    assert [event.type for event in published].count(
        CoreEventType.DELEGATION_COMPLETED
    ) == 2
    assert all(event.run_id == parent_run_id for event in published)
    created = next(
        event for event in published
        if event.type == CoreEventType.DELEGATION_CREATED
        and event.payload["agentRole"] == "researcher"
    )
    assert created.payload["input"] == {"chapterId": 7}
    assert created.payload["agentTitle"] == "研究 Agent"


def test_writing_role_registry_drives_delegation_schema_and_titles():
    registry = build_writing_agent_role_registry()

    assert registry.role_ids == ("researcher", "reviewer", "analyst")
    assert registry.require("reviewer").title == "审校 Agent"
    assert {
        mode.value for mode in registry.require("analyst").allowed_tool_modes
    } == {"read"}


def test_delegation_schema_uses_injected_product_roles_without_hardcoding():
    registry = AgentRoleRegistry((AgentRoleDefinition(
        id="continuity_checker",
        title="连续性检查 Agent",
        delegation_description="检查跨章节连续性",
        instruction="Check continuity using trusted read-only evidence.",
        allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
    ),))

    async def unused_runner(_view, _lineage):
        raise AssertionError("schema construction must not execute a child")

    async def unused_publish(_event):
        raise AssertionError("schema construction must not publish events")

    registration = build_delegation_tool_registration(
        service=object(),  # type: ignore[arg-type]
        role_registry=registry,
        worker_id="worker",
        runner=unused_runner,
        publish=unused_publish,
    )
    role_schema = registration.schema.parameters["properties"]["delegations"][
        "items"
    ]["properties"]["agentRole"]

    assert tuple(role_schema["enum"]) == ("continuity_checker",)
    assert "检查跨章节连续性" in registration.schema.description


@pytest.mark.asyncio
async def test_restart_requeues_expired_claim_and_reconciles_terminal_child(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    first = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="researcher",
        objective="abandoned before start",
        priority=2,
    )
    second = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="reviewer",
        objective="completed before parent receipt",
        priority=1,
    )
    claimed_first = await delegation_store.claim_next(
        db,
        parent_run_id=parent_run_id,
        worker_id="dead-worker",
        max_parallel_children=2,
        claim_lease_duration_ms=10,
        timestamp_ms=100,
    )
    assert claimed_first is not None
    claimed_second = await service.claim(
        parent_run_id=parent_run_id,
        worker_id="live-worker",
        max_parallel_children=2,
    )
    assert claimed_second is not None
    _, lineage = claimed_second
    repository = SqliteRunRepository(db, owner_id="live-worker")
    child_run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt=second["objective"],
        mode="agent",
        lineage=lineage,
    ))
    await repository.transition(
        child_run_id,
        RunStatus.DONE,
        final_response="durable child result",
    )

    recovered = await delegation_store.recover_delegations(db, timestamp_ms=111)
    snapshot = await service.snapshot(parent_run_id)
    items = {item["delegationId"]: item for item in snapshot["items"]}

    assert recovered == {"requeued": 1, "reconciled": 1}
    assert items[first["delegationId"]]["status"] == "queued"
    assert items[second["delegationId"]]["status"] == "done"
    assert items[second["delegationId"]]["resultSummary"] == "durable child result"
