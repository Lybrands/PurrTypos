"""Generic coordinator for checkpointed long-task execution units."""

from __future__ import annotations

import asyncio

from agent_core.long_tasks.contracts import LongTaskStatus
from agent_core.long_tasks.ports import LongTaskRepository, LongTaskUnitRunner
from agent_core.ports import CancellationSignal


class LongTaskCoordinator:
    def __init__(
        self,
        repository: LongTaskRepository,
        *,
        worker_id: str,
        lease_duration_ms: int = 300_000,
        retry_backoff_ms: tuple[int, ...] = (),
    ) -> None:
        self._repository = repository
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("long task coordinator worker_id is required")
        self._lease_duration_ms = int(lease_duration_ms)
        if self._lease_duration_ms <= 0:
            raise ValueError("long task coordinator lease must be positive")
        self._retry_backoff_ms = tuple(
            max(0, int(value)) for value in retry_backoff_ms
        )

    async def run(
        self,
        task_id: str,
        runner: LongTaskUnitRunner,
        signal: CancellationSignal | None = None,
    ):
        task = await self._require(task_id)
        if task.status is LongTaskStatus.PENDING:
            task = await self._repository.start(
                task.id,
                expected_revision=task.revision,
            )
        active: dict[asyncio.Task, object] = {}
        try:
            while task.status is LongTaskStatus.RUNNING:
                if signal is not None and signal.is_set():
                    return await self._stop_active(task.id, active)

                task = await self._require(task.id)
                if task.status is not LongTaskStatus.RUNNING:
                    break

                while len(active) < task.max_parallelism:
                    unit = await self._repository.claim_ready_unit(
                        task.id,
                        worker_id=self._worker_id,
                        lease_duration_ms=self._lease_duration_ms,
                    )
                    if unit is None:
                        break
                    execution = asyncio.create_task(
                        self._run_claimed_unit(task, unit, runner, signal)
                    )
                    active[execution] = unit

                if not active:
                    return await self._repository.finalize_if_complete(task.id)

                done, _ = await asyncio.wait(
                    tuple(active),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for execution in done:
                    active.pop(execution, None)
                    # _run_claimed_unit owns persistence and normalizes races;
                    # surfacing an unexpected infrastructure exception here is
                    # safer than silently abandoning the remaining branches.
                    await execution
                task = await self._require(task.id)
                if task.status is not LongTaskStatus.RUNNING and active:
                    await self._cancel_active(active)
            return task
        except asyncio.CancelledError:
            return await self._stop_active(task.id, active)
        finally:
            if active:
                await self._cancel_active(active)

    async def _run_claimed_unit(self, task, unit, runner, signal):
        try:
            if unit.attempt > 1 and unit.error_code:
                await self._wait_before_retry(unit.attempt - 1, signal)
            if signal is not None and signal.is_set():
                raise asyncio.CancelledError
            result = await runner.run_unit(task, unit, signal)
            settled = await self._repository.complete_unit(
                task.id,
                unit.id,
                worker_id=self._worker_id,
                result=result,
            )
            await self._notify_settled(runner, task.id)
            return settled
        except asyncio.CancelledError:
            return await self._checkpoint_interrupted(task.id, unit.id)
        except Exception as error:
            current = await self._require(task.id)
            if current.status is not LongTaskStatus.RUNNING:
                return current
            if signal is not None and signal.is_set():
                return await self._checkpoint_interrupted(task.id, unit.id)
            settled = await self._repository.fail_unit(
                task.id,
                unit.id,
                worker_id=self._worker_id,
                error_code=(str(error) or type(error).__name__)[:240],
                retryable=True,
            )
            await self._notify_settled(runner, task.id)
            return settled

    async def _checkpoint_interrupted(self, task_id: str, unit_id: str):
        current = await self._require(task_id)
        if current.status is not LongTaskStatus.RUNNING:
            return current
        return await self._repository.interrupt_unit(
            task_id,
            unit_id,
            worker_id=self._worker_id,
            reason_code="execution_interrupted",
        )

    async def _notify_settled(self, runner, task_id: str) -> None:
        callback = getattr(runner, "on_unit_settled", None)
        if callable(callback):
            await callback(task_id)

    async def _stop_active(self, task_id: str, active: dict):
        await self._cancel_active(active)
        current = await self._require(task_id)
        if current.status in {
            LongTaskStatus.PAUSED,
            LongTaskStatus.CANCELED,
        }:
            return current
        if current.status is LongTaskStatus.RUNNING:
            return await self._repository.pause(task_id)
        return current

    @staticmethod
    async def _cancel_active(active: dict) -> None:
        pending = tuple(active)
        active.clear()
        for execution in pending:
            if not execution.done():
                execution.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _wait_before_retry(self, attempt: int, signal) -> None:
        if not self._retry_backoff_ms:
            return
        index = min(max(0, int(attempt) - 1), len(self._retry_backoff_ms) - 1)
        delay_ms = self._retry_backoff_ms[index]
        if delay_ms <= 0:
            return
        if signal is None:
            await asyncio.sleep(delay_ms / 1000)
            return
        try:
            await asyncio.wait_for(signal.wait(), timeout=delay_ms / 1000)
        except TimeoutError:
            return

    async def _require(self, task_id: str):
        task = await self._repository.load(str(task_id or "").strip())
        if task is None:
            raise LookupError("long task does not exist")
        return task


__all__ = ["LongTaskCoordinator"]
