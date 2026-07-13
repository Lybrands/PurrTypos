"""Core-owned cancellation primitives for adapter operations."""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

from agent_core.errors import AgentCoreError
from agent_core.ports import CancellationSignal


T = TypeVar("T")


class OperationCanceled(AgentCoreError):
    """An in-flight Core operation was canceled by its run signal."""


async def cancel_and_wait(task: asyncio.Future) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def await_with_cancellation(
    awaitable: Awaitable[T],
    signal: CancellationSignal | None,
) -> T:
    """Await an adapter operation while Core owns liveness and cleanup."""

    operation = asyncio.ensure_future(awaitable)
    cancel_waiter: asyncio.Task[bool] | None = None
    try:
        if signal is None:
            return await operation
        if signal.is_set():
            await cancel_and_wait(operation)
            raise OperationCanceled("agent run was canceled")

        cancel_waiter = asyncio.create_task(signal.wait())
        done, _ = await asyncio.wait(
            {operation, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Once an operation has completed, its result wins: a tool side effect
        # may already have committed and must not be reported as canceled.
        if operation in done:
            return await operation
        if signal.is_set():
            await cancel_and_wait(operation)
            raise OperationCanceled("agent run was canceled")
        return await operation
    except asyncio.CancelledError:
        await cancel_and_wait(operation)
        raise
    finally:
        if cancel_waiter is not None:
            await cancel_and_wait(cancel_waiter)
