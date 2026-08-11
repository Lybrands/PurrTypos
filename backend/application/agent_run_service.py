"""Application-owned lifecycle for composed parent and child Agent Runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    ContextBundle,
    DomainContext,
    MessageOrigin,
    MessageRole,
    RunLineage,
    RunProvenance,
    ToolExecutionMode,
)
from purra.events import AgentEvent
from purra.json_values import thaw_json_mapping
from purra.output import AgentOutputEvent, OutputEventKind
from purra.api import AgentCoreRunOptions
from purra.ports import CancellationSignal, ResponseValidator
from application.agent_composition import AgentComposition
from application.agent_delegation_adapter import ApplicationDelegationAdapter
from application.agent_run_input import AgentRunInput
from application.request_mapping import (
    agent_context_claims,
    agent_run_options,
    to_agent_request,
)
from application.run_provenance import build_chat_run_provenance
from application.run_binding import RunBindingLifecycle
from infrastructure.models.capabilities import normalize_thinking_enabled
AgentRunUpdate = AgentOutputEvent | AgentRunResult


class AgentRunService:
    """Drive composed runs and multiplex live parent delegation events."""

    def __init__(self, composition: AgentComposition) -> None:
        self._composition = composition

    async def run(
        self,
        *,
        body: AgentRunInput,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal,
        provenance: RunProvenance | None = None,
        lineage: RunLineage | None = None,
        enable_delegation: bool = True,
        allowed_tool_modes: frozenset[ToolExecutionMode] | None = None,
        required_tool_names: frozenset[str] | None = None,
        domain_context_overrides: Mapping[str, object] | None = None,
        host_system_instruction: str | None = None,
        response_validators: Sequence[ResponseValidator] = (),
        agent_role: str | None = None,
        output_work_units: int = 1,
        host_context_only: bool = False,
        mapped_request: AgentRunRequest | None = None,
        base_options: AgentCoreRunOptions | None = None,
        run_binding_lifecycle: RunBindingLifecycle | None = None,
        long_task_executor=None,
    ) -> AsyncIterator[AgentRunUpdate]:
        composition = self._composition
        provider_capabilities = composition.provider_capabilities
        request = mapped_request or to_agent_request(body, provider_options)
        if run_binding_lifecycle is not None:
            await run_binding_lifecycle.validate()
        if domain_context_overrides:
            request = replace(
                request,
                domain_context=DomainContext(
                    namespace=request.domain_context.namespace,
                    payload={
                        **thaw_json_mapping(request.domain_context.payload),
                        **dict(domain_context_overrides),
                    },
                ),
            )
        prepare_request = getattr(composition, "prepare_request", None)
        if callable(prepare_request) and not host_context_only:
            request = await prepare_request(request)
        static_context_claims = (
            ()
            if host_context_only
            else (
                base_options.context_claims
                if base_options is not None
                else agent_context_claims(request)
            )
        )
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
        response_judge_policies = (
            composition.create_response_judge_policies(request)
        )
        force_planned_tool_choice = (
            not provider_capabilities.required_tool_choice_is_unsupported(
                capability_key
            )
        )
        options = (
            replace(
                base_options,
                force_planned_tool_choice=force_planned_tool_choice,
                provenance=run_provenance,
                lineage=lineage,
                response_judge_policies=tuple(response_judge_policies),
            )
            if base_options is not None
            else agent_run_options(
                request,
                provider_options,
                force_planned_tool_choice=force_planned_tool_choice,
                provenance=run_provenance,
                lineage=lineage,
                response_judge_policies=response_judge_policies,
                agent_role=agent_role,
                output_work_units=output_work_units,
            )
        )
        options = replace(
            options,
            context_claims=static_context_claims,
            response_validators=(
                *options.response_validators,
                *tuple(response_validators),
            ),
        )

        can_delegate = bool(
            enable_delegation
            and request.tools_enabled
            and request.mode == "agent"
            and hasattr(composition, "delegation_repository")
        )
        planner_agent_role_guidance: dict[str, dict[str, str]] = {}
        delegation_adapter = None
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
            planner_agent_role_guidance = {
                definition.id: {
                    "title": definition.title,
                    "description": definition.delegation_description,
                }
                for definition in role_registry.definitions
            }
            delegation_adapter = ApplicationDelegationAdapter(
                composition=composition,
                body=body,
                api_key=api_key,
                parent_request=request,
                parent_options=options,
                role_registry=role_registry,
            )

        create_core_kwargs = {
            "on_required_tool_choice_unsupported": (
                lambda: provider_capabilities.mark_required_tool_choice_unsupported(
                    capability_key
                )
            ),
        }
        if host_context_only:
            create_core_kwargs["context_provider_override"] = (
                _HostBoundContextProvider()
            )
        if planner_agent_role_guidance:
            create_core_kwargs.update({
                "agent_role_guidance": planner_agent_role_guidance,
                "max_parallel_agents": 3,
                "delegation_repository": composition.delegation_repository,
                "child_core_submitter": delegation_adapter,
                "child_request_factory": delegation_adapter,
            })
        if long_task_executor is not None:
            create_core_kwargs["long_task_executor"] = long_task_executor
        if (
            allowed_tool_modes is not None
            or required_tool_names is not None
        ):
            create_core_kwargs.update({
                "allowed_tool_modes": allowed_tool_modes,
                "required_tool_names": required_tool_names,
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
        handle = await core.submit(request, options=options)
        core_stream = handle.subscribe(after_sequence=0)
        if run_binding_lifecycle is not None:
            await run_binding_lifecycle.on_run_started(handle.run_id)
        terminal = False
        try:
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
            if run_binding_lifecycle is not None and lineage is None:
                await run_binding_lifecycle.on_run_finished(result)
            terminal = True
            yield result
        finally:
            await core_stream.aclose()
            if terminal:
                release_core = getattr(composition, "release_core", None)
                if callable(release_core):
                    release_core(core)


class _HostBoundContextProvider:
    """Use only the trusted prompt assembled by the durable workflow."""

    async def build_context(self, request, budget, signal=None) -> ContextBundle:
        del request, budget, signal
        return ContextBundle(diagnostics={"contextMode": "host_bound"})
