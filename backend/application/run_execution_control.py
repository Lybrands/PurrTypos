"""Application lifecycle for durable Run leases and cancellation."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from purra.ports import CancellationSignal
from purra.ports import ExecutionLeaseStore


class DurableCancellationSignal:
    """Combine transport cancellation with a durable cancel request."""

    def __init__(self, external: CancellationSignal) -> None:
        self._external = external
        self._durable = asyncio.Event()

    def is_set(self) -> bool:
        return self._external.is_set() or self._durable.is_set()

    async def wait(self) -> bool:
        if self.is_set():
            return True
        external_task = asyncio.create_task(self._external.wait())
        durable_task = asyncio.create_task(self._durable.wait())
        try:
            await asyncio.wait(
                (external_task, durable_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            return True
        finally:
            for task in (external_task, durable_task):
                task.cancel()
            for task in (external_task, durable_task):
                with suppress(asyncio.CancelledError):
                    await task

    def cancel_from_control_plane(self) -> None:
        self._durable.set()


class RunExecutionSession:
    """Heartbeat one owned Run and fail closed if its lease is lost."""

    def __init__(
        self,
        lease_store: ExecutionLeaseStore,
        *,
        owner_id: str,
        lease_duration_ms: int,
        external_signal: CancellationSignal,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self._lease_store = lease_store
        self._owner_id = owner_id
        self._lease_duration_ms = int(lease_duration_ms)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._signal = DurableCancellationSignal(external_signal)
        self._run_id: str | None = None
        self._monitor: asyncio.Task[None] | None = None

    @property
    def signal(self) -> DurableCancellationSignal:
        return self._signal

    async def bind(self, run_id: str) -> None:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run id is required")
        if self._run_id is not None:
            if self._run_id != normalized:
                raise RuntimeError("execution session cannot bind multiple runs")
            return
        state = await self._lease_store.get(normalized)
        if state is None or state.owner_id != self._owner_id:
            raise RuntimeError("run execution lease is not owned by this session")
        self._run_id = normalized
        self._monitor = asyncio.create_task(self._monitor_run())

    async def close(self) -> None:
        monitor = self._monitor
        self._monitor = None
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        if self._run_id is not None:
            await self._lease_store.release(self._run_id, self._owner_id)

    async def _monitor_run(self) -> None:
        assert self._run_id is not None
        heartbeat_interval = max(0.05, self._lease_duration_ms / 3_000)
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + heartbeat_interval
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            state = await self._lease_store.get(self._run_id)
            if state is None or state.cancellation_requested_at_ms is not None:
                self._signal.cancel_from_control_plane()
                return
            if loop.time() < next_heartbeat:
                continue
            renewed = await self._lease_store.renew(
                self._run_id,
                self._owner_id,
                lease_duration_ms=self._lease_duration_ms,
            )
            if not renewed:
                self._signal.cancel_from_control_plane()
                return
            next_heartbeat = loop.time() + heartbeat_interval
