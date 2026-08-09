from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

from agent_core.context_orchestration.contracts import (
    ConversationCompactionResult,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    ApprovalDecision,
    ApprovalStatus,
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    DomainContext,
    DomainEffect,
    ExecutionState,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    PlanningCapabilities,
    PlanningKind,
    PlanningConstraints,
    PlanningResult,
    PostPlanningContextOptimizationResult,
    ResponseValidationResult,
    RunCreateParams,
    RunStatus,
    RuntimeLimits,
    StepExecutor,
    StepStatus,
    StepType,
    TaskSpec,
    TaskPlan,
    TaskStep,
    TaskStepUpdate,
    ToolCallDelta,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
    TraceRecord,
)
from agent_core.engine import (
    AgentCore,
    AgentCoreRunOptions,
    _validate_planning_constraints,
    _validate_task_constraint_refinement,
)
from agent_core.errors import ContractViolationError, InvalidPlannerOutputError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import RunBeginResult, RunCommit, ToolRegistration
from agent_core.tools import InMemoryToolCatalog
from agent_core.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    LongTaskExecutionResult,
    LongTaskExecutionStatus,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
)


PLAN = {
    "needsTodos": True,
    "title": "Update a resource",
    "goal": "Read, propose, approve, and report",
    "todos": [
        {
            "id": "read",
            "title": "Read resource",
            "type": "read",
            "executor": "tool",
            "expectedTools": ["read_resource"],
            "riskLevel": "read",
        },
        {
            "id": "propose",
            "title": "Propose change",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["propose_change"],
            "riskLevel": "write",
        },
        {
            "id": "apply",
            "title": "Apply approved change",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["apply_change"],
            "riskLevel": "destructive",
        },
        {
            "id": "respond",
            "title": "Report result",
            "type": "review",
            "executor": "model",
            "expectedTools": [],
            "riskLevel": "read",
        },
    ],
}


class MemoryRunRepository:
    """A domain-free repository implementing the atomic Core port."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.events: list[AgentEvent] = []
        self.traces: list[TraceRecord] = []
        self._counter = 0
        self.terminal_committed = asyncio.Event()

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult:
        self._counter += 1
        run_id = f"run-{self._counter}"
        event = AgentEvent(
            type=started_event.type,
            run_id=run_id,
            payload=started_event.payload,
        )
        self.runs[run_id] = {
            "params": params,
            "status": RunStatus.RUNNING,
            "steps": [],
            "final_response": "",
            "error": None,
        }
        self.events.append(event)
        return RunBeginResult(run_id=run_id, event=event)

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        current = self.runs[run_id]
        next_steps = list(current["steps"])
        if commit.replace_steps is not None:
            next_steps = list(commit.replace_steps)
        for update in commit.step_updates:
            next_steps = [
                replace(
                    step,
                    status=update.status,
                    result_summary=(
                        update.result_summary
                        if update.result_summary is not None
                        else step.result_summary
                    ),
                    error=(update.error if update.error is not None else step.error),
                )
                if step.id == update.step_id
                else step
                for step in next_steps
            ]
        next_status = commit.terminal_status or current["status"]
        next_run = {
            **current,
            "steps": next_steps,
            "status": next_status,
            "final_response": (
                commit.final_response
                if commit.final_response is not None
                else current["final_response"]
            ),
            "error": commit.error,
        }
        # Commit state and outbox together from the controller's perspective.
        self.runs[run_id] = next_run
        self.events.extend(commit.events)
        if commit.terminal_status is not None:
            self.terminal_committed.set()
        return commit.events

    async def append_event(self, run_id: str, event: AgentEvent) -> None:
        assert event.run_id == run_id
        self.events.append(event)

    async def append_trace(self, run_id: str, trace: TraceRecord) -> None:
        assert run_id in self.runs
        self.traces.append(trace)

    async def bind_conversation(self, run_id: str, conversation_id: int) -> None:
        self.runs[run_id]["conversation_id"] = conversation_id


class DelayedBeginRepository(MemoryRunRepository):
    """Expose the instant after the atomic begin commit but before return."""

    def __init__(self):
        super().__init__()
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(self, params, started_event):
        result = await super().begin(params, started_event)
        self.committed.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # Cancellation-linearizable port: the durable begin already won,
            # so cancellation returns its receipt instead of claiming that no
            # write occurred.
            return result
        return result


class BlockedBeforeBeginRepository(MemoryRunRepository):
    """Block before the atomic begin write to test prompt cancellation."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(self, params, started_event):
        self.entered.set()
        await self.release.wait()
        return await super().begin(params, started_event)


class FailingTerminalRepository(MemoryRunRepository):
    async def commit(self, run_id, commit):
        if commit.terminal_status is not None:
            raise RuntimeError("terminal commit failed")
        return await super().commit(run_id, commit)


class AlwaysPlan:
    def planning_constraints(self, request, capabilities):
        del request, capabilities
        return PlanningConstraints()

    def should_plan(self, request, capabilities):
        assert request.tools_enabled
        assert capabilities.available_tool_names == {
            "read_resource",
            "propose_change",
            "apply_change",
        }
        return True


class StaticPlanner:
    def __init__(self, plan: TaskPlan):
        self.plan = plan

    async def create_plan(self, request, capabilities, signal=None):
        return PlanningResult(kind=PlanningKind.PLANNED, plan=self.plan)


class CapturePlanner(StaticPlanner):
    def __init__(self, plan: TaskPlan):
        super().__init__(plan)
        self.capabilities = None
        self.call_count = 0

    async def create_plan(self, request, capabilities, signal=None):
        self.capabilities = capabilities
        self.call_count += 1
        return await super().create_plan(request, capabilities, signal)


class StopAfterObservationPlanner(StaticPlanner):
    """Fixture proving an initial roadmap does not lock future execution."""

    def __init__(self, plan: TaskPlan):
        super().__init__(plan)
        self.turns = []

    async def revise_plan(
        self,
        request,
        capabilities,
        turn,
        signal=None,
    ):
        del request, capabilities, signal
        self.turns.append(turn)
        return PlanningResult(
            kind=PlanningKind.DIRECT_RESPONSE,
            plan=TaskPlan(
                title="Evidence is sufficient",
                steps=(TaskStep(
                    id="respond-now",
                    title="Respond from observed evidence",
                    type=StepType.REVIEW,
                    executor=StepExecutor.MODEL,
                    risk_level=ToolRiskLevel.READ,
                ),),
            ),
            reason="The first tool result satisfied the goal.",
        )


class InvalidAfterObservationPlanner(StaticPlanner):
    """Fixture for a provider that repeatedly violates replanning schema."""

    def __init__(self, plan: TaskPlan):
        super().__init__(plan)
        self.turns = []

    async def revise_plan(
        self,
        request,
        capabilities,
        turn,
        signal=None,
    ):
        del request, capabilities, signal
        self.turns.append(turn)
        raise InvalidPlannerOutputError(
            "planner output is missing needsTodos",
            code="invalid_plan",
        )


class FixtureContextProvider:
    async def build_context(self, request, budget, signal=None):
        assert budget.allocation_for("fixture") == 1_000
        return ContextBundle(
            blocks=(ContextBlock(
                name="fixture",
                content="The current resource is host-owned test data.",
                untrusted=True,
            ),),
            diagnostics={"source": "fixture"},
        )


