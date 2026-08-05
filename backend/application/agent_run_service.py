"""Application-owned lifecycle for composed parent and child Agent Runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace

from agent_core.contracts import (
    AgentMessage,
    AgentRunResult,
    MessageOrigin,
    MessageRole,
    RunLineage,
    RunProvenance,
    ToolExecutionMode,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import CancellationSignal, ResponseValidator
from application.agent_composition import AgentComposition
from application.agent_delegation_service import AgentDelegationService
from application.agent_delegation_tool import (
    build_delegation_tool_registration,
)
from application.request_mapping import (
    agent_context_claims,
    agent_run_options,
    to_agent_request,
)
from application.run_provenance import build_chat_run_provenance
from infrastructure.models.capabilities import normalize_thinking_enabled
from schemas.ai import ChatStreamRequest


AgentRunUpdate = AgentEvent | AgentRunResult
_QUEUE_END = object()


@dataclass(frozen=True, slots=True)
class _PumpFailure:
    error: Exception


class AgentRunService:
    """Drive composed runs and multiplex live parent delegation events."""

    def __init__(self, composition: AgentComposition) -> None:
        self._composition = composition

    async def run(
        self,
        *,
        body: ChatStreamRequest,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal,
        provenance: RunProvenance | None = None,
        lineage: RunLineage | None = None,
        enable_delegation: bool = True,
        allowed_tool_modes: frozenset[ToolExecutionMode] | None = None,
        host_system_instruction: str | None = None,
        response_validators: Sequence[ResponseValidator] = (),
    ) -> AsyncIterator[AgentRunUpdate]:
        composition = self._composition
        provider_capabilities = composition.provider_capabilities
        request = to_agent_request(body, provider_options)
        prepare_request = getattr(composition, "prepare_request", None)
        if callable(prepare_request):
            request = await prepare_request(request)
        static_context_claims = agent_context_claims(request)
        trusted_instruction = str(host_system_instruction or "").strip()
        if trusted_instruction:
            trusted_message = AgentMessage(
                role=MessageRole.SYSTEM,
                content=trusted_instruction,
                origin=MessageOrigin.HOST_CONTEXT,
            )
            request = replace(
                request,
                messages=(
                    trusted_message,
                    *request.messages,
                ),
            )
        run_provenance = provenance or build_chat_run_provenance(body)
        capability_key = provider_capabilities.key(
            api_provider=body.apiProvider,
            base_url=str(provider_options.get("baseURL") or ""),
            model=request.model.model,
            thinking_enabled=normalize_thinking_enabled(provider_options),
        )
        response_judges = composition.create_response_judges(api_key, request)
        options = agent_run_options(
            request,
            provider_options,
            force_planned_tool_choice=(
                not provider_capabilities.required_tool_choice_is_unsupported(
                    capability_key
                )
            ),
            provenance=run_provenance,
            lineage=lineage,
            response_judges=response_judges,
        )
        options = replace(
            options,
            context_claims=static_context_claims,
            response_validators=(
                *options.response_validators,
                *tuple(response_validators),
            ),
        )

        queue: asyncio.Queue[AgentRunUpdate | _PumpFailure | object] = asyncio.Queue()

        async def publish(event: AgentEvent) -> None:
            await queue.put(event)

        can_delegate = bool(
            enable_delegation
            and request.tools_enabled
            and request.mode == "agent"
            and hasattr(composition, "delegation_repository")
        )
        extra_registrations = ()
        if can_delegate:
            registry_for_request = getattr(
                composition,
                "agent_role_registry_for_request",
                None,
            )
            role_registry = (
                registry_for_request(request)
                if callable(registry_for_request)
                else composition.agent_role_registry
            )
            delegation_service = AgentDelegationService(
                composition.delegation_repository,
                role_registry=role_registry,
            )

            async def run_child(view: dict, child_lineage: RunLineage) -> AgentRunResult:
                role = str(view["agentRole"])
                role_definition = role_registry.require(role)
                objective = str(view["objective"])
                input_payload = view.get("input")
                child_prompt = objective
                if isinstance(input_payload, dict) and input_payload:
                    child_prompt += "\n\nStructured input data:\n" + json.dumps(
                        input_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                child_body = body.model_copy(update={
                    "messages": [
                        {"role": "user", "content": child_prompt},
                    ],
                    "sessionId": None,
                    "chatAgentMode": "agent",
                })
                child_result: AgentRunResult | None = None
                child_stream = AgentRunService(composition).run(
                    body=child_body,
                    api_key=api_key,
                    provider_options=provider_options,
                    signal=signal,
                    provenance=build_chat_run_provenance(child_body),
                    lineage=child_lineage,
                    enable_delegation=False,
                    allowed_tool_modes=role_definition.allowed_tool_modes,
                    host_system_instruction=role_definition.instruction,
                )
                try:
                    async for child_update in child_stream:
                        if isinstance(child_update, AgentEvent):
                            if child_update.type == "run.started":
                                await publish(AgentEvent(
                                    type="delegation.claimed",
                                    run_id=child_lineage.parent_run_id,
                                    payload={
                                        **view,
                                        "childRunId": child_update.run_id,
                                        "status": "running",
                                    },
                                ))
                            # A child Run uses the same canonical Core events as
                            # its parent.  Preserve that event verbatim inside a
                            # delegation envelope instead of discarding its
                            # model/tool activity or inventing a second stream
                            # protocol for multi-Agent UI consumers.
                            delegation_event = AgentEvent(
                                type="delegation.event",
                                run_id=child_lineage.parent_run_id,
                                payload={
                                    **view,
                                    "childRunId": (
                                        child_update.run_id
                                        or view.get("childRunId")
                                    ),
                                    "event": {
                                        "type": str(child_update.type),
                                        "runId": str(
                                            child_update.run_id or ""
                                        ),
                                        "payload": dict(
                                            child_update.payload
                                        ),
                                    },
                                },
                            )
                            # The live queue is only a transport.  Persist the
                            # same canonical envelope on the parent Run so a
                            # reconnect/replay sees the exact child lifecycle,
                            # tool and terminal events that the live UI saw.
                            # Raw token deltas intentionally remain transport-
                            # only, matching the persistence policy of an
                            # ordinary (non-delegated) Run; run.completed owns
                            # the durable final response snapshot.
                            if child_update.type not in {
                                CoreEventType.MODEL_DELTA,
                                CoreEventType.MODEL_THINKING_DELTA,
                            }:
                                await composition.append_run_event(
                                    str(child_lineage.parent_run_id),
                                    CoreEventType.DELEGATION_EVENT,
                                    dict(delegation_event.payload),
                                )
                            await publish(delegation_event)
                        if isinstance(child_update, AgentRunResult):
                            child_result = child_update
                finally:
                    await child_stream.aclose()
                if child_result is None:
                    raise RuntimeError("child Agent returned no terminal result")
                return child_result

            extra_registrations = (build_delegation_tool_registration(
                service=delegation_service,
                role_registry=role_registry,
                worker_id=composition.execution_owner_id,
                runner=run_child,
                publish=publish,
            ),)

        create_core_kwargs = {
            "on_required_tool_choice_unsupported": (
                lambda: provider_capabilities.mark_required_tool_choice_unsupported(
                    capability_key
                )
            ),
        }
        if hasattr(composition, "execute_screenplay_long_task"):
            async def execute_long_task(
                task_id,
                parent_run_id,
                observer,
                long_task_signal,
            ):
                return await composition.execute_screenplay_long_task(
                    task_id,
                    parent_run_id=parent_run_id,
                    observer=observer,
                    body=body,
                    api_key=api_key,
                    provider_options=provider_options,
                    signal=long_task_signal,
                )
            create_core_kwargs["long_task_executor"] = execute_long_task
        if can_delegate or allowed_tool_modes is not None:
            create_core_kwargs.update({
                "extra_tool_registrations": extra_registrations,
                "allowed_tool_modes": allowed_tool_modes,
            })
        core_for_request = getattr(
            composition,
            "create_core_for_request",
            None,
        )
        core = (
            core_for_request(request, api_key, **create_core_kwargs)
            if callable(core_for_request)
            else composition.create_core(api_key, **create_core_kwargs)
        )
        session_factory = getattr(composition, "create_execution_session", None)
        execution_session = (
            session_factory(signal) if session_factory is not None else None
        )
        execution_signal = (
            execution_session.signal if execution_session is not None else signal
        )
        core_stream = core.run(request, options=options, signal=execution_signal)
        run_id: str | None = None

        async def pump_core() -> None:
            nonlocal run_id
            try:
                async for update in core_stream:
                    if isinstance(update, AgentEvent):
                        composition.observe_event(update)
                    run_id = update.run_id or run_id
                    if execution_session is not None and run_id:
                        await execution_session.bind(run_id)
                    await queue.put(update)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await queue.put(_PumpFailure(error))
            finally:
                try:
                    await core_stream.aclose()
                finally:
                    try:
                        if run_id:
                            await composition.release_run(run_id)
                    finally:
                        if execution_session is not None:
                            await execution_session.close()
                await queue.put(_QUEUE_END)

        pump_task = asyncio.create_task(pump_core())
        try:
            while True:
                update = await queue.get()
                if update is _QUEUE_END:
                    break
                if isinstance(update, _PumpFailure):
                    raise update.error
                if isinstance(update, (AgentEvent, AgentRunResult)):
                    yield update
        finally:
            if not pump_task.done():
                pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
