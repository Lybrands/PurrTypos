"""Application-owned lifecycle for composed Agent Runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    RunStatus,
)
from purra.events import AgentEvent
from purra.output import (
    AgentOutputEvent,
    OutputEventKind,
    ResponseTransactionMode,
)
from purra.api import (
    AgentCoreRunOptions,
    AgentModelTask,
    AgentModelTextResult,
)
from purra.model_protocol import FeatureSupport
from purra.ports import CancellationSignal
from application.agent_composition import AgentComposition
from application.run_binding import RunBindingLifecycle
from infrastructure.models.capabilities import normalize_thinking_enabled
AgentRunUpdate = AgentOutputEvent | AgentRunResult


class AgentRunService:
    """Drive a prepared Core Run through submit, subscription, and completion."""

    def __init__(self, composition: AgentComposition) -> None:
        self._composition = composition

    async def run_operation(self, *, request, options, run_id, operation_id, api_key, signal=None):
        composition = self._composition
        request = await composition.prepare_request(request, run_id=run_id)
        options = composition.bind_run_profile(request, options)
        core = composition.create_core_for_request(request, api_key, agent_tree_enabled=False)
        try:
            from application.public_commentary_output import private_model_operation
            with private_model_operation():
                return await core.execute_operation(request, run_id=run_id, operation_id=operation_id,
                                                    options=options, signal=signal)
        finally:
            await core.close()
            composition.release_core(core)

    async def read_validated_result(self, run_id: str) -> str:
        """Read the private canonical result without replaying execution."""

        return await self._composition.output_repository.load_validated_result(
            run_id
        )

    async def run_model_text(
        self,
        *,
        run_id: str,
        turn_id: str,
        api_key: str,
        messages: Sequence[AgentMessage],
        model_request,
        reasoning_mode,
        signal: CancellationSignal | None,
    ) -> AgentModelTextResult:
        """Run one private model task inside an existing durable Run."""

        runner = await self._composition.create_model_task_runner(
            api_key=api_key,
            run_id=run_id,
            turn_id=turn_id,
            reasoning_mode=reasoning_mode,
            model_request=model_request,
        )
        return await runner.stream_text(
            messages,
            AgentModelTask(
                request=model_request,
            ),
            signal,
        )

    async def report_agent_results(self, run_id, results, signal=None):
        await self._composition.report_agent_results(run_id, results, signal)

    async def run(
        self,
        *,
        request: AgentRunRequest,
        api_key: str,
        options: AgentCoreRunOptions,
        signal: CancellationSignal,
        run_binding_lifecycle: RunBindingLifecycle | None = None,
        long_task_executor=None,
        cancellation_reason: str | None = None,
    ) -> AsyncIterator[AgentRunUpdate]:
        composition = self._composition
        provider_capabilities = composition.provider_capabilities
        if run_binding_lifecycle is not None:
            await run_binding_lifecycle.validate()
        request = await composition.prepare_request(request)
        model_options = dict(request.model.options)
        capability_key = provider_capabilities.key(
            api_provider=request.model.provider,
            base_url=str(model_options.get("baseURL") or ""),
            model=request.model.model,
            thinking_enabled=normalize_thinking_enabled(model_options),
        )
        force_planned_tool_choice = (
            request.model.protocol_capabilities.required_tool_choice
            is not FeatureSupport.UNAVAILABLE
            and not provider_capabilities.required_tool_choice_is_unsupported(
                capability_key
            )
        )
        options = replace(
            options,
            force_planned_tool_choice=force_planned_tool_choice,
            response_judge_policies=(
                *options.response_judge_policies,
                *composition.create_response_judge_policies(request),
            ),
        )
        options = composition.bind_run_profile(request, options)

        create_core_kwargs = {
            "on_required_tool_choice_unsupported": (
                lambda: provider_capabilities.mark_required_tool_choice_unsupported(
                    capability_key
                )
            ),
        }
        if options.agent_tree_run_id is not None:
            create_core_kwargs["agent_tree_enabled"] = True
        if long_task_executor is not None:
            create_core_kwargs["long_task_executor"] = long_task_executor
        core = composition.create_core_for_request(
            request,
            api_key,
            **create_core_kwargs,
        )
        try:
            if run_binding_lifecycle is not None:
                await run_binding_lifecycle.before_submit()
        except BaseException as error:
            release_core = getattr(composition, "release_core", None)
            if callable(release_core):
                release_core(core)
            if run_binding_lifecycle is not None:
                await run_binding_lifecycle.on_start_failed(
                    str(getattr(error, "code", "") or type(error).__name__)
                )
            raise
        try:
            handle = await core.submit(request, options=options)
        except BaseException as error:
            # submit() may already have spawned a shielded supervisor task.
            # Closing the Core is the only safe ownership conclusion; merely
            # discarding it can orphan execution during request cancellation.
            await core.close()
            release_core = getattr(composition, "release_core", None)
            if callable(release_core):
                release_core(core)
            if run_binding_lifecycle is not None:
                await run_binding_lifecycle.on_start_failed(
                    str(getattr(error, "code", "") or type(error).__name__)
                )
            raise
        cancel_watcher = (
            asyncio.create_task(
                _cancel_run_on_signal(signal, handle, cancellation_reason)
            )
            if cancellation_reason is not None
            else None
        )
        try:
            core_stream = handle.subscribe(after_sequence=0)
        except BaseException:
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                await asyncio.gather(cancel_watcher, return_exceptions=True)
            _track_core_until_terminal(composition, core, handle)
            raise
        terminal = False
        try:
            if run_binding_lifecycle is not None:
                try:
                    await run_binding_lifecycle.on_run_started(handle.run_id)
                except BaseException as error:
                    await handle.cancel("run_binding_failed")
                    with suppress(BaseException):
                        await handle.wait()
                    await run_binding_lifecycle.on_start_failed(
                        str(getattr(error, "code", "") or type(error).__name__)
                    )
                    terminal = True
                    raise
            async for update in core_stream:
                if (
                    isinstance(update, AgentOutputEvent)
                    and update.kind is OutputEventKind.RUNTIME
                ):
                    runtime_type = str(
                        update.payload.get("eventType") or ""
                    ).strip()
                    runtime_payload = update.payload.get("data")
                    if runtime_type and isinstance(runtime_payload, Mapping):
                        composition.observe_event(AgentEvent(
                            type=runtime_type,
                            run_id=update.run_id,
                            payload=runtime_payload,
                        ))
                yield update
            result = await handle.wait()
            if (
                result.status is RunStatus.DONE
                and options.resolved_response_transaction_policy.mode
                is ResponseTransactionMode.VALIDATED_RESULT
            ):
                result = replace(
                    result,
                    validated_result=await self.read_validated_result(
                        result.run_id
                    ),
                )
            if run_binding_lifecycle is not None:
                await run_binding_lifecycle.on_run_finished(result)
            terminal = True
            yield result
        finally:
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                await asyncio.gather(cancel_watcher, return_exceptions=True)
            await core_stream.aclose()
            if terminal:
                release_core = getattr(composition, "release_core", None)
                if callable(release_core):
                    release_core(core)
            else:
                # The durable Run may continue after a bind/SSE failure. Keep
                # the Core owned until its handle actually settles; shutdown
                # can still cancel this tracked waiter and close the Core.
                _track_core_until_terminal(composition, core, handle)


async def _cancel_run_on_signal(signal, handle, reason: str) -> None:
    await signal.wait()
    await handle.cancel(reason)


def _track_core_until_terminal(composition, core, handle) -> None:
    async def _wait_and_release() -> None:
        try:
            await handle.wait()
        except asyncio.CancelledError:
            # Composition shutdown gathers background waiters before closing
            # active Cores. Keep ownership registered so that second phase can
            # close the underlying durable Run task.
            raise
        except BaseException:
            release_core = getattr(composition, "release_core", None)
            if callable(release_core):
                release_core(core)
            raise
        else:
            release_core = getattr(composition, "release_core", None)
            if callable(release_core):
                release_core(core)

    task = asyncio.create_task(_wait_and_release())
    track_background = getattr(composition, "track_background_run", None)
    if callable(track_background):
        track_background(task)