class PlanningFactContextProvider(FixtureContextProvider):
    def __init__(self):
        self.build_count = 0

    async def build_context(self, request, budget, signal=None):
        bundle = await super().build_context(request, budget, signal)
        self.build_count += 1
        return ContextBundle(
            blocks=bundle.blocks,
            diagnostics={
                "hostPlanningFacts": {
                    "currentChapter": {
                        "bound": True,
                        "singleChapterToolsMayOmitChapterId": True,
                    },
                    "planningRules": ["host fact rule"],
                },
            },
        )


class CapturePlanningPolicy:
    def __init__(self, provider):
        self.provider = provider
        self.capabilities = None

    def planning_constraints(self, request, capabilities):
        del request
        assert self.provider.build_count == 1
        return capabilities.constraints

    def should_plan(self, request, capabilities):
        assert self.provider.build_count == 1
        self.capabilities = capabilities
        return True


class ConstraintPlanningPolicy:
    def __init__(
        self,
        *context_satisfied_tool_names: str,
        planning_excluded_tool_names: tuple[str, ...] = (),
    ):
        self.constraints = PlanningConstraints(
            context_satisfied_tool_names=frozenset(
                context_satisfied_tool_names
            ),
            planning_excluded_tool_names=frozenset(
                planning_excluded_tool_names
            ),
        )
        self.base_capabilities = None
        self.capabilities = None

    def planning_constraints(self, request, capabilities):
        del request
        self.base_capabilities = capabilities
        return self.constraints

    def should_plan(self, request, capabilities):
        del request
        self.capabilities = capabilities
        return True


class SharedStateFactory:
    def __init__(self):
        self.state = ExecutionState(domain={
            "value": "before",
            "proposal": None,
            "handler_order": [],
            "value_seen_after_propose": None,
        })

    def create(self, request):
        return self.state


class ScriptedModelGateway:
    def __init__(self, state: ExecutionState, *, invalid_plan: bool = False):
        self.state = state
        self.invalid_plan = invalid_plan
        self.completions = []
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        self.completions.append((tuple(messages), invocation))
        content = (
            "not valid JSON"
            if self.invalid_plan
            else json.dumps(getattr(self, "plan", PLAN))
        )
        return ModelCompletion(
            message=AgentMessage(role=MessageRole.ASSISTANT, content=content),
            model="planner-model",
        )

    async def stream(self, messages, invocation, signal=None):
        self.invocations.append(invocation)
        visible = tuple(schema.name for schema in invocation.tools)
        if visible == ("read_resource",):
            return ModelStream(
                chunks=_tool_chunks("call-read", "read_resource", "{}"),
                model="runtime-model",
            )
        if visible == ("propose_change",):
            return ModelStream(
                chunks=_tool_chunks(
                    "call-propose",
                    "propose_change",
                    '{"value":"after"}',
                ),
                model="runtime-model",
            )
        if visible == ("apply_change",):
            return ModelStream(
                chunks=_tool_chunks("call-apply", "apply_change", "{}"),
                model="runtime-model",
            )
        if visible == ():
            final = (
                "Applied the approved change."
                if self.state.domain["value"] == "after"
                else "The proposed change was not applied."
            )
            return ModelStream(chunks=_final_chunks(final), model="runtime-model")
        raise AssertionError(f"unexpected visible tools: {visible}")


async def _tool_chunks(call_id: str, name: str, arguments: str):
    yield ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id=call_id,
            type="function",
            name=name,
            arguments_fragment=arguments,
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )


async def _final_chunks(content: str):
    yield ModelStreamChunk(
        content_delta=content,
        finish_reason=ModelFinishReason.STOP,
    )


def _registration(
    name,
    handler,
    *,
    mode,
    risk,
    host_planned_arguments=None,
):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=f"Generic test capability {name}",
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            },
        ),
        handler=handler,
        policy=ToolPolicy(
            mode=mode,
            title=f"Use {name}",
            risk_level=risk,
        ),
        host_planned_arguments=host_planned_arguments,
    )


def _core_fixture(
    *,
    invalid_plan: bool = False,
    repository: MemoryRunRepository | None = None,
    proposed_effect_type: str = "test.change_proposed",
    planner=None,
    runtime_limits: RuntimeLimits = RuntimeLimits(),
    read_error_code: str | None = None,
    task_admission_evaluator=None,
    long_task_dispatcher=None,
    context_provider=None,
    planning_policy=None,
    agent_role_guidance=None,
    replan_after_tools: frozenset[str] = frozenset(),
    host_direct_apply: bool = False,
):
    state_factory = SharedStateFactory()

    async def read_resource(state, arguments, signal=None):
        state.domain["handler_order"].append("read")
        return ToolHandlerResult(
            json.dumps({"value": state.domain["value"]}),
            error_code=read_error_code,
            planning_disposition=(
                ToolPlanningDisposition.REPLAN
                if "read_resource" in replan_after_tools
                else ToolPlanningDisposition.KEEP_PLAN
            ),
        )

    async def propose_change(state, arguments, signal=None):
        state.domain["handler_order"].append("propose")
        state.domain["proposal"] = arguments["value"]
        state.domain["value_seen_after_propose"] = state.domain["value"]
        return ToolHandlerResult(
            json.dumps({"proposed": arguments["value"]}),
            effects=(DomainEffect(
                type=proposed_effect_type,
                payload={"value": arguments["value"]},
            ),),
            planning_disposition=(
                ToolPlanningDisposition.REPLAN
                if "propose_change" in replan_after_tools
                else ToolPlanningDisposition.KEEP_PLAN
            ),
        )

    async def apply_change(state, arguments, signal=None):
        state.domain["handler_order"].append("apply")
        state.domain["value"] = state.domain["proposal"]
        return ToolHandlerResult(
            json.dumps({"value": state.domain["value"]}),
            planning_disposition=(
                ToolPlanningDisposition.REPLAN
                if "apply_change" in replan_after_tools
                else ToolPlanningDisposition.KEEP_PLAN
            ),
        )

    catalog = InMemoryToolCatalog((
        _registration(
            "read_resource",
            read_resource,
            mode=ToolExecutionMode.READ,
            risk=ToolRiskLevel.READ,
        ),
        _registration(
            "propose_change",
            propose_change,
            mode=ToolExecutionMode.PROPOSE,
            risk=ToolRiskLevel.WRITE,
        ),
        _registration(
            "apply_change",
            apply_change,
            mode=ToolExecutionMode.CONFIRM,
            risk=ToolRiskLevel.DESTRUCTIVE,
            host_planned_arguments={} if host_direct_apply else None,
        ),
    ))
    repository = repository or MemoryRunRepository()
    model = ScriptedModelGateway(
        state_factory.state,
        invalid_plan=invalid_plan,
    )
    core = AgentCore(
        model_gateway=model,
        run_repository=repository,
        planner=planner,
        planning_policy=planning_policy or AlwaysPlan(),
        context_provider=context_provider or FixtureContextProvider(),
        execution_state_factory=state_factory,
        tool_catalog=catalog,
        runtime_limits=runtime_limits,
        task_admission_evaluator=task_admission_evaluator,
        long_task_dispatcher=long_task_dispatcher,
        agent_role_guidance=agent_role_guidance or {},
        max_parallel_agents=3,
    )
    request = AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content="Update the generic resource safely.",
        ),),
        model=ModelRequest(provider="fixture", model="scripted"),
        domain_context=DomainContext(
            namespace="test.fixture",
            payload={"resource": "alpha"},
        ),
        session_id="fixture-session",
        mode="agent",
        context_window=32_000,
        tools_enabled=True,
    )
    options = AgentCoreRunOptions(
        context_claims=(ContextBudgetClaim("fixture", 1_000),),
        output_reserve_tokens=1_024,
    )
    return core, request, options, repository, model, state_factory.state


