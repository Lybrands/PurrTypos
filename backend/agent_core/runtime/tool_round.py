"""Cancellation-linearizable execution of one Runtime tool batch."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from agent_core.cancellation import OperationCanceled
from agent_core.contracts import ToolBatchRequest, ToolBatchResult
from agent_core.events import AgentEvent
from agent_core.ports import CancellationSignal, ToolExecutionGateway


class _QueueEventSink:
    def __init__(self, queue: asyncio.Queue[AgentEvent]):
        self._queue = queue

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)


async def stream_tool_batch(
    gateway: ToolExecutionGateway,
    request: ToolBatchRequest,
    signal: CancellationSignal | None,
) -> AsyncIterator[AgentEvent | ToolBatchResult]:
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
    gateway_task = asyncio.create_task(
        gateway.execute_batch(request, _QueueEventSink(queue), signal)
    )
    progress_task: asyncio.Task[AgentEvent] | None = asyncio.create_task(queue.get())
    signal_task: asyncio.Task[bool] | None = (
        asyncio.create_task(signal.wait()) if signal is not None else None
    )
    gateway_terminal_selected = False
    try:
        while True:
            # A committed result/exception wins over same-tick progress and
            # cancellation. Progress already emitted by that gateway is still
            # drained before its terminal update.
            if gateway_task.done():
                gateway_terminal_selected = True
                terminal_result: ToolBatchResult | None = None
                terminal_error: BaseException | None = None
                try:
                    terminal_result = gateway_task.result()
                except BaseException as error:
                    terminal_error = error

                if progress_task is not None and progress_task.done():
                    try:
                        yield progress_task.result()
                    except BaseException:
                        pass
                    progress_task = None
                while not queue.empty():
                    yield queue.get_nowait()
                if terminal_error is not None:
                    raise terminal_error
                if terminal_result is None:
                    raise RuntimeError("tool gateway returned no result")
                yield terminal_result
                return

            # Progress wins over cancellation when both become observable in
            # the same event-loop turn.
            if progress_task is not None and progress_task.done():
                yield progress_task.result()
                progress_task = asyncio.create_task(queue.get())
                continue

            if _is_canceled(signal):
                # The gateway owns the authoritative tool outcome. A
                # cancellation-linearizable handler may already be committing;
                # after cancellation is forwarded, preserve a durable receipt
                # instead of unconditionally rewriting it as canceled.
                terminal_result = await _cancel_gateway_for_result(gateway_task)
                if terminal_result is None:
                    raise OperationCanceled("agent run was canceled")
                gateway_terminal_selected = True
                if progress_task is not None and progress_task.done():
                    try:
                        yield progress_task.result()
                    except BaseException:
                        pass
                    progress_task = None
                while not queue.empty():
                    yield queue.get_nowait()
                yield terminal_result
                return

            waiters: set[asyncio.Future[Any]] = {gateway_task}
            if progress_task is not None:
                waiters.add(progress_task)
            if signal_task is not None:
                waiters.add(signal_task)
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        # A caller cancellation is also forwarded to the gateway. If the
        # gateway suppresses it because a durable tool commit is already in
        # flight, finish delivering that authoritative receipt instead of
        # exposing a false non-commit to the caller.
        terminal_result = await _cancel_gateway_for_result(gateway_task)
        if terminal_result is None:
            raise
        gateway_terminal_selected = True
        if progress_task is not None and progress_task.done():
            try:
                yield progress_task.result()
            except BaseException:
                pass
            progress_task = None
        while not queue.empty():
            yield queue.get_nowait()
        yield terminal_result
        return
    finally:
        await _cancel_task_safely(progress_task)
        await _cancel_task_safely(signal_task)
        if not gateway_terminal_selected:
            await _cancel_task_safely(gateway_task)


async def _cancel_task_safely(task: asyncio.Future[Any] | None) -> None:
    """Cleanup must never replace the already selected runtime outcome."""

    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:
        pass


async def _cancel_gateway_for_result(
    task: asyncio.Task[ToolBatchResult],
) -> ToolBatchResult | None:
    """Cancel a gateway but retain a commit-wins result if it returns one."""

    if not task.done():
        task.cancel()
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return None
            if task.done():
                return task.result()
            task.cancel()
        except BaseException:
            # Once the signal branch has selected cancellation, cleanup
            # failures are private unless the gateway returns an authoritative
            # ToolBatchResult receipt.
            return None


async def close_async_iterator(iterator: AsyncIterator) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except (GeneratorExit, StopAsyncIteration):
        pass
    except Exception:
        # Stream termination has already been classified by the runtime. A
        # provider cleanup failure must not replace that public outcome.
        pass


def _is_canceled(signal: CancellationSignal | None) -> bool:
    return bool(signal is not None and signal.is_set())
