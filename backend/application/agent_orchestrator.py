"""Generic bounded dispatcher for claimed child Agent Runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agent_core.contracts import AgentRunResult, RunLineage, RunStatus
from agent_core.events import CoreEventType
from agent_core.ports import CancellationSignal
from application.agent_delegation_service import AgentDelegationService


ChildRunner = Callable[[dict, RunLineage], Awaitable[AgentRunResult]]
DelegationObserver = Callable[[CoreEventType, dict], Awaitable[None]]


class AgentOrchestrator:
    """Drain queued delegations without knowing any domain-specific roles."""

    def __init__(self, service: AgentDelegationService) -> None:
        self._service = service

    async def run_queued(
        self,
        *,
        parent_run_id: str,
        worker_id: str,
        max_parallel_children: int,
        runner: ChildRunner,
        observer: DelegationObserver | None = None,
        signal: CancellationSignal | None = None,
    ) -> dict:
        limit = int(max_parallel_children)
        if limit <= 0:
            raise ValueError("max parallel children must be positive")
        active: set[asyncio.Task[None]] = set()
        while True:
            while len(active) < limit and not _is_canceled(signal):
                claimed = await self._service.claim(
                    parent_run_id=parent_run_id,
                    worker_id=worker_id,
                    max_parallel_children=limit,
                )
                if claimed is None:
                    break
                view, lineage = claimed
                if observer is not None:
                    await observer(CoreEventType.DELEGATION_CLAIMED, {
                        **view,
                        "status": "claimed",
                    })
                active.add(asyncio.create_task(self._run_one(
                    view=view,
                    lineage=lineage,
                    worker_id=worker_id,
                    runner=runner,
                    observer=observer,
                )))
            if not active:
                break
            done, active = await asyncio.wait(
                active,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                await task
            if _is_canceled(signal):
                await self._service.cancel_children(parent_run_id)
                if active:
                    for task in active:
                        task.cancel()
                    await asyncio.gather(*active, return_exceptions=True)
                if observer is not None:
                    snapshot = await self._service.snapshot(parent_run_id)
                    for item in snapshot["items"]:
                        if item["status"] == "canceled":
                            await observer(
                                CoreEventType.DELEGATION_CANCELED,
                                item,
                            )
                break
        return await self._service.snapshot(parent_run_id)

    async def _run_one(
        self,
        *,
        view: dict,
        lineage: RunLineage,
        worker_id: str,
        runner: ChildRunner,
        observer: DelegationObserver | None,
    ) -> None:
        delegation_id = str(view["delegationId"])
        try:
            result = await runner(view, lineage)
            recorded = await self._service.record_result(
                delegation_id=delegation_id,
                child_run_id=result.run_id,
                result=result,
            )
            if not recorded:
                raise RuntimeError("child result did not match an active delegation")
            if observer is not None:
                if result.status is RunStatus.DONE:
                    event_type = CoreEventType.DELEGATION_COMPLETED
                    delegation_status = "done"
                elif result.status is RunStatus.CANCELED:
                    event_type = CoreEventType.DELEGATION_CANCELED
                    delegation_status = "canceled"
                else:
                    event_type = CoreEventType.DELEGATION_FAILED
                    delegation_status = "failed"
                await observer(
                    event_type,
                    {
                        **view,
                        "childRunId": result.run_id,
                        "status": delegation_status,
                        "resultSummary": result.final_response,
                        "error": (
                            None
                            if result.status is RunStatus.DONE
                            else result.error
                        ),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = str(error) or type(error).__name__
            await self._service.fail_claim(
                delegation_id=delegation_id,
                worker_id=worker_id,
                error=message,
            )
            if observer is not None:
                await observer(CoreEventType.DELEGATION_FAILED, {
                    **view,
                    "status": "failed",
                    "error": message,
                })


def _is_canceled(signal: CancellationSignal | None) -> bool:
    return bool(signal is not None and signal.is_set())