@pytest.mark.asyncio
async def test_durable_task_admission_dispatches_before_runtime_execution():
    plan = TaskPlan(
        title="Generate a large deliverable",
        task_spec=TaskSpec(
            goal="Generate all remaining units",
            operation="write",
            target={"scope": "all_remaining"},
        ),
        steps=(TaskStep(
            id="write",
            title="Write units",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.WRITE,
            suggested_tools=("propose_change",),
        ),),
    )

    class _Admission:
        async def evaluate(self, request, planned, signal=None):
            assert request.messages[-1].content
            assert planned.task_spec == plan.task_spec
            return TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="multiple_model_calls_required",
                estimated_units=100,
                estimated_model_calls=20,
                covered_step_ids=("write",),
            )

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(
            self,
            request,
            planned,
            decision,
            *,
            parent_run_id,
            signal=None,
        ):
            self.calls.append((request, planned, decision, parent_run_id))
            return LongTaskDispatchReceipt(
                task_id="task-1",
                message="Long task created.",
                metadata={"totalUnits": 100},
            )

        async def execute(
            self,
            task_id,
            *,
            parent_run_id,
            observer,
            signal=None,
        ):
            del parent_run_id, observer, signal
            return LongTaskExecutionResult(
                task_id=task_id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response="Long task finished.",
            )

    dispatcher = _Dispatcher()
    planner = CapturePlanner(plan)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
        task_admission_evaluator=_Admission(),
        long_task_dispatcher=dispatcher,
    )

    updates = [update async for update in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert result.final_response == "Long task finished."
    assert planner.call_count == 1
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][3] == result.run_id
    assert state.domain["handler_order"] == []
    assert model.invocations == []
    assert [event.type for event in repository.events] == [
        CoreEventType.RUN_STARTED,
        CoreEventType.TASK_ADMISSION_DECIDED,
        CoreEventType.RUN_TODOS_UPDATED,
        CoreEventType.LONG_TASK_DISPATCHED,
        CoreEventType.RUN_TODO_UPDATED,
        CoreEventType.RUN_COMPLETED,
    ]
    planning_trace = next(
        trace for trace in repository.traces
        if trace.stage == "planning"
    )
    assert planning_trace.outcome == "planned"
    persisted_steps = repository.runs[result.run_id]["steps"]
    assert persisted_steps[0].status is StepStatus.DONE
    assert persisted_steps[0].result_summary == (
        "Durable execution fulfilled this admitted step."
    )


@pytest.mark.asyncio
async def test_durable_progress_rebinds_persisted_steps_to_current_plan_ids():
    plan = TaskPlan(
        title="Resume equivalent durable task",
        task_spec=TaskSpec(
            goal="Resume persisted work",
            operation="write",
        ),
        steps=(TaskStep(
            id="current-write",
            title="Write units",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            suggested_tools=("propose_change",),
        ),),
    )

    class _Admission:
        async def evaluate(self, request, planned, signal=None):
            del request, planned, signal
            return TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="resume_durable_task",
                estimated_units=1,
                estimated_model_calls=0,
                covered_step_ids=("current-write",),
            )

    class _Dispatcher:
        async def dispatch(
            self,
            request,
            planned,
            decision,
            *,
            parent_run_id,
            signal=None,
        ):
            del request, planned, decision, parent_run_id, signal
            return LongTaskDispatchReceipt(
                task_id="task-resumed",
                message="Recovered persisted task.",
                metadata={
                    "durableStepAliases": {
                        "persisted-write": "current-write",
                    },
                },
            )

        async def execute(
            self,
            task_id,
            *,
            parent_run_id,
            observer,
            signal=None,
        ):
            del parent_run_id, signal
            await observer(LongTaskExecutionUpdate(event=AgentEvent(
                type=CoreEventType.LONG_TASK_PROGRESS,
                payload={
                    "taskId": task_id,
                    "units": [{
                        "plannerStepId": "persisted-write",
                        "status": "completed",
                    }],
                },
            )))
            return LongTaskExecutionResult(
                task_id=task_id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response="Recovered result.",
            )

    core, request, options, repository, *_ = _core_fixture(
        planner=CapturePlanner(plan),
        task_admission_evaluator=_Admission(),
        long_task_dispatcher=_Dispatcher(),
    )

    updates = [update async for update in core.run(request, options=options)]

    assert isinstance(updates[-1], AgentRunResult)
    assert updates[-1].status is RunStatus.DONE
    progress = next(
        event
        for event in repository.events
        if event.type == CoreEventType.LONG_TASK_PROGRESS
    )
    assert progress.payload["units"][0]["plannerStepId"] == "current-write"


@pytest.mark.asyncio
async def test_agent_plan_without_task_spec_still_enters_task_admission():
    plan = TaskPlan(
        title="Delegate bounded work",
        steps=(TaskStep(
            id="writer",
            title="Write selected scenes",
            type=StepType.WRITE,
            executor=StepExecutor.AGENT,
            agent_role="fixture_writer",
            assignment={"itemIds": ["item-1"]},
        ),),
    )

    class _Admission:
        def __init__(self):
            self.calls = 0

        async def evaluate(self, request, planned, signal=None):
            del request, signal
            self.calls += 1
            assert planned.task_spec is None
            return TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="agent_executor_requires_durable_scheduler",
                estimated_units=1,
                estimated_model_calls=1,
                covered_step_ids=("writer",),
            )

    class _Dispatcher:
        async def dispatch(
            self,
            request,
            planned,
            decision,
            *,
            parent_run_id,
            signal=None,
        ):
            del request, planned, decision, parent_run_id, signal
            return LongTaskDispatchReceipt(
                task_id="task-agent",
                message="Agent task created.",
            )

        async def execute(
            self,
            task_id,
            *,
            parent_run_id,
            observer,
            signal=None,
        ):
            del parent_run_id, observer, signal
            return LongTaskExecutionResult(
                task_id=task_id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response="Agent work complete.",
            )

    admission = _Admission()
    core, request, options, *_ = _core_fixture(
        planner=CapturePlanner(plan),
        task_admission_evaluator=admission,
        long_task_dispatcher=_Dispatcher(),
        agent_role_guidance={
            "fixture_writer": {"description": "Writes bounded fixture items."},
        },
    )

    updates = [update async for update in core.run(request, options=options)]

    assert admission.calls == 1
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert result.final_response == "Agent work complete."


