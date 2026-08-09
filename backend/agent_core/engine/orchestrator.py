"""The single high-level orchestration entry for a complete Agent Core run."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import aclosing, suppress
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from agent_core.cancellation import (
    OperationCanceled,
    await_with_cancellation,
    is_canceled as _is_canceled,
)
from agent_core.context_budget import (
    allocate_context_budget,
    estimate_agent_messages_tokens,
    estimate_json_tokens,
    resolve_context_budget_claims,
    resolve_task_context_budget_claims,
)
from agent_core.context_orchestration.contracts import (
    ConversationCompactionResult,
)
from agent_core.context_orchestration.compaction import (
    ContextCompressionCoordinator,
)
from agent_core.context_orchestration.ledger import (
    ContextCompactionBudget,
    ContextCompactionPhase,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeResult,
    ApprovalDecision,
    ApprovalStatus,
    ContextBlock,
    ContextBudget,
    ContextBudgetClaim,
    ContextBundle,
    ExecutionState,
    MessageOrigin,
    MessageRole,
    PlannerLimits,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningKind,
    PlanningTurn,
    ResponseConstraints,
    RunCreateParams,
    RunId,
    RunProvenance,
    RunLineage,
    RuntimeLimits,
    RuntimeOutcome,
    StepExecutor,
    StepStatus,
    StepType,
    TaskContextRequest,
    TaskPlan,
    TaskStep,
    ToolBatchOutcome,
    ToolExecutionLimits,
    ToolRiskLevel,
    TraceRecord,
)
from agent_core.contracts.normalization import optional_text as _optional_text
from agent_core.errors import (
    ContextOverflowError,
    ContractViolationError,
    InvalidPlannerOutputError,
    ModelGatewayError,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.engine.context_phase import (
    assemble_messages as _assemble_messages,
    compile_task_context_request as _compile_task_context_request,
    context_demand_diagnostics as _context_demand_diagnostics,
    host_planning_facts as _host_planning_facts,
    merge_context_claims as _merge_context_claims,
    planned_tool_names as _planned_tool_names,
    planning_tool_guidance as _planning_tool_guidance,
    validate_context_allocations as _validate_context_allocations,
)
from agent_core.engine.durable_execution import (
    bind_event_to_run as _bind_event_to_run,
    complete_admitted_task as _complete_admitted_task,
    validate_task_admission_coverage as _validate_task_admission_coverage,
)
from agent_core.engine.options import AgentCoreRunOptions
from agent_core.engine.planning_validation import (
    effective_registrations as _effective_registrations,
    validate_plan_authority as _validate_plan_authority,
    validate_planning_constraints as _validate_planning_constraints,
    validate_task_constraint_refinement as _validate_task_constraint_refinement,
)
from agent_core.host_planned_tool_gateway import HostPlannedToolGateway
from agent_core.json_values import thaw_json_mapping
from agent_core.output_budget import ResolvedOutputBudget
from agent_core.planner import (
    AgentPlanner,
    build_execution_message,
    effective_planning_tool_names,
)
from agent_core.plan_constraints import (
    agent_assignment_coverage_violations,
)
from agent_core.plan_compiler import (
    compile_task_plan,
    project_completed_steps_for_planning,
    projected_planning_tool_names,
    projected_planning_tool_schemas,
    runtime_tool_names_for_planning_names,
)
from agent_core.ports import (
    ApprovalGateway,
    CancellationSignal,
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    ContextProvider,
    ConversationCompactor,
    DynamicTaskPlanner,
    ExecutionStateFactory,
    PlanningPolicy,
    ResponseJudge,
    ResponseValidator,
    RunRepository,
    StagedContextProvider,
    TaskContextDemandProvider,
    TaskPlanningConstraintProvider,
    TaskPlanner,
    ToolCatalog,
    ToolIdempotencyGateway,
    ToolRegistration,
    ModelGateway,
)
from agent_core.run_controller import AgentRunController
from agent_core.run_state import RunStateMachine
from agent_core.runtime import AgentRuntime
from agent_core.recovery import RecoveryPolicy
from agent_core.tools import (
    CoreToolExecutor,
    InMemoryApprovalGateway,
    InMemoryToolCatalog,
    model_visible_tool_schema,
    resolve_tool_display_name,
)
from agent_core.task_admission import (
    ExecutionMode,
    LongTaskDispatcher,
    LongTaskExecutionStatus,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
    TaskAdmissionEvaluator,
)
from agent_core.timing import duration_ms as _duration_ms


CoreRunUpdate = AgentEvent | AgentRunResult


class AgentCore:
    """Compose planning, context, model/tool runtime and run lifecycle.

    The async iterator is the only public output channel. Applications can map
    its typed events to any host transport.
    Concrete domain tools enter only as registrations in ``tool_catalog``.
    """

    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        run_repository: RunRepository,
        planner: TaskPlanner | None = None,
        planning_policy: PlanningPolicy | None = None,
        context_provider: ContextProvider | None = None,
        conversation_compactor: ConversationCompactor | None = None,
        execution_state_factory: ExecutionStateFactory | None = None,
        tool_catalog: ToolCatalog | None = None,
        agent_role_guidance: Mapping[str, Any] | None = None,
        max_parallel_agents: int = 1,
        task_admission_evaluator: TaskAdmissionEvaluator | None = None,
        long_task_dispatcher: LongTaskDispatcher | None = None,
        approval_gateway: ApprovalGateway | None = None,
        tool_idempotency_gateway: ToolIdempotencyGateway | None = None,
        runtime_limits: RuntimeLimits = RuntimeLimits(),
        recovery_policy: RecoveryPolicy = RecoveryPolicy(),
        tool_execution_limits: ToolExecutionLimits = ToolExecutionLimits(),
    ) -> None:
        self._model_gateway = model_gateway
        self._repository = run_repository
        self._runtime_limits = runtime_limits
        self._recovery_policy = recovery_policy
        self._conversation_compactor = (
            conversation_compactor or ContextCompressionCoordinator()
        )
        self._task_admission_evaluator = task_admission_evaluator
        self._long_task_dispatcher = long_task_dispatcher
        default_planner_limits = PlannerLimits()
        self._planner = planner or AgentPlanner(
            model_gateway,
            PlannerLimits(
                max_tool_steps=min(
                    default_planner_limits.max_tool_steps,
                    max(0, runtime_limits.max_model_rounds - 2),
                ),
                # Initial planning may encounter one structural violation and
                # then one authenticated assignment-coverage violation. Keep
                # both repairs bounded while allowing the composed runtime to
                # return the corrected plan instead of installing stale work.
                max_repair_attempts=min(
                    2,
                    max(1, runtime_limits.max_model_rounds),
                ),
            ),
        )
        self._planning_policy = planning_policy or _DefaultPlanningPolicy()
        self._context_provider = context_provider or _EmptyContextProvider()
        self._execution_state_factory = (
            execution_state_factory or _DefaultExecutionStateFactory()
        )
        self._tool_catalog = tool_catalog or InMemoryToolCatalog(())
        self._registrations = tuple(self._tool_catalog.registrations())
        self._agent_role_guidance = dict(agent_role_guidance or {})
        self._max_parallel_agents = max(1, int(max_parallel_agents))
        self._approval_gateway = approval_gateway or InMemoryApprovalGateway()
        # This is deliberately not replaceable by a domain ``execute`` hook:
        # every registered handler crosses the same Core policy boundary.
        self._tool_executor = CoreToolExecutor(
            _CapturedToolCatalog(self._registrations),
            self._approval_gateway,
            tool_execution_limits,
            tool_idempotency_gateway,
        )

    async def resolve_approval(
        self,
        run_id: RunId,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalStatus | None:
        return await self._approval_gateway.resolve(
            run_id,
            approval_id,
            ApprovalDecision(decision),
        )

    async def cancel_pending_approvals(self, run_id: RunId) -> int:
        return await self._approval_gateway.cancel_pending(run_id)

    async def run(
        self,
        request: AgentRunRequest,
        *,
        options: AgentCoreRunOptions | None = None,
        signal: CancellationSignal | None = None,
    ) -> AsyncIterator[CoreRunUpdate]:
        options = options or AgentCoreRunOptions()
        sink = _BufferedEventSink()
        controller = AgentRunController(
            repository=self._repository,
            event_sink=sink,
        )
        runtime_stream = None
        compaction_source_request = request
        pre_planning_compaction: dict[str, Any] = {
            "outcome": "not_configured",
        }
        try:
            await controller.start(
                RunCreateParams(
                    session_id=request.session_id,
                    prompt=request.latest_user_text(),
                    mode=request.mode,
                    provenance=options.provenance,
                    lineage=options.lineage, binding=options.binding,
                )
            )
            for event in sink.drain():
                yield event

            compaction_trace = request.metadata.get("conversationCompaction")
            if isinstance(compaction_trace, Mapping):
                await controller.record_trace(TraceRecord(
                    stage="conversation_compaction",
                    outcome=str(
                        compaction_trace.get("outcome") or "unknown"
                    ),
                    details={
                        str(key): value
                        for key, value in compaction_trace.items()
                        if key != "outcome"
                    },
                ))

            if _is_canceled(signal):
                await controller.cancel("request_canceled")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return

            # Reserve against every enabled schema. Staged providers use this
            # pass only for a lightweight, host-authenticated planning manifest;
            # legacy providers retain their original single-pass behavior.
            reservation_started = perf_counter()
            try:
                context_claims = await await_with_cancellation(
                    resolve_context_budget_claims(
                        self._context_provider,
                        request,
                        options.context_claims,
                        signal,
                    ),
                    signal,
                )
                registrations, enabled_names = _effective_registrations(
                    self._tool_catalog,
                    self._registrations,
                    request,
                    model_supports_tools=options.model_supports_tools,
                )
                display_locale = str(
                    request.metadata.get("locale") or "zh-CN"
                )
                reserved_schemas = tuple(
                    model_visible_tool_schema(
                        registration.schema,
                        display_locale,
                    )
                    for registration in registrations
                    if registration.schema.name in enabled_names
                )
                reserved_budget = allocate_context_budget(
                    window_tokens=(
                        request.context_window
                        or options.default_context_window_tokens
                    ),
                    output_reserve_tokens=options.output_reserve_tokens,
                    tools=reserved_schemas,
                    claims=context_claims,
                    safety_reserve_tokens=options.safety_reserve_tokens,
                    runtime_reserve_tokens=options.runtime_reserve_tokens,
                    minimum_message_tokens=options.minimum_message_tokens,
                )
                compactor = self._conversation_compactor
                if compactor is not None:
                    compaction_started = asyncio.Event()
                    compaction_started_payload: dict[str, Any] = {}

                    async def notify_compaction_started(
                        payload: Mapping[str, Any],
                    ) -> None:
                        compaction_started_payload.update(dict(payload))
                        compaction_started.set()

                    compaction_task = asyncio.create_task(
                        await_with_cancellation(
                            compactor.prepare(
                                compaction_source_request,
                                signal,
                                on_compaction_started=(
                                    notify_compaction_started
                                ),
                                budget=ContextCompactionBudget(
                                    phase=(
                                        ContextCompactionPhase.PRE_PLANNING
                                    ),
                                    provider_input_tokens=(
                                        reserved_budget.provider_input_tokens
                                    ),
                                    context_tokens=sum(
                                        reserved_budget.context_allocations.values()
                                    ),
                                    context_tokens_are_resolved=False,
                                    output_reserve_tokens=(
                                        reserved_budget.output_reserve_tokens
                                    ),
                                ),
                            ),
                            signal,
                        )
                    )
                    compaction_started_wait = asyncio.create_task(
                        compaction_started.wait()
                    )
                    compaction_result: ConversationCompactionResult | None = None
                    try:
                        await asyncio.wait(
                            (compaction_task, compaction_started_wait),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if compaction_started.is_set():
                            started_event = AgentEvent(
                                type="conversation.compaction.started",
                                run_id=controller.run_id,
                                payload={
                                    "status": "running",
                                    "phase": "pre_planning",
                                    "postPlanning": False,
                                    **compaction_started_payload,
                                },
                            )
                            await self._publish_runtime_event(
                                controller.run_id,
                                started_event,
                            )
                            yield started_event
                        candidate = await compaction_task
                        if not isinstance(
                            candidate,
                            ConversationCompactionResult,
                        ):
                            raise ContractViolationError(
                                "conversation compactor returned an invalid result"
                            )
                        compaction_result = candidate
                    except OperationCanceled:
                        raise
                    except Exception as error:
                        pre_planning_compaction = {
                            "outcome": "failed_open",
                            "phase": "pre_planning",
                        }
                        await _record_safe_exception(
                            controller,
                            stage="conversation_compaction",
                            outcome="failed_open",
                            error=error,
                            safe_details={"phase": "pre_planning"},
                        )
                    finally:
                        if not compaction_started_wait.done():
                            compaction_started_wait.cancel()
                        if not compaction_task.done():
                            compaction_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await compaction_started_wait
                        with suppress(asyncio.CancelledError, Exception):
                            await compaction_task

                    if compaction_result is not None:
                        request = compaction_result.request
                        pre_planning_compaction = {
                            **thaw_json_mapping(
                                compaction_result.diagnostics
                            ),
                            "outcome": compaction_result.outcome,
                            "phase": "pre_planning",
                            "compactedTurnCount": (
                                compaction_result.compacted_turn_count
                            ),
                            "retainedRawTurnCount": (
                                compaction_result.retained_raw_turn_count
                            ),
                            "summaryVersion": (
                                compaction_result.compression_state_version
                            ),
                        }
                        await controller.record_trace(TraceRecord(
                            stage="conversation_compaction",
                            outcome=compaction_result.outcome,
                            details={
                                key: value
                                for key, value in pre_planning_compaction.items()
                                if key != "outcome"
                            },
                        ))
                    if compaction_started.is_set():
                        completed_event = AgentEvent(
                            type="conversation.compaction.completed",
                            run_id=controller.run_id,
                            payload={
                                "status": (
                                    "completed"
                                    if compaction_result is not None
                                    and compaction_result.outcome.startswith(
                                        "compacted"
                                    )
                                    else "failed"
                                ),
                                "phase": "pre_planning",
                                "postPlanning": False,
                                "outcome": (
                                    compaction_result.outcome
                                    if compaction_result is not None
                                    else "failed_open"
                                ),
                                "compactedTurnCount": (
                                    compaction_result.compacted_turn_count
                                    if compaction_result is not None
                                    else 0
                                ),
                                "retainedRawTurnCount": (
                                    compaction_result.retained_raw_turn_count
                                    if compaction_result is not None
                                    else 0
                                ),
                                "summaryVersion": (
                                    compaction_result.compression_state_version
                                    if compaction_result is not None
                                    else None
                                ),
                            },
                        )
                        await self._publish_runtime_event(
                            controller.run_id,
                            completed_event,
                        )
                        yield completed_event
                staged_context_provider = (
                    self._context_provider
                    if isinstance(self._context_provider, StagedContextProvider)
                    else None
                )
                planning_bundle = await await_with_cancellation(
                    (
                        staged_context_provider.build_planning_context(
                            request,
                            reserved_budget,
                            signal,
                        )
                        if staged_context_provider is not None
                        else self._context_provider.build_context(
                            request,
                            reserved_budget,
                            signal,
                        )
                    ),
                    signal,
                )
                if not isinstance(planning_bundle, ContextBundle):
                    raise ContractViolationError(
                        "context provider must return ContextBundle"
                    )
                _validate_context_allocations(planning_bundle, reserved_budget)
                bundle = planning_bundle
            except OperationCanceled:
                await controller.cancel("request_canceled")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except ContextOverflowError as error:
                await _record_safe_exception(
                    controller,
                    stage="context_reservation",
                    outcome="overflow",
                    error=error,
                    started=reservation_started,
                )
                await controller.fail("context_overflow_initial")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except Exception as error:
                await _record_safe_exception(
                    controller,
                    stage="context_reservation",
                    outcome="failed",
                    error=error,
                    started=reservation_started,
                )
                await controller.fail("context_setup_failed")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return

            planning_started = perf_counter()
            planning_fallback_model_only = False
            capabilities: PlanningCapabilities | None = None
            admission: TaskAdmissionDecision | None = None
            try:
                planning_tool_names = projected_planning_tool_names(
                    registrations,
                    enabled_names,
                )
                base_capabilities = PlanningCapabilities(
                    available_tool_names=planning_tool_names,
                    available_agent_roles=frozenset(
                        self._agent_role_guidance
                    ),
                    model_supports_tools=options.model_supports_tools,
                    host_planning_facts=_host_planning_facts(planning_bundle),
                    tool_guidance=_planning_tool_guidance(
                        registrations,
                        enabled_names,
                        display_locale,
                    ),
                    agent_role_guidance=self._agent_role_guidance,
                    max_parallel_agents=self._max_parallel_agents,
                )
                constraints = self._planning_policy.planning_constraints(
                    request,
                    base_capabilities,
                )
                _validate_planning_constraints(
                    base_capabilities,
                    constraints,
                    runtime_tool_names=enabled_names,
                )
                capabilities = replace(
                    base_capabilities,
                    constraints=constraints,
                )
                should_plan = bool(
                    self._planning_policy.should_plan(request, capabilities)
                )
                plan: TaskPlan | None = None
                planning_kind: PlanningKind | None = None
                if should_plan:
                    planning = await await_with_cancellation(
                        self._planner.create_plan(request, capabilities, signal),
                        signal,
                    )
                    if planning.model_call_parameters:
                        for parameters in planning.model_call_parameters:
                            await controller.record_event(
                                CoreEventType.MODEL_CALL_RECORDED,
                                {
                                    "phase": "planning",
                                    "count": 1,
                                    "toolNames": [],
                                    "toolChoice": "none",
                                    "parameters": dict(parameters),
                                },
                            )
                    elif planning.model_call_count > 0:
                        await controller.record_event(
                            CoreEventType.MODEL_CALL_RECORDED,
                            {
                                "phase": "planning",
                                "count": planning.model_call_count,
                                "toolNames": [],
                                "toolChoice": "none",
                            },
                        )
                    if (
                        planning.plan.task_spec is not None
                        and isinstance(
                            self._planning_policy,
                            TaskPlanningConstraintProvider,
                        )
                    ):
                        constraints = (
                            self._planning_policy.planning_constraints_for_task(
                                request,
                                capabilities,
                                planning.plan.task_spec,
                            )
                        )
                        _validate_task_constraint_refinement(
                            capabilities.constraints,
                            constraints,
                        )
                        _validate_planning_constraints(
                            base_capabilities,
                            constraints,
                            runtime_tool_names=enabled_names,
                        )
                        capabilities = replace(
                            capabilities,
                            constraints=constraints,
                        )
                    compiled = compile_task_plan(
                        planning.plan,
                        registrations,
                        constraints=constraints,
                        satisfied_tool_names=(
                            constraints.execution_satisfied_tool_names
                        ),
                        enabled_tool_names=enabled_names,
                    )
                    plan = compiled.plan
                    planning_kind = planning.kind
                    _validate_plan_authority(
                        plan,
                        enabled_names,
                        available_agent_roles=capabilities.available_agent_roles,
                        constraints=constraints,
                        max_tool_steps=max(
                            0,
                            self._runtime_limits.max_model_rounds - 2,
                        ),
                    )
                    if (
                        self._task_admission_evaluator is not None
                        and (
                            plan.task_spec is not None
                            or any(
                                step.executor is StepExecutor.AGENT
                                for step in plan.steps
                            )
                        )
                    ):
                        admission = await await_with_cancellation(
                            self._task_admission_evaluator.evaluate(
                                request,
                                plan,
                                signal,
                            ),
                            signal,
                        )
                        _validate_task_admission_coverage(plan, admission)
                        await controller.record_event(
                            CoreEventType.TASK_ADMISSION_DECIDED,
                            admission.to_event_payload(),
                        )
                    elif any(
                        step.executor is StepExecutor.AGENT
                        for step in plan.steps
                    ):
                        raise ContractViolationError(
                            "Agent plan steps require a task admission evaluator"
                        )
                    await controller.install_plan(plan)
                await controller.record_trace(TraceRecord(
                    stage="planning",
                    outcome=(planning_kind.value if planning_kind else "skipped"),
                    details={
                        "toolCount": len(enabled_names),
                        "planningCapabilityCount": len(
                            capabilities.available_tool_names
                        ),
                        "contextSatisfiedToolCount": len(
                            constraints.context_satisfied_tool_names
                        ),
                        "planningExcludedToolCount": len(
                            constraints.planning_excluded_tool_names
                        ),
                        "satisfiedToolDependencyEdgeCount": len(
                            constraints.satisfied_tool_dependency_edges
                        ),
                        "requiredAnyToolCount": len(
                            constraints.required_any_tool_names
                        ),
                        "agentRoleCount": len(
                            capabilities.available_agent_roles
                        ),
                        "requiredAnyAgentRoleCount": len(
                            constraints.required_any_agent_roles
                        ),
                        "minimumRootAgentCount": (
                            constraints.minimum_root_agent_count
                        ),
                        "planningExcludedExecutorCount": len(
                            constraints.planning_excluded_executors
                        ),
                        "allowModelOnlyFallback": (
                            constraints.allow_model_only_fallback
                        ),
                        "planned": should_plan,
                        "hostInsertedPrerequisiteCount": (
                            len(compiled.inserted_tool_names)
                            if should_plan
                            else 0
                        ),
                        "hostLoweredProtocolToolCount": (
                            len(compiled.lowered_tool_names)
                            if should_plan
                            else 0
                        ),
                    },
                    duration_ms=_duration_ms(planning_started),
                ))
            except OperationCanceled:
                await controller.record_trace(TraceRecord(
                    stage="planning",
                    outcome="canceled",
                    duration_ms=_duration_ms(planning_started),
                ))
                await controller.cancel("request_canceled")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except InvalidPlannerOutputError as error:
                await _record_safe_exception(
                    controller,
                    stage="planning",
                    outcome="invalid",
                    error=error,
                    started=planning_started,
                    safe_details={
                        "reasonCode": error.code,
                        "validationReason": str(error)[:240],
                    },
                )
                if (
                    capabilities is not None
                    and not capabilities.constraints.allow_model_only_fallback
                ):
                    await controller.record_trace(TraceRecord(
                        stage="planning",
                        outcome="fallback_denied",
                        details={
                            "reasonCode": error.code,
                            "hostPolicy": "deny_model_only_fallback",
                        },
                        duration_ms=_duration_ms(planning_started),
                    ))
                    await controller.fail("planning_invalid")
                    for event in sink.drain():
                        yield event
                    yield _run_result(controller)
                    return
                # Planner JSON is untrusted model output. After its bounded
                # repair is exhausted, preserve a useful conversation by
                # installing a host-authored, model-only plan. This keeps every
                # tool and side effect disabled while allowing the runtime to
                # explain the limitation or answer from already trusted context.
                plan = _safe_model_only_plan(
                    title="安全降级回复",
                    goal="在不调用工具的情况下回应用户",
                    step_id="respond-after-invalid-plan",
                )
                await controller.install_plan(plan)
                planning_fallback_model_only = True
                await controller.record_trace(TraceRecord(
                    stage="planning",
                    outcome="fallback_model_only",
                    details={
                        "reasonCode": error.code,
                        "fallbackToolCount": 0,
                    },
                    duration_ms=_duration_ms(planning_started),
                ))
            except ContractViolationError as error:
                await _record_safe_exception(
                    controller,
                    stage="planning",
                    outcome="contract_violation",
                    error=error,
                    started=planning_started,
                )
                await controller.fail("planning_contract_violation")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except Exception as error:
                await _record_safe_exception(
                    controller,
                    stage="planning",
                    outcome="failed",
                    error=error,
                    started=planning_started,
                )
                await controller.fail("planning_failed")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return

            if (
                admission is not None
                and admission.mode is not ExecutionMode.INLINE
                and plan is not None
            ):
                async for admitted_event in _complete_admitted_task(
                    controller=controller,
                    request=request,
                    plan=plan,
                    admission=admission,
                    dispatcher=self._long_task_dispatcher,
                    sink=sink,
                    signal=signal,
                ):
                    yield admitted_event
                yield _run_result(controller)
                return

            for event in sink.drain():
                yield event

            planning_hook: _DynamicPlanningOrchestrator | None = None
            if (
                should_plan
                and plan is not None
                and not planning_fallback_model_only
                and isinstance(self._planner, DynamicTaskPlanner)
            ):
                planning_hook = _DynamicPlanningOrchestrator(
                    planner=self._planner,
                    request=request,
                    capabilities=capabilities,
                    controller=controller,
                    enabled_names=enabled_names,
                    registrations=registrations,
                )
            selected_names = (
                runtime_tool_names_for_planning_names(
                    registrations,
                    enabled_names,
                    effective_planning_tool_names(capabilities),
                )
                if planning_hook is not None
                else _planned_tool_names(plan)
                if plan is not None
                else enabled_names
            )
            selected_registrations = tuple(
                registration
                for registration in registrations
                if registration.schema.name in selected_names
            )
            schemas = tuple(
                model_visible_tool_schema(
                    registration.schema,
                    display_locale,
                )
                for registration in selected_registrations
            )

            setup_started = perf_counter()
            try:
                task_context: TaskContextRequest | None = None
                task_context_claims: tuple[ContextBudgetClaim, ...] = ()
                if (
                    staged_context_provider is not None
                    and plan is not None
                    and plan.task_spec is not None
                ):
                    task_context = _compile_task_context_request(
                        plan,
                        selected_registrations,
                        run_id=controller.run_id,
                    )
                    if isinstance(
                        staged_context_provider,
                        TaskContextDemandProvider,
                    ):
                        task_context_claims = await await_with_cancellation(
                            resolve_task_context_budget_claims(
                                staged_context_provider,
                                request,
                                task_context,
                                signal,
                            ),
                            signal,
                        )
                effective_context_claims = _merge_context_claims(
                    context_claims,
                    task_context_claims,
                )
                budget = allocate_context_budget(
                    window_tokens=(
                        request.context_window
                        or options.default_context_window_tokens
                    ),
                    output_reserve_tokens=options.output_reserve_tokens,
                    tools=schemas,
                    claims=effective_context_claims,
                    safety_reserve_tokens=options.safety_reserve_tokens,
                    runtime_reserve_tokens=options.runtime_reserve_tokens,
                    minimum_message_tokens=options.minimum_message_tokens,
                )
                context_mode = "legacy_reserved"
                if staged_context_provider is not None:
                    retrieval_started = perf_counter()
                    if task_context is not None:
                        bundle = await await_with_cancellation(
                            staged_context_provider.build_task_context(
                                request,
                                budget,
                                task_context,
                                signal,
                            ),
                            signal,
                        )
                        context_mode = "task_spec"
                    else:
                        # Missing TaskSpec is a compatibility condition, not
                        # permission to silently omit previously available
                        # context. Rebuild through the legacy full path.
                        bundle = await await_with_cancellation(
                            self._context_provider.build_context(
                                request,
                                budget,
                                signal,
                            ),
                            signal,
                        )
                        context_mode = "legacy_fallback"
                    if not isinstance(bundle, ContextBundle):
                        raise ContractViolationError(
                            "context provider must return ContextBundle"
                        )
                    await controller.record_trace(TraceRecord(
                        stage="context_retrieval",
                        outcome=context_mode,
                        details={
                            "postPlanning": context_mode == "task_spec",
                            "plannedToolCount": len(
                                _planned_tool_names(plan)
                                if plan is not None
                                else ()
                            ),
                            "requiredContextBlockCount": (
                                len(task_context.required_context_blocks)
                                if context_mode == "task_spec"
                                else 0
                            ),
                            "evidenceKindCount": (
                                len(task_context.evidence_kinds)
                                if context_mode == "task_spec"
                                else 0
                            ),
                        },
                        duration_ms=_duration_ms(retrieval_started),
                    ))
                _validate_context_allocations(bundle, budget)
                post_planning_diagnostics: dict[str, Any] = {
                    "outcome": "not_configured",
                    "plannedStepCount": len(plan.steps) if plan is not None else 0,
                    "plannedToolCount": (
                        sum(
                            step.executor is StepExecutor.TOOL
                            for step in plan.steps
                        )
                        if plan is not None
                        else 0
                    ),
                    "selectedToolCount": len(selected_names),
                }
                compactor = self._conversation_compactor
                if compactor is not None:
                    resolved_context_tokens = estimate_agent_messages_tokens(
                        _assemble_messages((), bundle.blocks, plan)
                    )
                    optimization_started = asyncio.Event()
                    optimization_started_payload: dict[str, Any] = {}

                    async def notify_optimization_started(
                        payload: Mapping[str, Any],
                    ) -> None:
                        optimization_started_payload.update(dict(payload))
                        optimization_started.set()

                    async def run_context_optimization(
                    ) -> ConversationCompactionResult:
                        source_request = replace(
                            compaction_source_request,
                            metadata={
                                **compaction_source_request.metadata,
                                **request.metadata,
                            },
                        )
                        compacted = await await_with_cancellation(
                            compactor.prepare(
                                source_request,
                                signal,
                                budget=ContextCompactionBudget(
                                    phase=ContextCompactionPhase.POST_PLANNING,
                                    provider_input_tokens=(
                                        budget.provider_input_tokens
                                    ),
                                    context_tokens=resolved_context_tokens,
                                    context_tokens_are_resolved=True,
                                    output_reserve_tokens=(
                                        budget.output_reserve_tokens
                                    ),
                                    planned_step_count=(
                                        post_planning_diagnostics[
                                            "plannedStepCount"
                                        ]
                                    ),
                                    planned_tool_count=(
                                        post_planning_diagnostics[
                                            "plannedToolCount"
                                        ]
                                    ),
                                    selected_tool_count=len(selected_names),
                                ),
                                on_compaction_started=(
                                    notify_optimization_started
                                ),
                            ),
                            signal,
                        )
                        if not isinstance(
                            compacted,
                            ConversationCompactionResult,
                        ):
                            raise ContractViolationError(
                                "conversation compactor returned an invalid "
                                "post-planning result"
                            )
                        return replace(
                            compacted,
                            request=replace(
                                compacted.request,
                                metadata={
                                    **request.metadata,
                                    **compacted.request.metadata,
                                },
                            ),
                        )

                    optimization_task = asyncio.create_task(
                        run_context_optimization()
                    )
                    optimization_started_wait = asyncio.create_task(
                        optimization_started.wait()
                    )
                    optimization_result: (
                        ConversationCompactionResult | None
                    ) = None
                    optimization_failed = False
                    try:
                        await asyncio.wait(
                            (optimization_task, optimization_started_wait),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if optimization_started.is_set():
                            started_event = AgentEvent(
                                type="conversation.compaction.started",
                                run_id=controller.run_id,
                                payload={
                                    "status": "running",
                                    "phase": "post_planning",
                                    "postPlanning": True,
                                    **optimization_started_payload,
                                },
                            )
                            await self._publish_runtime_event(
                                controller.run_id,
                                started_event,
                            )
                            yield started_event
                        candidate = await optimization_task
                        if not isinstance(
                            candidate,
                            ConversationCompactionResult,
                        ):
                            raise ContractViolationError(
                                "conversation compactor returned an invalid "
                                "post-planning result"
                            )
                        optimization_result = candidate
                    except OperationCanceled:
                        raise
                    except Exception as error:
                        optimization_failed = True
                        await _record_safe_exception(
                            controller,
                            stage="post_planning_context_optimization",
                            outcome="failed_open",
                            error=error,
                        )
                    finally:
                        if not optimization_started_wait.done():
                            optimization_started_wait.cancel()
                        if not optimization_task.done():
                            optimization_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await optimization_started_wait
                        with suppress(asyncio.CancelledError, Exception):
                            await optimization_task

                    if optimization_result is not None:
                        request = optimization_result.request
                        post_planning_diagnostics = {
                            **post_planning_diagnostics,
                            **thaw_json_mapping(
                                optimization_result.diagnostics
                            ),
                            "outcome": optimization_result.outcome,
                            "postPlanning": True,
                            "resolvedContextTokens": resolved_context_tokens,
                            "compactedTurnCount": (
                                optimization_result.compacted_turn_count
                            ),
                            "retainedRawTurnCount": (
                                optimization_result.retained_raw_turn_count
                            ),
                            "summaryVersion": (
                                optimization_result.compression_state_version
                            ),
                        }
                        await controller.record_trace(TraceRecord(
                            stage="post_planning_context_optimization",
                            outcome=optimization_result.outcome,
                            details=post_planning_diagnostics,
                        ))
                    elif optimization_failed:
                        post_planning_diagnostics = {
                            **post_planning_diagnostics,
                            "outcome": "failed_open",
                            "postPlanning": True,
                            "resolvedContextTokens": resolved_context_tokens,
                        }

                    if optimization_started.is_set():
                        completed_event = AgentEvent(
                            type="conversation.compaction.completed",
                            run_id=controller.run_id,
                            payload={
                                "status": (
                                    "completed"
                                    if optimization_result is not None
                                    and optimization_result.outcome.startswith(
                                        "compacted"
                                    )
                                    else "failed"
                                ),
                                "phase": "post_planning",
                                "postPlanning": True,
                                "outcome": (
                                    optimization_result.outcome
                                    if optimization_result is not None
                                    else "failed_open"
                                ),
                                "compactedTurnCount": (
                                    optimization_result.compacted_turn_count
                                    if optimization_result is not None
                                    else 0
                                ),
                                "retainedRawTurnCount": (
                                    optimization_result.retained_raw_turn_count
                                    if optimization_result is not None
                                    else 0
                                ),
                                "summaryVersion": (
                                    optimization_result.compression_state_version
                                    if optimization_result is not None
                                    else None
                                ),
                            },
                        )
                        await self._publish_runtime_event(
                            controller.run_id,
                            completed_event,
                        )
                        yield completed_event
                prepared_request = replace(
                    request,
                    messages=_assemble_messages(request.messages, bundle.blocks, plan),
                    context_window=budget.window_tokens,
                )
                estimated_input_tokens = estimate_agent_messages_tokens(
                    prepared_request.messages
                )
                dropped_message_count = 0
                overflow_tokens = max(
                    0,
                    estimated_input_tokens - budget.provider_input_tokens,
                )
                if overflow_tokens:
                    raise ContextOverflowError(
                        "required messages exceed the initial provider input budget",
                        reason_code="required_messages_exceed_provider_budget",
                        details={
                            "providerInputTokens": budget.provider_input_tokens,
                            "estimatedInputTokens": estimated_input_tokens,
                            "overflowTokens": overflow_tokens,
                        },
                    )
                state = self._execution_state_factory.create(request)
                if not isinstance(state, ExecutionState):
                    raise ContractViolationError(
                        "execution state factory must return ExecutionState"
                    )
                state.run_id = controller.run_id
                await controller.record_trace(TraceRecord(
                    stage="context_budget",
                    outcome="within_budget",
                    details={
                        "windowTokens": budget.window_tokens,
                        "providerInputTokens": budget.provider_input_tokens,
                        "toolSchemaTokens": budget.tool_schema_tokens,
                        "reservedToolSchemaTokens": (
                            reserved_budget.tool_schema_tokens
                        ),
                        "contextMode": context_mode,
                        "contextDemands": _context_demand_diagnostics(
                            effective_context_claims,
                            budget,
                        ),
                        "droppedMessages": dropped_message_count,
                        "prePlanningCompaction": pre_planning_compaction,
                        "postPlanningOptimization": post_planning_diagnostics,
                    },
                    duration_ms=_duration_ms(setup_started),
                ))
                budget_event = AgentEvent(
                    type=CoreEventType.CONTEXT_BUDGETED,
                    run_id=controller.run_id,
                    payload={
                        "windowTokens": budget.window_tokens,
                        "providerInputTokens": budget.provider_input_tokens,
                        "estimatedInputTokens": estimated_input_tokens,
                        "outputReserveTokens": budget.output_reserve_tokens,
                        "outputBudget": (
                            options.output_budget.to_mapping()
                            if options.output_budget is not None
                            else None
                        ),
                        "runtimeReserveTokens": budget.runtime_reserve_tokens,
                        "safetyReserveTokens": budget.safety_reserve_tokens,
                        "toolSchemaTokens": budget.tool_schema_tokens,
                        "reservedToolSchemaTokens": (
                            reserved_budget.tool_schema_tokens
                        ),
                        "contextMode": context_mode,
                        "droppedMessages": dropped_message_count,
                        "projectedTotalTokens": (
                            estimated_input_tokens
                            + budget.tool_schema_tokens
                            + budget.output_reserve_tokens
                            + budget.runtime_reserve_tokens
                            + budget.safety_reserve_tokens
                        ),
                        "overflowTokens": 0,
                        "contextAllocations": thaw_json_mapping(
                            budget.context_allocations
                        ),
                        "diagnostics": {
                            **thaw_json_mapping(bundle.diagnostics),
                            "contextDemands": _context_demand_diagnostics(
                                effective_context_claims,
                                budget,
                            ),
                            "prePlanningCompaction": (
                                pre_planning_compaction
                            ),
                            "postPlanningOptimization": (
                                post_planning_diagnostics
                            ),
                        },
                    },
                )
                await self._publish_runtime_event(controller.run_id, budget_event)
                yield budget_event
            except OperationCanceled:
                await controller.cancel("request_canceled")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except ContextOverflowError as error:
                await _record_safe_exception(
                    controller,
                    stage="context_budget",
                    outcome="overflow",
                    error=error,
                    started=setup_started,
                )
                await controller.fail("context_overflow_initial")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return
            except Exception as error:
                await _record_safe_exception(
                    controller,
                    stage="context_budget",
                    outcome="failed",
                    error=error,
                    started=setup_started,
                )
                await controller.fail("context_setup_failed")
                for event in sink.drain():
                    yield event
                yield _run_result(controller)
                return

            host_planned_arguments = {
                registration.schema.name: thaw_json_mapping(
                    registration.host_planned_arguments
                )
                for registration in registrations
                if registration.host_planned_arguments is not None
            }
            runtime_model_gateway: ModelGateway = self._model_gateway
            if host_planned_arguments:
                runtime_model_gateway = HostPlannedToolGateway(
                    self._model_gateway,
                    host_planned_arguments,
                )
            runtime = AgentRuntime(
                model_gateway=runtime_model_gateway,
                tool_execution_gateway=self._tool_executor,
                observer=controller,
                context_compressor=self._conversation_compactor,
                limits=self._runtime_limits,
                recovery_policy=self._recovery_policy,
            )
            runtime_result: AgentRuntimeResult | None = None
            try:
                runtime_stream = runtime.run(
                    prepared_request,
                    tools=schemas,
                    response_constraints=options.response_constraints,
                    response_validators=options.response_validators,
                    response_judges=options.response_judges,
                    execution_state=state,
                    run_id=controller.run_id,
                    context_budget=budget,
                    output_budget=options.output_budget,
                    scope_tools_to_observer=(plan is not None),
                    force_tool_choice=bool(
                        plan is not None
                        and selected_names
                        and options.force_planned_tool_choice
                    ),
                    require_tool_call=bool(plan is not None and selected_names),
                    tools_executable=True,
                    planning_hook=planning_hook,
                    tool_context_contracts={
                        registration.schema.name: registration.context_contract
                        for registration in registrations
                    },
                    stage_context_projection_enabled=bool(
                        plan is not None and plan.task_spec is not None
                    ),
                    signal=signal,
                )
                async with aclosing(runtime_stream) as updates:
                    async for update in updates:
                        for event in sink.drain():
                            yield event
                        if isinstance(update, AgentEvent):
                            event = _bind_event_to_run(update, controller.run_id)
                            await self._publish_runtime_event(
                                controller.run_id,
                                event,
                            )
                            yield event
                        else:
                            runtime_result = update
                runtime_stream = None
            except OperationCanceled:
                runtime_result = AgentRuntimeResult(
                    run_id=controller.run_id,
                    outcome=RuntimeOutcome.CANCELED,
                    final_response="",
                    model=request.model.model,
                    round_count=0,
                    error_code="request_canceled",
                )
            except Exception as error:
                await _record_safe_exception(
                    controller,
                    stage="runtime",
                    outcome="exception",
                    error=error,
                )
                runtime_result = AgentRuntimeResult(
                    run_id=controller.run_id,
                    outcome=RuntimeOutcome.FAILED,
                    final_response="",
                    model=request.model.model,
                    round_count=0,
                    error_code="runtime_exception",
                )

            for event in sink.drain():
                yield event
            if runtime_result is None:
                await controller.fail("runtime_returned_no_result")
            elif runtime_result.outcome is RuntimeOutcome.COMPLETED:
                await controller.complete(runtime_result.final_response)
            elif runtime_result.outcome is RuntimeOutcome.CANCELED:
                await controller.cancel(
                    runtime_result.error_code or "request_canceled"
                )
            else:
                await controller.fail(
                    runtime_result.error_code or "runtime_failed"
                )
            for event in sink.drain():
                yield event
            yield _run_result(
                controller,
                model=(runtime_result.model if runtime_result is not None else request.model.model),
            )
        finally:
            if runtime_stream is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await runtime_stream.aclose()
            run_id = controller.run_id
            if run_id is not None:
                with suppress(Exception):
                    await self._approval_gateway.cancel_pending(run_id)
            snapshot = controller.snapshot
            if snapshot is not None and not snapshot.terminal:
                # Closing the public iterator is itself a disconnect signal.
                # Ordinary commit errors are intentionally not swallowed:
                # aclose()/the consuming task must observe persistence failure.
                await controller.cancel("consumer_disconnected")

    async def _publish_runtime_event(
        self,
        run_id: RunId | None,
        event: AgentEvent,
    ) -> None:
        if run_id is None:
            raise ContractViolationError("runtime event requires a run id")
        if event.type in CONTROLLER_OWNED_RUN_EVENT_TYPES:
            raise ContractViolationError(
                "runtime, tool, approval and domain events cannot use "
                f"controller-owned event type {event.type!r}"
            )
        # Token deltas remain transport-only. Lifecycle, context, tool,
        # approval and domain effects are replayable repository events.
        if event.type not in {
            CoreEventType.MODEL_DELTA,
            CoreEventType.MODEL_THINKING_DELTA,
        }:
            await self._repository.append_event(run_id, event)


class _BufferedEventSink:
    def __init__(self) -> None:
        self._events: deque[AgentEvent] = deque()

    async def emit(self, event: AgentEvent) -> None:
        self._events.append(event)

    def drain(self) -> tuple[AgentEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events


class _DefaultPlanningPolicy:
    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        del request, capabilities
        return PlanningConstraints()

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        return bool(request.tools_enabled and capabilities.available_tool_names)


class _DynamicPlanningOrchestrator:
    """Bridge runtime evidence to a dynamic planner and durable Run authority."""

    def __init__(
        self,
        *,
        planner: DynamicTaskPlanner,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
        controller: AgentRunController,
        enabled_names: frozenset[str],
        registrations: Sequence[ToolRegistration],
    ) -> None:
        self._planner = planner
        self._request = request
        self._capabilities = capabilities
        self._controller = controller
        self._enabled_names = enabled_names
        self._registrations = tuple(registrations)
        self._revision = 0

    async def replan_after_tool(
        self,
        messages: Sequence[AgentMessage],
        *,
        round_number: int,
        remaining_model_rounds: int,
        outcome: ToolBatchOutcome,
        signal: CancellationSignal | None = None,
    ) -> AgentMessage:
        started = perf_counter()
        self._revision += 1
        snapshot = self._controller.snapshot
        if snapshot is None:
            raise ContractViolationError("dynamic planning requires a live run")
        if outcome is ToolBatchOutcome.FAILED:
            await self._controller.on_tool_round_failed()
            snapshot = self._controller.snapshot
            if snapshot is None:  # pragma: no cover - controller invariant
                raise ContractViolationError("dynamic planning lost its live run")
        runtime_completed_steps = tuple(
            step
            for step in snapshot.steps
            if step.status in {
                StepStatus.DONE,
                StepStatus.BLOCKED,
                StepStatus.FAILED,
            }
        )
        completed_steps = project_completed_steps_for_planning(
            runtime_completed_steps,
            self._registrations,
            self._enabled_names,
        )
        try:
            planning = await await_with_cancellation(
                self._planner.revise_plan(
                    self._request,
                    self._capabilities,
                    PlanningTurn(
                        revision=self._revision,
                        round_number=round_number,
                        remaining_model_rounds=remaining_model_rounds,
                        messages=tuple(messages),
                        completed_steps=completed_steps,
                        last_tool_outcome=outcome,
                    ),
                    signal,
                ),
                signal,
            )
        except (InvalidPlannerOutputError, ModelGatewayError) as error:
            # Replanning is advisory: after a successful tool round, the
            # controller still owns a previously compiled and validated plan.
            # A malformed or unavailable model revision must not destroy that
            # trusted state. After a failed tool round, continuing future tool
            # steps could be unsafe, so recovery becomes a host-authored
            # model-only response instead of advancing past missing evidence.
            reason_code = getattr(error, "code", "replanning_failed")
            validation_reason = (
                str(error)[:240]
                if isinstance(error, InvalidPlannerOutputError)
                else None
            )
            if outcome is ToolBatchOutcome.FAILED:
                recovery_plan = _safe_model_only_plan(
                    title=snapshot.title,
                    goal=snapshot.goal,
                    step_id=f"respond-after-tool-failure-{self._revision}",
                )
                _validate_plan_authority(
                    recovery_plan,
                    self._enabled_names,
                    available_agent_roles=(
                        self._capabilities.available_agent_roles
                    ),
                    constraints=self._capabilities.constraints,
                    max_tool_steps=0,
                )
                revised = await self._controller.revise_plan(recovery_plan)
                remaining_plan = TaskPlan(
                    title=revised.title,
                    goal=revised.goal,
                    steps=tuple(
                        step
                        for step in revised.steps
                        if step.status in {
                            StepStatus.PENDING,
                            StepStatus.RUNNING,
                        }
                    ),
                )
                await self._controller.record_trace(TraceRecord(
                    stage="planning",
                    outcome="fallback_safe_response",
                    details={
                        "dynamic": True,
                        "revision": self._revision,
                        "round": round_number,
                        "errorType": type(error).__name__,
                        "reasonCode": reason_code,
                        "validationReason": validation_reason,
                        "fallbackToolCount": 0,
                    },
                    duration_ms=_duration_ms(started),
                ))
                return build_execution_message(remaining_plan)
            remaining_steps = tuple(
                step
                for step in snapshot.steps
                if step.status in {StepStatus.PENDING, StepStatus.RUNNING}
            )
            if not remaining_steps:
                remaining_steps = (TaskStep(
                    id="respond-after-replan-fallback",
                    title="Respond from completed work",
                    type=StepType.REVIEW,
                    executor=StepExecutor.MODEL,
                    risk_level=ToolRiskLevel.READ,
                ),)
            fallback_plan = TaskPlan(
                title=snapshot.title,
                goal=snapshot.goal,
                steps=remaining_steps,
            )
            fallback_tool_count = sum(
                step.executor is StepExecutor.TOOL
                for step in fallback_plan.steps
            )
            # This plan was compiled and authorized before execution began.
            # Re-check its authority, but do not reinterpret a shrinking
            # runtime round allowance as a capability-contract violation. If
            # the trusted plan eventually exhausts the runtime allowance, the
            # runtime reports max_model_rounds instead of misclassifying an
            # advisory Planner failure as dynamic_planning_failed.
            _validate_plan_authority(
                fallback_plan,
                self._enabled_names,
                available_agent_roles=(
                    self._capabilities.available_agent_roles
                ),
                constraints=self._capabilities.constraints,
                max_tool_steps=fallback_tool_count,
            )
            await self._controller.record_trace(TraceRecord(
                stage="planning",
                outcome="fallback_previous_plan",
                details={
                    "dynamic": True,
                    "revision": self._revision,
                    "round": round_number,
                    "errorType": type(error).__name__,
                    "reasonCode": reason_code,
                    "validationReason": validation_reason,
                    "remainingStepCount": len(remaining_steps),
                    "trustedPlanToolCount": fallback_tool_count,
                },
                duration_ms=_duration_ms(started),
            ))
            return build_execution_message(fallback_plan)
        if planning.model_call_parameters:
            for parameters in planning.model_call_parameters:
                await self._controller.record_event(
                    CoreEventType.MODEL_CALL_RECORDED,
                    {
                        "phase": "replanning",
                        "count": 1,
                        "toolNames": [],
                        "toolChoice": "none",
                        "round": round_number,
                        "revision": self._revision,
                        "parameters": dict(parameters),
                    },
                )
        elif planning.model_call_count > 0:
            await self._controller.record_event(
                CoreEventType.MODEL_CALL_RECORDED,
                {
                    "phase": "replanning",
                    "count": planning.model_call_count,
                    "toolNames": [],
                    "toolChoice": "none",
                    "round": round_number,
                    "revision": self._revision,
                },
            )
        satisfied_tool_names = frozenset(
            name
            for step in runtime_completed_steps
            if step.status is StepStatus.DONE
            for name in step.suggested_tools
        ) | self._capabilities.constraints.execution_satisfied_tool_names
        compiled = compile_task_plan(
            planning.plan,
            self._registrations,
            constraints=self._capabilities.constraints,
            satisfied_tool_names=satisfied_tool_names,
            enabled_tool_names=self._enabled_names,
        )
        prospective = RunStateMachine.revise_plan(snapshot, compiled.plan)
        remaining_plan = TaskPlan(
            title=prospective.title,
            goal=prospective.goal,
            task_spec=compiled.plan.task_spec,
            steps=tuple(
                step
                for step in prospective.steps
                if step.status in {StepStatus.PENDING, StepStatus.RUNNING}
            ),
        )
        _validate_plan_authority(
            remaining_plan,
            self._enabled_names,
            available_agent_roles=self._capabilities.available_agent_roles,
            constraints=self._capabilities.constraints,
            max_tool_steps=max(0, remaining_model_rounds - 1),
        )
        revised = await self._controller.revise_plan(compiled.plan)
        await self._controller.record_trace(TraceRecord(
            stage="planning",
            outcome="replanned",
            details={
                "dynamic": True,
                "revision": self._revision,
                "round": round_number,
                "planningKind": planning.kind.value,
                "completedStepCount": len(completed_steps),
                "remainingStepCount": sum(
                    step.status in {StepStatus.PENDING, StepStatus.RUNNING}
                    for step in revised.steps
                ),
                "hostInsertedPrerequisiteCount": len(
                    compiled.inserted_tool_names
                ),
            },
            duration_ms=_duration_ms(started),
        ))
        return build_execution_message(remaining_plan)


def _safe_model_only_plan(
    *,
    title: str,
    goal: str | None,
    step_id: str,
) -> TaskPlan:
    """Return the only fail-open plan Core may author without model trust."""

    return TaskPlan(
        title=title,
        goal=goal,
        steps=(TaskStep(
            id=step_id,
            title="说明当前结果",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            risk_level=ToolRiskLevel.READ,
        ),),
    )


class _EmptyContextProvider:
    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del request, budget, signal
        return ContextBundle()


class _DefaultExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        del request
        return ExecutionState()


class _CapturedToolCatalog:
    """Hold the exact registration snapshot shared by Engine and Executor."""

    def __init__(self, registrations: Sequence[ToolRegistration]) -> None:
        self._registrations = tuple(registrations)
        self._names = frozenset(
            registration.schema.name for registration in self._registrations
        )

    def registrations(self) -> tuple[ToolRegistration, ...]:
        return self._registrations

    def enabled_names(self, request: AgentRunRequest) -> frozenset[str]:
        del request
        return self._names


async def _record_safe_exception(
    controller: AgentRunController,
    *,
    stage: str,
    outcome: str,
    error: Exception,
    started: float | None = None,
    safe_details: Mapping[str, Any] | None = None,
) -> None:
    overflow_details = (
        {
            "reasonCode": error.reason_code,
            **error.details,
        }
        if isinstance(error, ContextOverflowError)
        else {}
    )
    await controller.record_trace(TraceRecord(
        stage=stage,
        outcome=outcome,
        details={
            "errorType": type(error).__name__,
            **overflow_details,
            **(safe_details or {}),
        },
        duration_ms=(_duration_ms(started) if started is not None else None),
    ))


def _run_result(
    controller: AgentRunController,
    *,
    model: str | None = None,
) -> AgentRunResult:
    snapshot = controller.snapshot
    if snapshot is None or not snapshot.terminal:
        raise RuntimeError("agent run has no terminal snapshot")
    return AgentRunResult(
        run_id=snapshot.run_id,
        status=snapshot.status,
        final_response=snapshot.final_response,
        error=snapshot.error,
        model=model,
    )
