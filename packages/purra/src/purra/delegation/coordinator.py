"""Generic parent/child Run coordination over stable PurrA contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from purra.contracts import (
    AgentDelegation,
    AgentRunRequest,
    AgentRunResult,
    DelegationAggregation,
    DelegationClaim,
    RunId,
    RunStatus,
)
from purra.errors import ContractViolationError
from purra.execution import AgentRunHandle
from purra.operations import (
    AgentOperationController,
    OperationDisplay,
    OperationKind,
    OperationScope,
)
from purra.output import AgentOutputEvent, FederatedOutputEvent
from purra.output.processor import AgentOutputProcessor
from purra.ports import DelegationRepository


@runtime_checkable
class ChildRunRequestFactory(Protocol):
    async def build(
        self,
        claim: DelegationClaim,
    ) -> tuple[AgentRunRequest, object]: ...


@runtime_checkable
class AgentCoreSubmitter(Protocol):
    async def submit(
        self,
        request: AgentRunRequest,
        *,
        options: object | None = None,
    ) -> AgentRunHandle: ...


class AgentDelegationCoordinator:
    """Own claims, child handles, event federation and result settlement."""

    def __init__(
        self,
        *,
        repository: DelegationRepository,
        core: AgentCoreSubmitter,
        output_processor: AgentOutputProcessor,
        operation_controller: AgentOperationController,
        child_request_factory: ChildRunRequestFactory,
        worker_id: str,
        max_parallel_children: int = 3,
        max_depth: int = 3,
    ) -> None:
        for method in (
            "create",
            "claim",
            "record_result",
            "fail",
            "cancel_children",
            "aggregate",
        ):
            if not callable(getattr(repository, method, None)):
                raise TypeError(
                    "delegation coordinator requires a repository with "
                    f"{method}()"
                )
        if not isinstance(core, AgentCoreSubmitter):
            raise TypeError("delegation coordinator requires AgentCore.submit")
        if not isinstance(child_request_factory, ChildRunRequestFactory):
            raise TypeError("delegation coordinator requires a child request factory")
        self._repository = repository
        self._core = core
        self._output = output_processor
        self._operations = operation_controller
        self._factory = child_request_factory
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("delegation worker id is required")
        self._max_parallel_children = int(max_parallel_children)
        if self._max_parallel_children <= 0:
            raise ValueError("max parallel children must be positive")
        self._max_depth = int(max_depth)
        if self._max_depth <= 0:
            raise ValueError("delegation max depth must be positive")
        self._active: dict[RunId, dict[str, AgentRunHandle]] = {}
        self._settlements: set[asyncio.Task[AgentRunResult]] = set()
        self._canceled_parents: set[RunId] = set()

    async def create(
        self,
        *,
        parent_run_id: RunId,
        agent_role: str,
        objective: str,
        input_payload=None,
        required: bool = True,
        priority: int = 0,
    ) -> AgentDelegation:
        return await self._repository.create(
            parent_run_id=parent_run_id,
            agent_role=agent_role,
            objective=objective,
            input_payload=input_payload,
            required=required,
            priority=priority,
            max_depth=self._max_depth,
        )

    async def claim_and_submit(
        self,
        delegation_id: str,
        *,
        parent_run_id: RunId,
    ) -> AgentRunHandle:
        claim = await self._repository.claim(
            delegation_id=delegation_id,
            parent_run_id=parent_run_id,
            worker_id=self._worker_id,
            max_parallel_children=self._max_parallel_children,
        )
        if claim is None:
            raise ContractViolationError(
                "delegation could not be claimed for child execution"
            )
        operation = await self._operations.start(
            OperationKind.DELEGATION,
            OperationScope(
                run_id=parent_run_id,
                display=OperationDisplay(
                    label_key="agent.operation.delegation",
                    label_params={
                        "agentRole": claim.delegation.agent_role,
                        "delegationId": claim.delegation.id,
                    },
                ),
            ),
        )
        try:
            request, options = await self._factory.build(claim)
            if not isinstance(request, AgentRunRequest):
                raise ContractViolationError(
                    "child request factory returned an invalid request"
                )
            child = await self._core.submit(request, options=options)
        except BaseException as error:
            await self._repository.fail(
                delegation_id=claim.delegation.id,
                worker_id=self._worker_id,
                error=str(error) or type(error).__name__,
            )
            await self._operations.fail(
                operation.operation_id,
                "delegation_submit_failed",
            )
            raise

        self._active.setdefault(parent_run_id, {})[
            claim.delegation.id
        ] = child
        settlement = asyncio.create_task(self._settle_child(
            claim,
            child,
            operation.operation_id,
        ))
        self._settlements.add(settlement)
        settlement.add_done_callback(self._settlements.discard)
        return _CoordinatedChildHandle(child, settlement)

    async def record_result(
        self,
        delegation_id: str,
        child_run_id: RunId,
        result: AgentRunResult,
    ) -> bool:
        return await self._repository.record_result(
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            result=result,
        )

    async def cancel_children(self, parent_run_id: RunId) -> int:
        if parent_run_id in self._canceled_parents:
            return 0
        self._canceled_parents.add(parent_run_id)
        canceled = await self._repository.cancel_children(parent_run_id)
        for handle in tuple(self._active.get(parent_run_id, {}).values()):
            await handle.cancel("parent_canceled")
        return canceled

    async def aggregate(self, parent_run_id: RunId) -> DelegationAggregation:
        return await self._repository.aggregate(parent_run_id)

    async def _settle_child(
        self,
        claim: DelegationClaim,
        child: AgentRunHandle,
        operation_id: str,
    ) -> AgentRunResult:
        parent_run_id = claim.delegation.parent_run_id
        try:
            async for event in child.subscribe(after_sequence=0):
                await self._output.accept_federated_event(
                    FederatedOutputEvent(
                        parent_run_id=parent_run_id,
                        delegation_id=claim.delegation.id,
                        source_event=event,
                    )
                )
            result = await child.wait()
            recorded = await self.record_result(
                claim.delegation.id,
                child.run_id,
                result,
            )
            if not recorded and result.status is not RunStatus.CANCELED:
                raise ContractViolationError(
                    "child result did not settle the active delegation"
                )
            if result.status is RunStatus.DONE:
                await self._operations.succeed(operation_id)
            elif result.status is RunStatus.CANCELED:
                await self._operations.cancel(
                    operation_id,
                    result.error or "child_run_canceled",
                )
            else:
                await self._operations.fail(
                    operation_id,
                    result.error or "child_run_failed",
                )
            return result
        except asyncio.CancelledError:
            await child.cancel("delegation_coordinator_stopped")
            await self._operations.cancel(
                operation_id,
                "delegation_coordinator_stopped",
            )
            raise
        except BaseException as error:
            await self._repository.fail(
                delegation_id=claim.delegation.id,
                worker_id=self._worker_id,
                error=str(error) or type(error).__name__,
            )
            await self._operations.fail(
                operation_id,
                "delegation_coordination_failed",
            )
            raise
        finally:
            active = self._active.get(parent_run_id)
            if active is not None:
                active.pop(claim.delegation.id, None)
                if not active:
                    self._active.pop(parent_run_id, None)


class _CoordinatedChildHandle:
    def __init__(
        self,
        child: AgentRunHandle,
        settlement: asyncio.Task[AgentRunResult],
    ) -> None:
        self._child = child
        self._settlement = settlement

    @property
    def run_id(self) -> RunId:
        return self._child.run_id

    def subscribe(
        self,
        after_sequence: int = 0,
    ) -> AsyncIterator[AgentOutputEvent]:
        return self._child.subscribe(after_sequence=after_sequence)

    async def wait(self) -> AgentRunResult:
        return await asyncio.shield(self._settlement)

    async def cancel(self, reason: str) -> None:
        await self._child.cancel(reason)


__all__ = [
    "AgentCoreSubmitter",
    "AgentDelegationCoordinator",
    "ChildRunRequestFactory",
]