@pytest.mark.asyncio
async def test_durable_admission_rejects_uncovered_planner_steps():
    plan = TaskPlan(
        title="Read then generate",
        task_spec=TaskSpec(
            goal="Generate all remaining units",
            operation="write",
            target={"scope": "all_remaining"},
        ),
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                risk_level=ToolRiskLevel.READ,
                suggested_tools=("read_resource",),
            ),
            TaskStep(
                id="write",
                title="Write units",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                risk_level=ToolRiskLevel.WRITE,
                suggested_tools=("propose_change",),
            ),
        ),
    )

    class _Admission:
        async def evaluate(self, request, planned, signal=None):
            del request, planned, signal
            return TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="multiple_model_calls_required",
                estimated_units=10,
                estimated_model_calls=2,
                covered_step_ids=("write",),
            )

    core, request, options, repository, *_ = _core_fixture(
        planner=CapturePlanner(plan),
        task_admission_evaluator=_Admission(),
    )

    updates = [update async for update in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert not any(
        event.type == CoreEventType.LONG_TASK_DISPATCHED
        for event in repository.events
    )
    trace = next(
        item for item in repository.traces
        if item.stage == "planning" and item.outcome == "contract_violation"
    )
    assert trace.details["errorType"] == "ContractViolationError"


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False], ids=["approved", "rejected"])
async def test_standalone_core_runs_read_propose_confirm_and_terminal_flow(approve):
    core, request, options, repository, model, state = _core_fixture()
    updates = []
    approval_id = None

    async for update in core.run(request, options=options):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            approval_id = str(update.payload["approvalId"])
            assert state.domain["handler_order"] == ["read", "propose"]
            assert state.domain["value"] == "before"
            assert await core.resolve_approval(
                "wrong-run",
                approval_id,
                ApprovalDecision.APPROVE,
            ) is None
            decision = (
                ApprovalDecision.APPROVE
                if approve
                else ApprovalDecision.REJECT
            )
            expected = (
                ApprovalStatus.APPROVED
                if approve
                else ApprovalStatus.REJECTED
            )
            assert await core.resolve_approval(
                update.run_id,
                approval_id,
                decision,
            ) is expected
            assert await core.resolve_approval(
                update.run_id,
                approval_id,
                decision,
            ) is None

    assert approval_id is not None
    assert isinstance(updates[-1], AgentRunResult)
    assert sum(isinstance(item, AgentRunResult) for item in updates) == 1
    result = updates[-1]
    assert result.status is RunStatus.DONE
    assert result.final_response == (
        "Applied the approved change."
        if approve
        else (
            "You rejected the approval. The operation was not executed, and "
            "the related data remains unchanged."
        )
    )
    assert [tuple(schema.name for schema in call.tools) for call in model.invocations] == [
        ("read_resource",),
        ("propose_change",),
        ("apply_change",),
        (),
    ]
    assert all(call.max_output_tokens == 1_024 for call in model.invocations)
    assert state.domain["value_seen_after_propose"] == "before"
    assert state.domain["handler_order"] == (
        ["read", "propose", "apply"] if approve else ["read", "propose"]
    )
    assert state.domain["value"] == ("after" if approve else "before")
    run = repository.runs[result.run_id]
    assert run["status"] is RunStatus.DONE
    steps = {step.id: step for step in run["steps"]}
    assert steps["read"].status is StepStatus.DONE
    assert steps["propose"].status is StepStatus.DONE
    assert steps["respond"].status is StepStatus.DONE
    assert steps["apply"].status is (
        StepStatus.DONE if approve else StepStatus.BLOCKED
    )
    assert steps["apply"].error == (None if approve else "approval_rejected")
    if not approve:
        assert steps["apply"].result_summary == (
            "User declined approval; the planned tool was not executed."
        )
        declined_updates = [
            item
            for item in updates
            if isinstance(item, AgentEvent)
            and item.type == CoreEventType.RUN_TODO_UPDATED
            and item.payload["step"]["id"] == "apply"
            and item.payload["step"]["status"] == "blocked"
        ]
        assert len(declined_updates) == 1
        assert declined_updates[0].payload["step"]["error"] == (
            "approval_rejected"
        )
        assert "Planned tool step completed." not in str(
            declined_updates[0].payload
        )
    assert CoreEventType.RUN_STARTED in [item.type for item in updates[:-1]]
    assert CoreEventType.RUN_TODOS_UPDATED in [item.type for item in updates[:-1]]
    assert CoreEventType.CONTEXT_BUDGETED in [item.type for item in updates[:-1]]
    assert updates[-2].type == CoreEventType.RUN_COMPLETED
    assert await core.cancel_pending_approvals(result.run_id) == 0


@pytest.mark.asyncio
async def test_engine_dispatches_registered_host_planned_tool_without_model():
    core, request, options, repository, model, state = _core_fixture(
        host_direct_apply=True,
    )
    updates = []

    async for update in core.run(request, options=options):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            assert await core.resolve_approval(
                update.run_id,
                str(update.payload["approvalId"]),
                ApprovalDecision.APPROVE,
            ) is ApprovalStatus.APPROVED

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert state.domain["handler_order"] == ["read", "propose", "apply"]
    assert [
        tuple(schema.name for schema in invocation.tools)
        for invocation in model.invocations
    ] == [
        ("read_resource",),
        ("propose_change",),
        (),
    ]
    assert any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.HOST_PLANNED_TOOL_DISPATCHED
        for update in updates
    )
    assert any(
        trace.stage == "tool_dispatch"
        and trace.outcome == "host_planned_call"
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_dynamic_planner_is_not_recalled_for_normal_success():
    initial = TaskPlan(
        title="Stable roadmap",
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="propose",
                title="Propose change",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("propose_change",),
                risk_level=ToolRiskLevel.WRITE,
            ),
            TaskStep(
                id="respond",
                title="Respond",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                risk_level=ToolRiskLevel.READ,
            ),
        ),
    )
    planner = StopAfterObservationPlanner(initial)
    core, request, options, repository, _model, state = _core_fixture(
        planner=planner,
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert state.domain["handler_order"] == ["read", "propose"]
    assert planner.turns == []
    assert not any(
        trace.stage == "planning"
        and trace.outcome in {"replan_requested", "replanned"}
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_dynamic_planner_can_drop_tentative_steps_after_real_tool_result():
    initial = TaskPlan(
        title="Tentative roadmap",
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="propose",
                title="Tentatively propose a change",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("propose_change",),
                risk_level=ToolRiskLevel.WRITE,
            ),
        ),
    )
    planner = StopAfterObservationPlanner(initial)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
        replan_after_tools=frozenset({"read_resource"}),
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert state.domain["handler_order"] == ["read"]
    assert [tuple(schema.name for schema in call.tools) for call in model.invocations] == [
        ("read_resource",),
        (),
    ]
    assert len(planner.turns) == 1
    turn = planner.turns[0]
    assert [step.id for step in turn.completed_steps] == ["read"]
    assert any(message.role is MessageRole.TOOL for message in turn.messages)
    persisted_steps = repository.runs[result.run_id]["steps"]
    assert [(step.id, step.status) for step in persisted_steps] == [
        ("read", StepStatus.DONE),
        ("respond-now", StepStatus.DONE),
    ]
    todo_replacements = [
        item
        for item in updates
        if isinstance(item, AgentEvent)
        and item.type == CoreEventType.RUN_TODOS_UPDATED
    ]
    assert len(todo_replacements) == 2
    assert any(
        trace.stage == "planning" and trace.outcome == "replanned"
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_invalid_dynamic_replan_falls_back_to_trusted_remaining_plan():
    initial = TaskPlan(
        title="Trusted roadmap",
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="propose",
                title="Propose change",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("propose_change",),
                risk_level=ToolRiskLevel.WRITE,
            ),
            TaskStep(
                id="respond",
                title="Respond",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                risk_level=ToolRiskLevel.READ,
            ),
        ),
    )
    planner = InvalidAfterObservationPlanner(initial)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
        replan_after_tools=frozenset({
            "read_resource",
            "propose_change",
        }),
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert state.domain["handler_order"] == ["read", "propose"]
    assert [
        tuple(schema.name for schema in call.tools)
        for call in model.invocations
    ] == [
        ("read_resource",),
        ("propose_change",),
        (),
    ]
    assert len(planner.turns) == 2
    fallbacks = [
        trace
        for trace in repository.traces
        if trace.stage == "planning"
        and trace.outcome == "fallback_previous_plan"
    ]
    assert len(fallbacks) == 2
    assert all(
        trace.details["validationReason"]
        == "planner output is missing needsTodos"
        for trace in fallbacks
    )


