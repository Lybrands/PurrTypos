from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    AgentRunResult,
    RunCreateParams,
    RunStatus,
)
from application.agent_delegation_service import AgentDelegationService
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
        role_registry=AgentRoleRegistry(
            AgentRoleDefinition(
                id=role_id,
                title=role_id,
                delegation_description=f"Delegate to {role_id}",
                instruction=f"Act as {role_id}",
            )
            for role_id in (
                "researcher",
                "reviewer",
                "screenplay_writer",
                "critic",
                "writer",
                "nested",
            )
        ),
        max_depth=max_depth,
    )


@pytest.mark.asyncio
async def test_delegate_rejects_profiles_without_agent_roles(db):
    parent_run_id = await _parent(db)
    service = AgentDelegationService(SqliteDelegationRepository(db))

    with pytest.raises(ValueError, match="does not support delegation"):
        await service.delegate(
            parent_run_id=parent_run_id,
            agent_role="researcher",
            objective="must stay disabled",
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

    # SqliteRunRepository.create owns the atomic first attachment. The Core
    # coordinator confirms that same identity after submit returns, so the
    # repository operation must be an event-free idempotent replay.
    events_before_replay = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ?",
        [parent_run_id],
    )
    assert await SqliteDelegationRepository(db).attach_child_run(
        delegation_id=delegation["delegationId"],
        child_run_id=child_run_id,
        worker_id="worker-child",
    )
    events_after_replay = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ?",
        [parent_run_id],
    )
    assert events_after_replay == events_before_replay

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
async def test_cancel_fence_rejects_late_delegation_child_attachment(db):
    parent_run_id = await _parent(db)
    service = _service(db)
    delegation = await service.delegate(
        parent_run_id=parent_run_id,
        agent_role="researcher",
        objective="collect facts",
    )
    claimed = await service.claim(
        parent_run_id=parent_run_id,
        worker_id="worker-child",
        max_parallel_children=1,
    )
    assert claimed is not None
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, mode, prompt, parent_run_id, root_run_id, "
        "delegation_id, agent_role, run_depth) "
        "VALUES ('late-delegated-child', 'running', 'agent', '', ?, ?, ?, "
        "'researcher', 1)",
        [parent_run_id, parent_run_id, delegation["delegationId"]],
    )
    await run_execution_store.fence_cancellation_tree(db, parent_run_id)

    assert not await SqliteDelegationRepository(db).attach_child_run(
        delegation_id=delegation["delegationId"],
        child_run_id="late-delegated-child",
        worker_id="worker-child",
    )
    persisted = await delegation_store.get_delegation(
        db,
        delegation["delegationId"],
    )
    assert persisted is not None
    assert persisted["status"] == "canceled"
    assert persisted["child_run_id"] is None


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
    root = await run_store.create_run(
        db,
        session_id=None,
        prompt="root",
        mode="agent",
    )
    deep_parent = await run_store.create_run(
        db,
        session_id=None,
        prompt="too deep",
        mode="agent",
        parent_run_id=root,
        root_run_id=root,
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


def test_writing_role_registry_drives_delegation_schema_and_titles():
    registry = build_writing_agent_role_registry()

    assert registry.role_ids == ("researcher", "reviewer", "analyst")
    assert registry.require("reviewer").title == "审校 Agent"
    assert {
        mode.value for mode in registry.require("analyst").allowed_tool_modes
    } == {"read"}


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
