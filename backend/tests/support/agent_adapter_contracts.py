"""Reusable behavioral contracts for Agent Core persistence adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_core.contracts import ToolCall, ToolHandlerResult
from agent_core.ports import (
    CheckpointStore,
    DelegationRepository,
    ExecutionLeaseStore,
    ToolIdempotencyGateway,
)


async def assert_execution_lease_store_contract(
    store: ExecutionLeaseStore,
    create_unowned_run: Callable[[], Awaitable[str]],
) -> None:
    assert isinstance(store, ExecutionLeaseStore)
    run_id = await create_unowned_run()
    assert await store.claim(run_id, "owner-a", lease_duration_ms=60_000)
    assert not await store.claim(run_id, "owner-b", lease_duration_ms=60_000)
    lease = await store.get(run_id)
    assert lease is not None
    assert lease.owner_id == "owner-a"
    assert lease.attempt == 1
    assert not await store.renew(run_id, "owner-b", lease_duration_ms=60_000)
    assert await store.renew(run_id, "owner-a", lease_duration_ms=60_000)
    assert await store.request_cancellation(run_id)
    assert not await store.request_cancellation(run_id)
    assert await store.release(run_id, "owner-a")
    released = await store.get(run_id)
    assert released is not None and released.owner_id is None


async def assert_delegation_repository_contract(
    repository: DelegationRepository,
    create_parent_run: Callable[[], Awaitable[str]],
) -> None:
    assert isinstance(repository, DelegationRepository)
    parent_run_id = await create_parent_run()
    low = await repository.create(
        parent_run_id=parent_run_id,
        agent_role="role-low",
        objective="low priority work",
        priority=1,
    )
    high = await repository.create(
        parent_run_id=parent_run_id,
        agent_role="role-high",
        objective="high priority work",
        priority=9,
    )
    claim = await repository.claim_next(
        parent_run_id=parent_run_id,
        worker_id="worker-a",
        max_parallel_children=1,
    )
    assert claim is not None
    assert claim.delegation.id == high.id
    assert claim.lineage.parent_run_id == parent_run_id
    assert await repository.claim_next(
        parent_run_id=parent_run_id,
        worker_id="worker-b",
        max_parallel_children=1,
    ) is None
    rows = await repository.list_for_parent(parent_run_id)
    assert {row.id for row in rows} == {low.id, high.id}
    assert (await repository.aggregate(parent_run_id)).state == "pending"
    assert await repository.cancel_children(parent_run_id) == 2
    assert (await repository.aggregate(parent_run_id)).state == "blocked"


async def assert_checkpoint_store_contract(
    store: CheckpointStore,
    seed_run: Callable[[], Awaitable[str]],
) -> None:
    assert isinstance(store, CheckpointStore)
    run_id = await seed_run()
    first = await store.load(run_id, limit=1)
    assert first is not None
    assert len(first.events) == 1
    assert first.has_more is True
    assert first.next_cursor > 0
    second = await store.load(run_id, after_event_id=first.next_cursor, limit=10)
    assert second is not None
    assert second.events
    assert second.next_cursor > first.next_cursor
    assert await store.load("missing-run") is None


async def assert_tool_idempotency_gateway_contract(
    gateway: ToolIdempotencyGateway,
    run_id: str,
) -> None:
    assert isinstance(gateway, ToolIdempotencyGateway)
    calls = 0

    async def operation() -> ToolHandlerResult:
        nonlocal calls
        calls += 1
        return ToolHandlerResult("durable result")

    tool_call = ToolCall(
        id="contract-call",
        name="contract_write",
        arguments_json='{"value":1}',
    )
    first = await gateway.execute_once(run_id, tool_call, operation)
    replay = await gateway.execute_once(run_id, tool_call, operation)
    assert calls == 1
    assert first.from_cache is False
    assert replay.from_cache is True
    assert replay.content == first.content