@pytest.mark.asyncio
async def test_invalid_failure_replan_falls_back_to_safe_model_only_response():
    initial = TaskPlan(
        title="Trusted roadmap",
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="propose",
                title="Propose change",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("propose_change",),
                risk_level=ToolRiskLevel.WRITE,
            ),
        ),
    )
    planner = InvalidAfterObservationPlanner(initial)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
        read_error_code="source_unavailable",
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert state.domain["handler_order"] == ["read"]
    assert [
        tuple(schema.name for schema in call.tools)
        for call in model.invocations
    ] == [("read_resource",), ()]
    persisted_steps = repository.runs[result.run_id]["steps"]
    assert [(step.id, step.status) for step in persisted_steps] == [
        ("read", StepStatus.FAILED),
        ("respond-after-tool-failure-1", StepStatus.DONE),
    ]
    assert any(
        trace.stage == "planning"
        and trace.outcome == "fallback_safe_response"
        and trace.details["fallbackToolCount"] == 0
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_staged_context_runs_formal_retrieval_after_task_spec_planning():
    class _StagedProvider:
        def __init__(self):
            self.calls = []
            self.task = None

        async def build_planning_context(self, request, budget, signal=None):
            del request, budget, signal
            self.calls.append("planning")
            return ContextBundle(diagnostics={
                "hostPlanningFacts": {"planningManifest": True},
            })

        async def build_task_context(
            self,
            request,
            budget,
            task,
            signal=None,
        ):
            del request, budget, signal
            assert self.calls == ["planning"]
            self.calls.append("task")
            self.task = task
            return ContextBundle(blocks=(ContextBlock(
                name="fixture",
                content="TaskSpec-targeted evidence.",
            ),))

        async def build_context(self, request, budget, signal=None):
            del request, budget, signal
            self.calls.append("legacy")
            return ContextBundle()

    plan = TaskPlan(
        title="Read after recall",
        task_spec=TaskSpec(
            goal="read the previously referenced resource",
            target={"resource": "alpha"},
        ),
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="respond",
                title="Respond",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                risk_level=ToolRiskLevel.READ,
            ),
        ),
    )
    core, request, options, repository, _model, _state = _core_fixture(
        planner=StaticPlanner(plan),
    )
    provider = _StagedProvider()
    core._context_provider = provider

    updates = [item async for item in core.run(request, options=options)]

    assert updates[-1].status is RunStatus.DONE, repository.traces
    assert provider.calls == ["planning", "task"]
    assert provider.task.task_spec is plan.task_spec
    assert provider.task.planned_tool_names == ("read_resource",)
    assert provider.task.include_response_context is True
    assert any(
        trace.stage == "context_retrieval"
        and trace.outcome == "task_spec"
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_task_specific_context_demand_is_allocated_after_planning():
    class _TaskDemandProvider:
        def __init__(self):
            self.task_demand_calls = 0
            self.allocated = 0

        async def describe_context_demands(self, request, signal=None):
            del request, signal
            return (ContextBudgetClaim(
                name="base",
                minimum_tokens=100,
                desired_tokens=100,
                priority=100,
            ),)

        async def build_planning_context(self, request, budget, signal=None):
            del request, signal
            assert budget.allocation_for("artifact") == 0
            return ContextBundle()

        async def describe_task_context_demands(
            self,
            request,
            task,
            signal=None,
        ):
            del request, signal
            assert task.task_spec.target["resume"] is True
            self.task_demand_calls += 1
            return (ContextBudgetClaim(
                name="artifact",
                minimum_tokens=1_000,
                desired_tokens=3_000,
                priority=90,
            ),)

        async def build_task_context(
            self,
            request,
            budget,
            task,
            signal=None,
        ):
            del request, task, signal
            self.allocated = budget.allocation_for("artifact")
            return ContextBundle(blocks=(ContextBlock(
                name="artifact",
                content="bounded recovery projection",
            ),))

        async def build_context(self, request, budget, signal=None):
            del request, budget, signal
            return ContextBundle()

    plan = TaskPlan(
        title="Resume",
        task_spec=TaskSpec(
            goal="resume",
            target={"resume": True},
        ),
        steps=(TaskStep(
            id="respond",
            title="Respond",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            risk_level=ToolRiskLevel.READ,
        ),),
    )
    core, request, options, repository, _model, _state = _core_fixture(
        planner=StaticPlanner(plan),
    )
    provider = _TaskDemandProvider()
    core._context_provider = provider

    updates = [item async for item in core.run(request, options=options)]

    assert updates[-1].status is RunStatus.DONE, repository.traces
    assert provider.task_demand_calls == 1
    assert provider.allocated == 3_000
    budget_event = next(
        item for item in updates
        if isinstance(item, AgentEvent)
        and item.type == CoreEventType.CONTEXT_BUDGETED
    )
    assert budget_event.payload["contextAllocations"] == {
        "base": 100,
        "artifact": 3_000,
    }


@pytest.mark.asyncio
async def test_staged_context_missing_task_spec_falls_back_to_legacy_context():
    class _StagedProvider:
        def __init__(self):
            self.calls = []

        async def build_planning_context(self, request, budget, signal=None):
            del request, budget, signal
            self.calls.append("planning")
            return ContextBundle()

        async def build_task_context(
            self,
            request,
            budget,
            task,
            signal=None,
        ):
            del request, budget, task, signal
            raise AssertionError("missing TaskSpec must not use task recall")

        async def build_context(self, request, budget, signal=None):
            del request, budget, signal
            self.calls.append("legacy")
            return ContextBundle(blocks=(ContextBlock(
                name="fixture",
                content="Legacy evidence remains available.",
            ),))

    plan = TaskPlan(
        title="Legacy-compatible read",
        steps=(
            TaskStep(
                id="read",
                title="Read resource",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("read_resource",),
                risk_level=ToolRiskLevel.READ,
            ),
            TaskStep(
                id="respond",
                title="Respond",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                risk_level=ToolRiskLevel.READ,
            ),
        ),
    )
    core, request, options, repository, _model, _state = _core_fixture(
        planner=StaticPlanner(plan),
    )
    provider = _StagedProvider()
    core._context_provider = provider

    updates = [item async for item in core.run(request, options=options)]

    assert updates[-1].status is RunStatus.DONE
    assert provider.calls == ["planning", "legacy"]
    assert any(
        trace.stage == "context_retrieval"
        and trace.outcome == "legacy_fallback"
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_core_builds_host_planning_facts_before_planning_and_keeps_tool_guidance():
    core, request, options, _repository, model, _state = _core_fixture()
    provider = PlanningFactContextProvider()
    policy = CapturePlanningPolicy(provider)
    core._context_provider = provider
    core._planning_policy = policy
    model.plan = {
        "needsTodos": True,
        "title": "Read once",
        "todos": [
            {
                "id": "read",
                "title": "Read resource",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["read_resource"],
                "riskLevel": "read",
            },
            {
                "id": "respond",
                "title": "Respond",
                "type": "review",
                "executor": "model",
                "expectedTools": [],
                "riskLevel": "read",
            },
        ],
    }

    updates = []
    async for update in core.run(request, options=options):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            await core.resolve_approval(
                update.run_id,
                str(update.payload["approvalId"]),
                ApprovalDecision.REJECT,
            )

    assert policy.capabilities is not None
    assert policy.capabilities.host_planning_facts["currentChapter"]["bound"] is True
    assert policy.capabilities.tool_guidance["read_resource"] == {
        "purpose": "Generic test capability read_resource",
        "requires": [],
    }
    assert provider.build_count == 1
    budget_event = next(
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.CONTEXT_BUDGETED
    )
    # Dynamic planning keeps every request-scoped candidate schema available;
    # each runtime round still exposes only the latest authorized transition.
    assert budget_event.payload["reservedToolSchemaTokens"] == (
        budget_event.payload["toolSchemaTokens"]
    )


@pytest.mark.asyncio
async def test_core_runs_context_optimization_after_actual_plan_and_tools():
    class _CaptureOptimizer:
        def __init__(self):
            self.arguments = None

        async def optimize(
            self,
            request,
            *,
            provider_input_tokens,
            resolved_context_tokens,
            output_reserve_tokens,
            planned_step_count,
            planned_tool_count,
            selected_tool_names,
            signal=None,
            on_compaction_started=None,
        ):
            self.arguments = {
                "providerInputTokens": provider_input_tokens,
                "resolvedContextTokens": resolved_context_tokens,
                "outputReserveTokens": output_reserve_tokens,
                "plannedStepCount": planned_step_count,
                "plannedToolCount": planned_tool_count,
                "selectedToolNames": tuple(selected_tool_names),
            }
            assert on_compaction_started is not None
            await on_compaction_started({
                "selectedTurnCount": 2,
                "pressureRatio": 0.81,
            })
            return PostPlanningContextOptimizationResult(
                request=request,
                outcome="compacted",
                compacted_turn_count=2,
                retained_raw_turn_count=4,
                summary_version=2,
                diagnostics={"providerBudgetKind": "resolved"},
            )

    optimizer = _CaptureOptimizer()
    core, request, options, repository, _model, _state = _core_fixture()
    core._post_planning_context_optimizer = optimizer

    updates = []
    async for item in core.run(request, options=options):
        updates.append(item)
        if (
            isinstance(item, AgentEvent)
            and item.type == CoreEventType.APPROVAL_REQUESTED
        ):
            await core.resolve_approval(
                item.run_id,
                str(item.payload["approvalId"]),
                ApprovalDecision.APPROVE,
            )

    assert updates[-1].status is RunStatus.DONE
    assert optimizer.arguments is not None
    assert optimizer.arguments["plannedStepCount"] == 4
    assert optimizer.arguments["plannedToolCount"] == 3
    assert optimizer.arguments["selectedToolNames"] == (
        "apply_change",
        "propose_change",
        "read_resource",
    )
    assert optimizer.arguments["resolvedContextTokens"] > 0
    compaction_events = [
        item
        for item in updates
        if isinstance(item, AgentEvent)
        and item.type.startswith("conversation.compaction.")
    ]
    assert [item.type for item in compaction_events] == [
        "conversation.compaction.started",
        "conversation.compaction.completed",
    ]
    assert compaction_events[-1].payload["postPlanning"] is True
    budget_event = next(
        item
        for item in updates
        if isinstance(item, AgentEvent)
        and item.type == CoreEventType.CONTEXT_BUDGETED
    )
    assert budget_event.payload["diagnostics"][
        "postPlanningOptimization"
    ]["outcome"] == "compacted"
    trace = next(
        item
        for item in repository.traces
        if item.stage == "post_planning_context_optimization"
    )
    assert trace.outcome == "compacted"


@pytest.mark.asyncio
async def test_core_offers_compression_hook_at_planning_and_model_boundaries():
    class _CaptureCompactor:
        def __init__(self):
            self.calls = []

        async def prepare(
            self,
            request,
            signal=None,
            *,
            on_compaction_started=None,
            **budget,
        ):
            self.calls.append({
                "messages": tuple(request.messages),
                "budget": dict(budget),
            })
            if on_compaction_started is not None:
                await on_compaction_started({"selectedTurnCount": 1})
            return ConversationCompactionResult(
                request=replace(
                    request,
                    metadata={
                        **dict(request.metadata),
                        "coreCompactionPass": len(self.calls),
                    },
                ),
                outcome="compacted",
                compacted_turn_count=1,
                retained_raw_turn_count=4,
                diagnostics={"decisionOwner": "agent_core"},
            )

    compactor = _CaptureCompactor()
    core, request, options, _repository, _model, _state = _core_fixture()
    core._conversation_compactor = compactor

    updates = []
    async for item in core.run(request, options=options):
        updates.append(item)
        if (
            isinstance(item, AgentEvent)
            and item.type == CoreEventType.APPROVAL_REQUESTED
        ):
            await core.resolve_approval(
                item.run_id,
                str(item.payload["approvalId"]),
                ApprovalDecision.APPROVE,
            )

    assert updates[-1].status is RunStatus.DONE
    assert len(compactor.calls) >= 3
    preflight, post_planning, *model_calls = compactor.calls
    assert preflight["messages"] == request.messages
    assert post_planning["messages"] == request.messages
    preflight_budget = preflight["budget"]["budget"]
    post_planning_budget = post_planning["budget"]["budget"]
    assert preflight_budget.provider_input_tokens > 0
    assert preflight_budget.phase.value == "pre_planning"
    assert preflight_budget.planned_step_count == 0
    assert post_planning_budget.phase.value == "post_planning"
    assert post_planning_budget.planned_step_count == 4
    assert post_planning_budget.planned_tool_count == 3
    assert all(
        item["budget"]["budget"].phase.value == "model_call"
        for item in model_calls
    )
    compaction_events = [
        item
        for item in updates
        if isinstance(item, AgentEvent)
        and item.type.startswith("conversation.compaction.")
    ]
    assert [item.payload["phase"] for item in compaction_events] == [
        "pre_planning",
        "pre_planning",
        "post_planning",
        "post_planning",
    ]
    assert all(
        item.run_id == updates[-1].run_id for item in compaction_events
    )


def test_dependency_edge_waivers_require_available_tools_and_a_declared_edge():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"catalog", "read", "write"}),
        tool_guidance={
            "catalog": {"purpose": "catalog", "requires": []},
            "read": {"purpose": "read", "requires": ["catalog"]},
            "write": {"purpose": "write", "requires": ["catalog"]},
        },
    )

    _validate_planning_constraints(
        capabilities,
        PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({
                ("read", "catalog"),
            }),
        ),
    )
    with pytest.raises(ContractViolationError, match="unavailable tools"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                planning_excluded_tool_names=frozenset({"missing"}),
            ),
        )
    with pytest.raises(ContractViolationError, match="both context-satisfied"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                context_satisfied_tool_names=frozenset({"read"}),
                planning_excluded_tool_names=frozenset({"read"}),
            ),
        )
    with pytest.raises(ContractViolationError, match="still required"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                planning_excluded_tool_names=frozenset({"catalog"}),
            ),
        )
    with pytest.raises(ContractViolationError, match="unavailable tools"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                satisfied_tool_dependency_edges=frozenset({
                    ("missing", "catalog"),
                }),
            ),
        )
    with pytest.raises(ContractViolationError, match="undeclared edge"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                satisfied_tool_dependency_edges=frozenset({
                    ("read", "write"),
                }),
            ),
        )
    with pytest.raises(ContractViolationError, match="unavailable tools"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                required_any_tool_names=frozenset({"missing"}),
            ),
        )
    with pytest.raises(ContractViolationError, match="remain selectable"):
        _validate_planning_constraints(
            capabilities,
            PlanningConstraints(
                planning_excluded_tool_names=frozenset({"write"}),
                required_any_tool_names=frozenset({"write"}),
            ),
        )


