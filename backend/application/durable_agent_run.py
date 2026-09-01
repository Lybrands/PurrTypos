"""Shared bound Agent execution for host-owned durable units."""

from __future__ import annotations

import asyncio

from purra.contracts import AgentRunResult, RunStatus
from purra.errors import ModelGatewayError


class _BindDurableUnitRun:
    def __init__(self, bind_run) -> None:
        self._bind_run = bind_run

    async def validate(self) -> None:
        pass

    async def before_submit(self) -> None:
        pass

    async def on_run_started(self, run_id: str) -> None:
        await self._bind_run(run_id)

    async def on_run_finished(self, result: AgentRunResult) -> None:
        pass

    async def on_start_failed(self, code: str) -> None:
        pass


async def run_durable_agent_unit(
    *, db, runs, request, options, api_key, signal, bind_run,
    active_error_code="durable_unit_run_active",
) -> AgentRunResult:
    binding = options.binding
    if binding is None:
        raise ValueError("durable unit Run requires a binding")
    existing = await db.fetch_one(
        "SELECT id, status, final_response, model_name "
        "FROM ai_agent_runs WHERE binding_namespace = ? "
        "AND binding_aggregate_id = ? AND binding_command_id = ? "
        "ORDER BY rowid DESC LIMIT 1",
        [binding.namespace, binding.aggregate_id, binding.command_id],
    )
    if existing is not None and existing["status"] == "done":
        run_id = str(existing["id"])
        if bind_run is not None:
            await bind_run(run_id)
        return AgentRunResult(
            run_id=run_id, status=RunStatus.DONE,
            final_response=str(existing.get("final_response") or ""),
            model=str(existing.get("model_name") or request.model.model),
        )
    if existing is not None and existing["status"] == "running":
        raise ModelGatewayError(
            "the durable unit already has an active model Run",
            code=active_error_code, retryable=True,
        )
    terminal = None
    async for update in runs.run(
        request=request, api_key=api_key, options=options,
        signal=signal or asyncio.Event(),
        run_binding_lifecycle=(
            _BindDurableUnitRun(bind_run) if bind_run is not None else None
        ),
        cancellation_reason="durable_unit_canceled" if signal is not None else None,
    ):
        if isinstance(update, AgentRunResult):
            terminal = update
    if terminal is None:
        raise RuntimeError("durable unit Run completed without a result")
    return terminal
