"""Application-owned lifecycle for composed parent and child Agent Runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

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
    RunStatus,
    ToolExecutionMode,
)
from purra.events import AgentEvent
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping
from purra.output import (
    AgentOutputEvent,
    OutputEventKind,
    ResponseTransactionMode,
)
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
from application.host_child_runs import (
    HOST_CHILD_BINDING_PROTOCOL,
    HostChildReservation,
    HostChildReservationDisposition,
    HostChildTerminalRetryPolicy,
)
from infrastructure.persistence.run_execution_store import now_ms
from infrastructure.models.capabilities import normalize_thinking_enabled
AgentRunUpdate = AgentOutputEvent | AgentRunResult


class AgentRunService:
    """Drive composed runs and multiplex live parent delegation events."""

    def __init__(self, composition: AgentComposition) -> None:
        self._composition = composition

    async def read_validated_result(self, run_id: str) -> str:
        """Read the private canonical result without replaying execution."""

        return await self._composition.output_repository.load_validated_result(
            run_id
        )

    async def run_host_child(
        self,
        *,
        body: AgentRunInput,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal | None,
        lineage: RunLineage,
        mapped_request: AgentRunRequest,
        base_options: AgentCoreRunOptions,
        allowed_tool_modes: frozenset[ToolExecutionMode] | None = None,
        required_tool_names: frozenset[str] | None = None,
        host_child_key: str | None = None,
        terminal_retry_policy: HostChildTerminalRetryPolicy = (
            HostChildTerminalRetryPolicy.REUSE_DONE_ONLY
        ),
    ) -> AgentRunResult:
        """Run one host-owned child without creating a delegation claim.

        The caller supplies the immutable parent/root lineage and the product
        request contract.  This service remains the sole owner of Core
        submission, cancellation and terminal settlement.
        """

        if lineage.delegation_id is not None:
            raise ValueError(
                "host-orchestrated child lineage cannot claim a delegation"
            )
        stable_key = str(host_child_key or "").strip()
        if stable_key:
            return await self._run_durable_host_child(
                body=body,
                api_key=api_key,
                provider_options=provider_options,
                signal=signal,
                lineage=lineage,
                mapped_request=mapped_request,
                base_options=base_options,
                allowed_tool_modes=allowed_tool_modes,
                required_tool_names=required_tool_names,
                host_child_key=stable_key,
                terminal_retry_policy=HostChildTerminalRetryPolicy(
                    terminal_retry_policy
                ),
            )
        return await self._run_new_host_child(
            body=body,
            api_key=api_key,
            provider_options=provider_options,
            signal=signal,
            lineage=lineage,
            mapped_request=mapped_request,
            base_options=base_options,
            allowed_tool_modes=allowed_tool_modes,
            required_tool_names=required_tool_names,
        )

    async def _run_new_host_child(
        self,
        *,
        body: AgentRunInput,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal | None,
        lineage: RunLineage,
        mapped_request: AgentRunRequest,
        base_options: AgentCoreRunOptions,
        allowed_tool_modes: frozenset[ToolExecutionMode] | None,
        required_tool_names: frozenset[str] | None,
        run_binding_lifecycle: RunBindingLifecycle | None = None,
    ) -> AgentRunResult:
        child_signal = _HostChildCancellationSignal(signal)

        async def _consume() -> AgentRunResult:
            terminal: AgentRunResult | None = None
            async for update in self.run(
                body=body,
                api_key=api_key,
                provider_options=provider_options,
                signal=child_signal,
                provenance=base_options.provenance,
                lineage=lineage,
                enable_delegation=False,
                allowed_tool_modes=allowed_tool_modes,
                required_tool_names=required_tool_names,
                mapped_request=mapped_request,
                base_options=base_options,
                host_context_only=True,
                cancellation_reason="host_child_canceled",
                run_binding_lifecycle=run_binding_lifecycle,
            ):
                if isinstance(update, AgentRunResult):
                    terminal = update
            if terminal is None:
                raise RuntimeError("host child completed without a terminal result")
            return terminal

        consume_task = asyncio.create_task(_consume())
        try:
            return await asyncio.shield(consume_task)
        except asyncio.CancelledError:
            child_signal.cancel()
            try:
                await asyncio.shield(consume_task)
            except BaseException:
                pass
            raise

    async def _run_durable_host_child(
        self,
        *,
        body: AgentRunInput,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal | None,
        lineage: RunLineage,
        mapped_request: AgentRunRequest,
        base_options: AgentCoreRunOptions,
        allowed_tool_modes: frozenset[ToolExecutionMode] | None,
        required_tool_names: frozenset[str] | None,
        host_child_key: str,
        terminal_retry_policy: HostChildTerminalRetryPolicy,
    ) -> AgentRunResult:
        bound_options = self._composition.bind_run_profile(
            mapped_request,
            replace(base_options, lineage=lineage),
        )
        binding = bound_options.binding
        if binding is None:
            raise ValueError("durable host child requires a Run binding")
        attributes = thaw_json_mapping(binding.attributes)
        profile = str(attributes.get("agentProfile") or "").strip()
        domain = str(attributes.get("domainNamespace") or "").strip()
        contract = {
            "sessionId": mapped_request.session_id,
            "turnId": bound_options.turn_id,
            "bindingNamespace": binding.namespace,
            "bindingAggregateId": binding.aggregate_id,
            "bindingCommandId": binding.command_id,
            "parentRunId": lineage.parent_run_id,
            "rootRunId": lineage.root_run_id,
            "delegationId": lineage.delegation_id,
            "agentRole": lineage.agent_role,
            "depth": lineage.depth,
            "agentProfile": profile,
            "domainNamespace": domain,
            "responseMode": (
                bound_options.resolved_response_transaction_policy.mode.value
            ),
        }
        identity_digest = _host_child_identity_digest(
            mapped_request,
            bound_options,
            allowed_tool_modes=allowed_tool_modes,
            required_tool_names=required_tool_names,
        )
        owner_token = f"host-child-{uuid4().hex}"
        registry = self._composition.host_child_run_registry
        reservation = await registry.reserve(
            host_child_key=host_child_key,
            identity_digest=identity_digest,
            contract=contract,
            owner_token=owner_token,
            timestamp_ms=now_ms(),
        )
        while True:
            if reservation.disposition is HostChildReservationDisposition.CREATE:
                host_child_binding = {
                    "protocol": HOST_CHILD_BINDING_PROTOCOL,
                    "identityDigest": identity_digest,
                    "attemptKey": reservation.attempt_key,
                    "generation": reservation.generation,
                    "responseMode": contract["responseMode"],
                }
                attempt_options = replace(
                    bound_options,
                    binding=replace(
                        binding,
                        attributes={
                            **attributes,
                            "hostChild": host_child_binding,
                        },
                    ),
                )
                lifecycle = _HostChildRunBindingLifecycle(
                    registry,
                    reservation,
                    owner_token,
                )
                return await self._run_new_host_child(
                    body=body,
                    api_key=api_key,
                    provider_options=provider_options,
                    signal=signal,
                    lineage=lineage,
                    mapped_request=mapped_request,
                    base_options=attempt_options,
                    allowed_tool_modes=allowed_tool_modes,
                    required_tool_names=required_tool_names,
                    run_binding_lifecycle=lifecycle,
                )
            terminal = reservation.terminal_status
            if terminal is None:
                await _wait_for_existing_host_child(signal)
                reservation = await registry.refresh(reservation)
                continue
            if terminal == RunStatus.DONE.value:
                return await self._persisted_host_child_result(
                    reservation,
                    bound_options,
                )
            if (
                terminal == RunStatus.FAILED.value
                and terminal_retry_policy
                is HostChildTerminalRetryPolicy.REUSE_DONE_RETRY_FAILED
            ):
                reservation = await registry.advance_failed(
                    reservation,
                    expected_run_id=str(reservation.run_id or ""),
                    owner_token=owner_token,
                    timestamp_ms=now_ms(),
                )
                continue
            raise ContractViolationError(
                f"host child terminal status {terminal!r} is not reusable"
            )

    async def _persisted_host_child_result(
        self,
        reservation: HostChildReservation,
        options: AgentCoreRunOptions,
    ) -> AgentRunResult:
        result = await self._composition.host_child_run_registry.load_result(
            reservation
        )
        if (
            options.resolved_response_transaction_policy.mode
            is ResponseTransactionMode.VALIDATED_RESULT
        ):
            result = replace(
                result,
                validated_result=await self.read_validated_result(result.run_id),
            )
        return result

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
        cancellation_reason: str | None = None,
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
        options = composition.bind_run_profile(request, options)

        role_registry = composition.agent_role_registry_for_request(request)
        can_delegate = bool(
            enable_delegation
            and request.tools_enabled
            and request.mode == "agent"
            and role_registry is not None
            and hasattr(composition, "delegation_repository")
        )
        planner_agent_role_guidance: dict[str, dict[str, str]] = {}
        delegation_adapter = None
        if can_delegate:
            assert role_registry is not None
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


class _HostChildCancellationSignal:
    """Combine caller cancellation with the product cancellation signal."""

    def __init__(self, upstream: CancellationSignal | None) -> None:
        self._upstream = upstream
        self._local = asyncio.Event()

    def is_set(self) -> bool:
        return self._local.is_set() or (
            self._upstream is not None and self._upstream.is_set()
        )

    def cancel(self) -> None:
        self._local.set()

    async def wait(self) -> bool:
        if self.is_set():
            return True
        if self._upstream is None:
            return await self._local.wait()
        local = asyncio.create_task(self._local.wait())
        upstream = asyncio.create_task(self._upstream.wait())
        waiters = (local, upstream)
        try:
            done, _pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            return any(bool(task.result()) for task in done)
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)


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


class _HostBoundContextProvider:
    """Use only the trusted prompt assembled by the durable workflow."""

    async def build_context(self, request, budget, signal=None) -> ContextBundle:
        del request, budget, signal
        return ContextBundle(diagnostics={"contextMode": "host_bound"})


class _HostChildRunBindingLifecycle:
    def __init__(
        self,
        registry,
        reservation: HostChildReservation,
        owner_token: str,
    ) -> None:
        self._registry = registry
        self._reservation = reservation
        self._owner_token = owner_token

    async def validate(self) -> None:
        return None

    async def before_submit(self) -> None:
        return None

    async def on_run_started(self, run_id: str) -> None:
        self._reservation = await self._registry.bind_run(
            self._reservation,
            run_id=run_id,
            owner_token=self._owner_token,
        )

    async def on_run_finished(self, _result) -> None:
        self._reservation = await self._registry.refresh(self._reservation)

    async def on_start_failed(self, _code: str) -> None:
        # The durable reservation remains the recovery authority. It has no
        # Run to terminalize yet and is safely reclaimed after its lease.
        return None


async def _wait_for_existing_host_child(signal) -> None:
    if signal is not None and signal.is_set():
        raise asyncio.CancelledError
    if signal is None:
        await asyncio.sleep(0.05)
        return
    timer = asyncio.create_task(asyncio.sleep(0.05))
    canceled = asyncio.create_task(signal.wait())
    try:
        done, _pending = await asyncio.wait(
            (timer, canceled),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if canceled in done and bool(canceled.result()):
            raise asyncio.CancelledError
    finally:
        for task in (timer, canceled):
            if not task.done():
                task.cancel()
        await asyncio.gather(timer, canceled, return_exceptions=True)


def _host_child_identity_digest(
    request: AgentRunRequest,
    options: AgentCoreRunOptions,
    *,
    allowed_tool_modes: frozenset[ToolExecutionMode] | None,
    required_tool_names: frozenset[str] | None,
) -> str:
    provenance = options.provenance
    binding = options.binding
    binding_attributes = (
        thaw_json_mapping(binding.attributes) if binding is not None else {}
    )
    binding_attributes.pop("hostChild", None)
    output_limit = options.output_limit
    lineage = options.lineage
    payload = {
        "request": {
            "messages": [message.to_mapping() for message in request.messages],
            "model": {
                "provider": request.model.provider,
                "model": request.model.model,
                "options": thaw_json_mapping(request.model.options),
                "capability": request.model.capability_snapshot.to_mapping(
                    include_digest=True
                ),
            },
            "domain": {
                "namespace": request.domain_context.namespace,
                "payload": thaw_json_mapping(request.domain_context.payload),
            },
            "sessionId": request.session_id,
            "mode": request.mode,
            "contextWindow": request.context_window,
            "toolsEnabled": request.tools_enabled,
            "metadata": thaw_json_mapping(request.metadata),
        },
        "options": {
            "turnId": options.turn_id,
            "lineage": (
                {
                    "parentRunId": lineage.parent_run_id,
                    "rootRunId": lineage.root_run_id,
                    "delegationId": lineage.delegation_id,
                    "agentRole": lineage.agent_role,
                    "depth": lineage.depth,
                }
                if lineage is not None
                else None
            ),
            "outputLimit": (
                {
                    "maxTokens": output_limit.max_tokens,
                    "source": output_limit.source.value,
                    "profileMaxTokens": output_limit.profile_max_tokens,
                }
                if output_limit is not None
                else None
            ),
            "reasoningMode": options.reasoning_mode.value,
            "binding": (
                {
                    "namespace": binding.namespace,
                    "aggregateId": binding.aggregate_id,
                    "commandId": binding.command_id,
                    "attributes": binding_attributes,
                }
                if binding is not None
                else None
            ),
            "provenance": (
                {
                    "provider": provenance.model_provider,
                    "model": provenance.model_name,
                    "contextWindow": provenance.context_window,
                    "endpointDigest": provenance.endpoint_digest,
                    "requestProfileDigest": provenance.request_profile_digest,
                    "capability": thaw_json_mapping(
                        provenance.capability_snapshot
                    ),
                }
                if provenance is not None
                else None
            ),
            "response": {
                "mode": options.resolved_response_transaction_policy.mode.value,
                "presentation": (
                    options.resolved_response_transaction_policy.public_presentation.value
                ),
                "validatorTypes": [
                    f"{type(item).__module__}.{type(item).__qualname__}"
                    for item in options.response_validators
                ],
            },
            "tools": {
                "allowedModes": sorted(
                    mode.value for mode in (allowed_tool_modes or ())
                ),
                "requiredNames": sorted(required_tool_names or ()),
            },
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