def test_task_planning_constraints_can_add_but_not_remove_host_guards():
    base = PlanningConstraints(
        context_satisfied_tool_names=frozenset({"read"}),
        planning_excluded_tool_names=frozenset({"delete"}),
        satisfied_tool_dependency_edges=frozenset({("write", "read")}),
        required_any_tool_names=frozenset({"write"}),
        allow_model_only_fallback=False,
    )
    _validate_task_constraint_refinement(
        base,
        PlanningConstraints(
            context_satisfied_tool_names=frozenset({"read", "catalog"}),
            planning_excluded_tool_names=frozenset({"delete"}),
            satisfied_tool_dependency_edges=frozenset({
                ("write", "read"),
                ("publish", "write"),
            }),
            required_any_tool_names=frozenset({"write", "publish"}),
            allow_model_only_fallback=False,
        ),
    )
    with pytest.raises(ContractViolationError, match="cannot weaken"):
        _validate_task_constraint_refinement(
            base,
            PlanningConstraints(
                planning_excluded_tool_names=frozenset({"delete"}),
                satisfied_tool_dependency_edges=frozenset({("write", "read")}),
                required_any_tool_names=frozenset({"write"}),
                allow_model_only_fallback=False,
            ),
        )
    with pytest.raises(ContractViolationError, match="cannot weaken"):
        _validate_task_constraint_refinement(
            base,
            PlanningConstraints(
                context_satisfied_tool_names=frozenset({"read"}),
                planning_excluded_tool_names=frozenset({"delete"}),
                satisfied_tool_dependency_edges=frozenset({("write", "read")}),
                required_any_tool_names=frozenset({"write"}),
                allow_model_only_fallback=True,
            ),
        )


