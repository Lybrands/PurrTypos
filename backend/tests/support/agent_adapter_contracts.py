"""Reusable behavioral contracts for PurrA persistence adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from purra.artifacts import (
    ArtifactClaimLeaseCommand,
    ArtifactWriteClaimCommand,
)
from purra.artifacts.errors import ArtifactConflictError
from purra.artifacts.ports import ArtifactClaimRepository
from purra.contracts import ToolCall, ToolHandlerResult
from purra.ports import (
    CheckpointStore,
    DelegationRepository,
    ExecutionLeaseStore,
    ToolIdempotencyGateway,
)
from purra.work_items import (
    WorkItemCreateCommand,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemStatus,
    WorkItemTransitionCommand,
)
from purra.work_items.ports import WorkItemRepository


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
    exact = await repository.claim(
        delegation_id=low.id,
        parent_run_id=parent_run_id,
        worker_id="worker-b",
        max_parallel_children=2,
    )
    assert exact is not None
    assert exact.delegation.id == low.id
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


async def assert_work_item_repository_contract(
    repository: WorkItemRepository,
) -> None:
    assert isinstance(repository, WorkItemRepository)
    item = await repository.create(
        "contract-work-item",
        WorkItemCreateCommand(
            namespace="contract",
            kind="durable_task",
            owner_id="contract-owner",
            created_by_run_id="contract-run-a",
            metadata={"version": 1},
        ),
    )
    assert item.status is WorkItemStatus.OPEN
    creator_links = await repository.list_run_links(item.id)
    assert len(creator_links) == 1
    assert creator_links[0].relation is WorkItemRunRelation.CREATED

    command = WorkItemRunLinkCommand(
        work_item_id=item.id,
        run_id="contract-run-b",
        relation=WorkItemRunRelation.CONTINUATION,
        expected_revision=1,
    )
    link = await repository.link_run(command)
    assert await repository.link_run(command) == link
    current = await repository.load(item.id)
    assert current is not None and current.revision == 1

    completed = await repository.complete(WorkItemTransitionCommand(
        work_item_id=item.id,
        expected_revision=1,
    ))
    assert completed.status is WorkItemStatus.COMPLETED
    assert completed.revision == 2
    reference = await repository.link_run(WorkItemRunLinkCommand(
        work_item_id=item.id,
        run_id="contract-run-c",
        relation=WorkItemRunRelation.REFERENCE,
        expected_revision=2,
    ))
    assert reference.relation is WorkItemRunRelation.REFERENCE


async def assert_artifact_claim_repository_contract(
    repository: ArtifactClaimRepository,
    *,
    artifact_id: str,
    work_item_id: str,
    revision: int,
    first_run_id: str,
    second_run_id: str,
) -> None:
    assert isinstance(repository, ArtifactClaimRepository)

    async def acquire(run_id: str):
        return await repository.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact_id,
            work_item_id=work_item_id,
            run_id=run_id,
            expected_revision=revision,
            lease_duration_ms=30_000,
        ))

    results = await asyncio.gather(
        acquire(first_run_id),
        acquire(second_run_id),
        return_exceptions=True,
    )
    claims = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result for result in results
        if isinstance(result, ArtifactConflictError)
    ]
    assert len(claims) == 1
    assert len(conflicts) == 1
    claim = claims[0]
    active = await repository.load_active(artifact_id)
    assert active == claim
    assert not await repository.release(ArtifactClaimLeaseCommand(
        artifact_id=artifact_id,
        run_id=claim.run_id,
        claim_token="wrong-token",
    ))
    assert await repository.release(ArtifactClaimLeaseCommand(
        artifact_id=artifact_id,
        run_id=claim.run_id,
        claim_token=claim.claim_token,
    ))
    reacquired = await acquire(claim.run_id)
    assert reacquired.run_id == claim.run_id
    assert await repository.release_for_run(claim.run_id) == 1
    assert await repository.load_active(artifact_id) is None
