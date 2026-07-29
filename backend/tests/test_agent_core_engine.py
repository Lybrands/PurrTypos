from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

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
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
    TraceRecord,
)
from agent_core.engine import (
    AgentCore,
    AgentCoreRunOptions,
    _validate_planning_constraints,
)
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import RunBeginResult, RunCommit, ToolRegistration
from agent_core.tools import InMemoryToolCatalog


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


def _registration(name, handler, *, mode, risk):
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
    )


def _core_fixture(
    *,
    invalid_plan: bool = False,
    repository: MemoryRunRepository | None = None,
    proposed_effect_type: str = "test.change_proposed",
    planner=None,
    runtime_limits: RuntimeLimits = RuntimeLimits(),
):
    state_factory = SharedStateFactory()

    async def read_resource(state, arguments, signal=None):
        state.domain["handler_order"].append("read")
        return ToolHandlerResult(json.dumps({"value": state.domain["value"]}))

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
        )

    async def apply_change(state, arguments, signal=None):
        state.domain["handler_order"].append("apply")
        state.domain["value"] = state.domain["proposal"]
        return ToolHandlerResult(json.dumps({"value": state.domain["value"]}))

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
        planning_policy=AlwaysPlan(),
        context_provider=FixtureContextProvider(),
        execution_state_factory=state_factory,
        tool_catalog=catalog,
        runtime_limits=runtime_limits,
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

    assert [
        item.type for item in updates if isinstance(item, AgentEvent)
    ] == [CoreEventType.RUN_STARTED, CoreEventType.RUN_FAILED]
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_invalid"
    assert repository.runs[result.run_id]["status"] is RunStatus.FAILED
    assert model.invocations == []
    assert state.domain["handler_order"] == []


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
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_invalid"
    assert model.invocations == []


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