@pytest.mark.asyncio
async def test_valid_context_constraints_reach_planner_without_narrowing_runtime_catalog():
    plan = TaskPlan(
        title="Respond from trusted context",
        steps=(TaskStep(
            id="respond",
            title="Respond",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            risk_level=ToolRiskLevel.READ,
        ),),
    )
    planner = CapturePlanner(plan)
    core, request, options, _repository, model, _state = _core_fixture(
        planner=planner,
    )
    provider = PlanningFactContextProvider()
    policy = ConstraintPlanningPolicy("read_resource")
    core._context_provider = provider
    core._planning_policy = policy

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert provider.build_count == 1
    assert policy.base_capabilities is not None
    assert policy.base_capabilities.constraints == PlanningConstraints()
    assert planner.call_count == 1
    assert planner.capabilities is policy.capabilities
    assert planner.capabilities.constraints == PlanningConstraints(
        context_satisfied_tool_names=frozenset({"read_resource"}),
    )
    assert planner.capabilities.host_planning_facts["currentChapter"][
        "bound"
    ] is True
    assert planner.capabilities.available_tool_names == {
        "read_resource",
        "propose_change",
        "apply_change",
    }
    assert set(planner.capabilities.tool_guidance) == {
        "read_resource",
        "propose_change",
        "apply_change",
    }
    assert {
        registration.schema.name
        for registration in core._tool_catalog.registrations()
    } == {
        "read_resource",
        "propose_change",
        "apply_change",
    }
    assert core._tool_catalog.enabled_names(request) == {
        "read_resource",
        "propose_change",
        "apply_change",
    }
    assert model.completions == []
    assert [
        tuple(schema.name for schema in invocation.tools)
        for invocation in model.invocations
    ] == [()]


@pytest.mark.asyncio
async def test_planning_policy_cannot_satisfy_a_tool_outside_request_scope():
    planner = CapturePlanner(TaskPlan(
        title="Respond",
        steps=(TaskStep(
            id="respond",
            title="Respond",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            risk_level=ToolRiskLevel.READ,
        ),),
    ))
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
    )
    policy = ConstraintPlanningPolicy("unavailable_resource_reader")
    core._planning_policy = policy

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert policy.base_capabilities is not None
    assert policy.capabilities is None
    assert planner.call_count == 0
    assert model.completions == []
    assert model.invocations == []
    assert state.domain["handler_order"] == []
    assert repository.runs[result.run_id]["steps"] == []


@pytest.mark.asyncio
async def test_core_authority_rejects_custom_plan_for_context_satisfied_tool():
    plan = TaskPlan(
        title="Redundant read",
        steps=(TaskStep(
            id="read",
            title="Read resource again",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            suggested_tools=("read_resource",),
        ),),
    )
    planner = CapturePlanner(plan)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
    )
    policy = ConstraintPlanningPolicy("read_resource")
    core._planning_policy = policy

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert planner.call_count == 1
    assert planner.capabilities.constraints == policy.constraints
    assert model.completions == []
    assert model.invocations == []
    assert state.domain["handler_order"] == []
    assert repository.runs[result.run_id]["steps"] == []


@pytest.mark.asyncio
async def test_core_authority_rejects_custom_plan_for_scope_excluded_tool():
    plan = TaskPlan(
        title="Out-of-scope discovery",
        steps=(TaskStep(
            id="dashboard",
            title="Read unrelated dashboard",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            suggested_tools=("read_resource",),
        ),),
    )
    planner = CapturePlanner(plan)
    core, request, options, repository, model, state = _core_fixture(
        planner=planner,
    )
    policy = ConstraintPlanningPolicy(
        planning_excluded_tool_names=("read_resource",),
    )
    core._planning_policy = policy

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert planner.call_count == 1
    assert planner.capabilities.constraints == policy.constraints
    assert model.completions == []
    assert model.invocations == []
    assert state.domain["handler_order"] == []
    assert repository.runs[result.run_id]["steps"] == []


@pytest.mark.asyncio
async def test_standalone_core_cancels_during_approval_without_running_confirm_handler():
    core, request, options, repository, model, state = _core_fixture()
    signal = asyncio.Event()
    updates = []

    async for update in core.run(request, options=options, signal=signal):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            signal.set()

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.CANCELED
    assert state.domain["handler_order"] == ["read", "propose"]
    assert state.domain["value"] == "before"
    assert repository.runs[result.run_id]["status"] is RunStatus.CANCELED
    assert await core.cancel_pending_approvals(result.run_id) == 0


@pytest.mark.asyncio
async def test_closing_public_stream_persists_canceled_run_and_cleans_approval():
    core, request, options, repository, model, state = _core_fixture()
    stream = core.run(request, options=options)
    run_id = None
    while True:
        update = await anext(stream)
        if isinstance(update, AgentEvent):
            run_id = update.run_id or run_id
            if update.type == CoreEventType.APPROVAL_REQUESTED:
                break

    await stream.aclose()

    assert run_id is not None
    assert repository.runs[run_id]["status"] is RunStatus.CANCELED
    assert state.domain["handler_order"] == ["read", "propose"]
    assert await core.cancel_pending_approvals(run_id) == 0


@pytest.mark.asyncio
async def test_closing_immediately_after_run_started_never_leaves_a_running_run():
    core, request, options, repository, model, state = _core_fixture()
    stream = core.run(request, options=options)
    started = await anext(stream)
    assert isinstance(started, AgentEvent)
    assert started.type == CoreEventType.RUN_STARTED

    await stream.aclose()

    assert started.run_id is not None
    assert repository.runs[started.run_id]["status"] is RunStatus.CANCELED
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_outer_cancellation_after_atomic_begin_commit_closes_the_run():
    repository = DelayedBeginRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    next_update = asyncio.create_task(anext(stream))
    await repository.committed.wait()

    next_update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_update
    repository.release.set()

    assert len(repository.runs) == 1
    run = next(iter(repository.runs.values()))
    assert run["status"] is RunStatus.CANCELED
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_outer_cancellation_does_not_wait_for_precommit_begin_block():
    repository = BlockedBeforeBeginRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    next_update = asyncio.create_task(anext(stream))
    await repository.entered.wait()

    next_update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(next_update, timeout=0.2)
    assert repository.runs == {}

    # No retained background operation may turn a definitely canceled begin
    # into a later write.
    repository.release.set()
    await asyncio.sleep(0)
    assert repository.runs == {}
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_disconnect_surfaces_terminal_commit_failure_to_aclose_caller():
    repository = FailingTerminalRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    started = await anext(stream)
    assert isinstance(started, AgentEvent)
    assert started.type == CoreEventType.RUN_STARTED

    with pytest.raises(RuntimeError, match="terminal commit failed"):
        await stream.aclose()

    assert started.run_id is not None
    assert repository.runs[started.run_id]["status"] is RunStatus.RUNNING
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_planner_failure_still_has_a_traceable_failed_run_and_one_result():
    core, request, options, repository, model, state = _core_fixture(
        invalid_plan=True
    )
    updates = [item async for item in core.run(request, options=options)]

    event_types = [
        item.type for item in updates if isinstance(item, AgentEvent)
    ]
    assert event_types[0] == CoreEventType.RUN_STARTED
    assert CoreEventType.RUN_FAILED not in event_types
    assert event_types[-1] == CoreEventType.RUN_COMPLETED
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert result.error is None
    assert repository.runs[result.run_id]["status"] is RunStatus.DONE
    assert [tuple(item.tools) for item in model.invocations] == [()]
    assert state.domain["handler_order"] == []
    invalid_trace = next(
        trace
        for trace in repository.traces
        if trace.stage == "planning" and trace.outcome == "invalid"
    )
    assert invalid_trace.details["reasonCode"] == "invalid_json"
    assert invalid_trace.details["validationReason"] == (
        "planner output is not valid JSON"
    )
    assert any(
        trace.stage == "planning"
        and trace.outcome == "fallback_model_only"
        and trace.details["fallbackToolCount"] == 0
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_host_can_deny_model_only_fallback_for_invalid_plan():
    class FailClosedPlanningPolicy(AlwaysPlan):
        def planning_constraints(self, request, capabilities):
            del request, capabilities
            return PlanningConstraints(allow_model_only_fallback=False)

    core, request, options, repository, model, state = _core_fixture(
        invalid_plan=True,
        planning_policy=FailClosedPlanningPolicy(),
    )

    updates = [item async for item in core.run(request, options=options)]

    event_types = [
        item.type for item in updates if isinstance(item, AgentEvent)
    ]
    assert CoreEventType.RUN_FAILED in event_types
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_invalid"
    assert model.invocations == []
    assert state.domain["handler_order"] == []
    assert any(
        trace.stage == "planning"
        and trace.outcome == "fallback_denied"
        and trace.details["hostPolicy"] == "deny_model_only_fallback"
        for trace in repository.traces
    )


@pytest.mark.asyncio
async def test_default_planner_tool_limit_tracks_runtime_round_capacity():
    core, request, options, _repository, model, _state = _core_fixture(
        runtime_limits=RuntimeLimits(max_model_rounds=1),
    )

    updates = [item async for item in core.run(request, options=options)]

    planner_payload = json.loads(model.completions[0][0][1].content)
    assert planner_payload["maxToolSteps"] == 0
    assert len(model.completions) == 2
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.DONE
    assert result.error is None
    assert [tuple(item.tools) for item in model.invocations] == [()]


@pytest.mark.asyncio
async def test_core_authority_rejects_custom_multi_tool_step_without_expansion():
    unsafe_plan = TaskPlan(
        title="unsafe",
        steps=(TaskStep(
            id="combined",
            title="Combined mutation",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.DESTRUCTIVE,
            suggested_tools=("propose_change", "apply_change"),
        ),),
    )
    core, request, options, _repository, model, state = _core_fixture(
        planner=StaticPlanner(unsafe_plan),
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert model.completions == []
    assert model.invocations == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_core_authority_reserves_one_runtime_round_for_final_response():
    plan = TaskPlan(
        title="three reads",
        steps=tuple(
            TaskStep(
                id=f"read-{index}",
                title=f"Read {index}",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=(tool,),
            )
            for index, tool in enumerate(
                ("read_resource", "propose_change", "apply_change")
            )
        ),
    )
    core, request, options, _repository, model, state = _core_fixture(
        planner=StaticPlanner(plan),
        runtime_limits=RuntimeLimits(max_model_rounds=3),
    )

    updates = [item async for item in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_contract_violation"
    assert model.completions == []
    assert model.invocations == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_runtime_domain_effect_cannot_inject_controller_owned_lifecycle_event():
    core, request, options, repository, _, _ = _core_fixture(
        proposed_effect_type=CoreEventType.RUN_COMPLETED,
    )

    updates = [update async for update in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "tool_execution_error"
    persisted_types = [event.type for event in repository.events]
    assert CoreEventType.RUN_COMPLETED not in persisted_types
    assert [
        event_type
        for event_type in persisted_types
        if event_type in {
            CoreEventType.RUN_COMPLETED,
            CoreEventType.RUN_BLOCKED,
            CoreEventType.RUN_FAILED,
            CoreEventType.RUN_CANCELED,
        }
    ] == [CoreEventType.RUN_FAILED]


@pytest.mark.asyncio
async def test_reused_core_does_not_leak_request_scoped_response_judge():
    class _DirectPolicy:
        def planning_constraints(self, request, capabilities):
            del request, capabilities
            return PlanningConstraints()

        def should_plan(self, request, capabilities):
            del request, capabilities
            return False

    class _EmptyContext:
        async def build_context(self, request, budget, signal=None):
            del request, budget, signal
            return ContextBundle()

    class _FinalGateway:
        async def complete(self, messages, invocation, signal=None):
            raise AssertionError("direct runs must not invoke the planner")

        async def stream(self, messages, invocation, signal=None):
            del messages, invocation, signal
            return ModelStream(
                chunks=_final_chunks("request-scoped response"),
                model="runtime-model",
            )

    class _CountingJudge:
        def __init__(self):
            self.calls = 0

        async def judge(self, *, content, messages, signal=None):
            del content, messages, signal
            self.calls += 1
            return ResponseValidationResult()

    judge = _CountingJudge()
    core = AgentCore(
        model_gateway=_FinalGateway(),
        run_repository=MemoryRunRepository(),
        planning_policy=_DirectPolicy(),
        context_provider=_EmptyContext(),
    )
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="answer"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=DomainContext(namespace="fixture"),
    )

    first = [
        update async for update in core.run(
            request,
            options=AgentCoreRunOptions(response_judges=(judge,)),
        )
    ]
    second = [
        update async for update in core.run(
            request,
            options=AgentCoreRunOptions(),
        )
    ]

    assert first[-1].status is RunStatus.DONE
    assert second[-1].status is RunStatus.DONE
    assert judge.calls == 1


def test_standalone_engine_test_has_no_application_or_domain_imports():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned = {
        "application",
        "database",
        "domains",
        "infrastructure",
        "routers",
        "services",
    }
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(banned)
