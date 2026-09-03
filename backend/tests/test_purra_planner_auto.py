"""PurrTypos conformance checks for the installed PurrA Planner Auto contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from purra.api import (
    AgentComponentBinding,
    AgentCore,
    AgentCoreRunOptions,
    AgentPreset,
    ExecutionProfile,
    InMemoryAgentAdapters,
    PlanningMode,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBundle,
    DomainContext,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    PlanningKind,
    PlanningResult,
    RunStatus,
    RuntimeLimits,
    StepExecutor,
    StepType,
    TaskSpec,
    ToolCallDelta,
    ToolHandlerResult,
    ToolPlanningRequirement,
    ToolPolicy,
    ToolSchema,
    WorkPlan,
    WorkStep,
)
from purra.model_protocol import generic_capability_snapshot
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog


async def _text_chunks(text: str):
    yield ModelStreamChunk(
        content_delta=text,
        finish_reason=ModelFinishReason.STOP,
    )


class _Gateway:
    def __init__(self, response: str = "直接回答") -> None:
        self.response = response
        self.invocations = []

    async def stream(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        return ModelStream(
            chunks=_text_chunks(self.response),
            model="fixture-model",
            applied_output_limit=invocation.output_limit.max_tokens,
        )

    async def complete(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        return ModelCompletion(
            message=AgentMessage(
                role=MessageRole.ASSISTANT,
                content=self.response,
            ),
            model="fixture-model",
            applied_output_limit=invocation.output_limit.max_tokens,
            finish_reason=ModelFinishReason.STOP,
        )


class _ScriptedToolGateway(_Gateway):
    def __init__(self, steps) -> None:
        super().__init__("规划后回答")
        self.steps = list(steps)

    async def stream(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        step = self.steps.pop(0)

        async def chunks():
            if step is None:
                yield ModelStreamChunk(
                    content_delta=self.response,
                    finish_reason=ModelFinishReason.STOP,
                )
                return
            yield ModelStreamChunk(
                tool_call_deltas=(ToolCallDelta(
                    index=0,
                    id=f"call-{step}-{len(self.invocations)}",
                    name=step,
                    arguments_fragment="{}",
                ),),
                finish_reason=ModelFinishReason.TOOL_CALLS,
            )

        return ModelStream(
            chunks=chunks(),
            model="fixture-model",
            applied_output_limit=invocation.output_limit.max_tokens,
        )


class _Context:
    async def build_context(self, request, budget, signal=None):
        del request, budget, signal
        return ContextBundle()

    async def build_planning_context(self, request, budget, signal=None):
        del request, budget, signal
        return ContextBundle()

    async def build_task_context(self, request, budget, task, signal=None):
        del request, budget, task, signal
        return ContextBundle()


class _PlanningPolicy:
    def planning_constraints(self, request, capabilities):
        del request
        return capabilities.constraints


class _Planner:
    def __init__(
        self,
        order: list[str] | None = None,
        work_plan: WorkPlan | None = None,
    ) -> None:
        self.calls = 0
        self.order = order
        self.work_plan = work_plan or WorkPlan(
            title="执行计划",
            task_spec=TaskSpec(goal="安全完成任务"),
            steps=(WorkStep(
                id="answer",
                title="完成任务",
                type=StepType.WRITE,
                executor=StepExecutor.MODEL,
            ),),
        )

    async def create_plan(
        self,
        request,
        capabilities,
        signal=None,
        *,
        run_id=None,
        turn_id=None,
        reasoning_mode=None,
    ):
        del request, capabilities, signal, run_id, turn_id, reasoning_mode
        self.calls += 1
        if self.order is not None:
            self.order.append("planner")
        return PlanningResult(
            kind=PlanningKind.PLANNED,
            work_plan=self.work_plan,
        )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="处理请求"),),
        model=ModelRequest(
            provider="fixture",
            model="fixture-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="fixture:model",
                max_call_output_tokens=1_024,
            ),
            options={"max_tokens": 512},
        ),
        domain_context=DomainContext(namespace="purrtypos.test"),
        context_window=32_768,
    )


def _core(
    *,
    gateway,
    planner=None,
    catalog=None,
    adapters=None,
    runtime_limits=None,
):
    adapters = adapters or InMemoryAgentAdapters()
    profile = ExecutionProfile(
        planner=planner,
        planning_policy=_PlanningPolicy() if planner is not None else None,
    )
    bindings = {
        "contextProvider": AgentComponentBinding("purrtypos.test.context", "1"),
    }
    if planner is not None:
        bindings.update({
            "planner": AgentComponentBinding("purrtypos.test.planner", "1"),
            "planningPolicy": AgentComponentBinding(
                "purrtypos.test.planning-policy",
                "1",
            ),
        })
    return AgentCore(
        model_gateway=gateway,
        run_repository=adapters.runs,
        output_repository=adapters.outputs,
        output_publisher=adapters.publisher,
        preset=AgentPreset(
            id="purrtypos-test",
            revision="1",
            tool_catalog=catalog or InMemoryToolCatalog(()),
            context_provider=_Context(),
            execution_profile=profile,
            runtime_limits=runtime_limits or RuntimeLimits(
                max_run_output_tokens=None,
            ),
            component_bindings=bindings,
        ),
    )


@pytest.mark.asyncio
async def test_auto_direct_answer_uses_one_normal_model_call_without_planning():
    gateway = _Gateway()
    planner = _Planner()
    adapters = InMemoryAgentAdapters()
    core = _core(gateway=gateway, planner=planner, adapters=adapters)
    try:
        result = await (await core.submit(_request())).wait()
        events = await adapters.outputs.list_events(result.run_id, after_sequence=0)
    finally:
        await core.close()

    assert result.status is RunStatus.DONE
    assert result.final_response == "直接回答"
    assert len(gateway.invocations) == 1
    assert [tool.name for tool in gateway.invocations[0].tools] == ["request_plan"]
    assert planner.calls == 0
    assert all(
        event.payload.get("kind") != "planning"
        for event in events
        if event.kind.value == "operation.started"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("planner,model_supports_tools", [
    (None, True),
    (_Planner(), False),
])
async def test_auto_does_not_advertise_request_plan_without_capability(
    planner,
    model_supports_tools,
):
    gateway = _Gateway()
    core = _core(gateway=gateway, planner=planner)
    try:
        result = await (await core.submit(
            _request(),
            options=AgentCoreRunOptions(
                model_supports_tools=model_supports_tools,
            ),
        )).wait()
    finally:
        await core.close()

    assert result.status is RunStatus.DONE
    assert len(gateway.invocations) == 1
    assert gateway.invocations[0].tools == ()
    if planner is not None:
        assert planner.calls == 0


@pytest.mark.asyncio
async def test_auto_request_plan_promotes_inside_core_and_stays_private():
    gateway = _ScriptedToolGateway(("request_plan", None))
    planner = _Planner()
    adapters = InMemoryAgentAdapters()
    core = _core(gateway=gateway, planner=planner, adapters=adapters)
    try:
        result = await (await core.submit(_request())).wait()
        events = await adapters.outputs.list_events(result.run_id, after_sequence=0)
    finally:
        await core.close()

    assert result.status is RunStatus.DONE
    assert planner.calls == 1
    assert len(gateway.invocations) == 2
    assert gateway.invocations[1].tools == ()
    public_payload = "\n".join(
        str(event.payload)
        for event in events
        if event.visibility.value == "public"
    )
    assert "request_plan" not in public_payload
    assert all(
        event.payload.get("toolName") != "request_plan"
        for event in events
        if event.kind.value == "tool.event"
    )


def _required_catalog(effect_order: list[str]) -> InMemoryToolCatalog:
    async def publish(state, arguments, signal=None):
        del state, arguments, signal
        effect_order.append("effect")
        return ToolHandlerResult(content="published", effect_state="not_started")

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(
            name="publish",
            description="Publish a prepared result.",
            parameters={"type": "object", "properties": {}},
        ),
        handler=publish,
        policy=ToolPolicy(mode="read", title="Publish"),
        planning_requirement=ToolPlanningRequirement.REQUIRED,
    ),))


@pytest.mark.asyncio
async def test_required_tool_promotes_before_effect_and_reactive_fails_closed():
    order: list[str] = []
    planner = _Planner(order, WorkPlan(
        title="发布计划",
        task_spec=TaskSpec(goal="安全发布"),
        steps=(WorkStep(
            id="publish",
            title="发布",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            capability_names=("publish",),
        ),),
    ))
    catalog = _required_catalog(order)
    auto = _core(
        gateway=_ScriptedToolGateway(("publish", "publish", None)),
        planner=planner,
        catalog=catalog,
    )
    try:
        auto_result = await (await auto.submit(replace(
            _request(),
            tools_enabled=True,
        ))).wait()
    finally:
        await auto.close()

    assert auto_result.status is RunStatus.DONE
    assert order == ["planner", "effect"]

    reactive_planner = _Planner()
    reactive = _core(
        gateway=_ScriptedToolGateway(("publish",)),
        planner=reactive_planner,
        catalog=catalog,
    )
    try:
        reactive_result = await (await reactive.submit(replace(
            _request(),
            tools_enabled=True,
            planning_mode=PlanningMode.REACTIVE,
        ))).wait()
    finally:
        await reactive.close()

    assert reactive_result.status is RunStatus.FAILED
    assert reactive_result.error == "planning_required"
    assert reactive_planner.calls == 0
    assert order == ["planner", "effect"]


@pytest.mark.asyncio
async def test_auto_plans_remaining_work_after_an_ordinary_tool_executes():
    effects: list[str] = []

    async def execute(state, arguments, signal=None):
        del state, arguments, signal
        effects.append("executed")
        return ToolHandlerResult(content="ok", effect_state="not_started")

    def registration(name, requirement=ToolPlanningRequirement.OPTIONAL):
        return ToolRegistration(
            schema=ToolSchema(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
            ),
            handler=execute,
            policy=ToolPolicy(mode="read", title=name),
            planning_requirement=requirement,
        )

    planner = _Planner(work_plan=WorkPlan(
        title="剩余工作",
        task_spec=TaskSpec(goal="完成发布"),
        steps=(WorkStep(
            id="publish",
            title="发布",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            capability_names=("publish",),
        ),),
    ))
    core = _core(
        gateway=_ScriptedToolGateway(("lookup", "publish", "publish", None)),
        planner=planner,
        catalog=InMemoryToolCatalog((
            registration("lookup"),
            registration("publish", ToolPlanningRequirement.REQUIRED),
        )),
    )
    try:
        result = await (await core.submit(replace(
            _request(),
            tools_enabled=True,
        ))).wait()
    finally:
        await core.close()

    assert result.status is RunStatus.DONE
    assert planner.calls == 1
    assert effects == ["executed", "executed"]


@pytest.mark.asyncio
async def test_explicit_modes_and_planning_availability_are_fail_closed():
    unavailable_gateway = _Gateway("must not run")
    unavailable = _core(gateway=unavailable_gateway)
    try:
        result = await (await unavailable.submit(replace(
            _request(),
            planning_mode=PlanningMode.PLANNED,
        ))).wait()
    finally:
        await unavailable.close()

    assert result.status is RunStatus.FAILED
    assert result.error == "planning_unavailable"
    assert unavailable_gateway.invocations == []

    planner = _Planner()
    reactive_gateway = _Gateway()
    reactive = _core(gateway=reactive_gateway, planner=planner)
    try:
        result = await (await reactive.submit(replace(
            _request(),
            planning_mode=PlanningMode.REACTIVE,
        ))).wait()
    finally:
        await reactive.close()

    assert result.status is RunStatus.DONE
    assert planner.calls == 0
    assert len(reactive_gateway.invocations) == 1
    assert reactive_gateway.invocations[0].tools == ()


@pytest.mark.asyncio
async def test_auto_activation_cannot_exceed_the_shared_model_round_budget():
    gateway = _ScriptedToolGateway(("request_plan",))
    planner = _Planner()
    core = _core(
        gateway=gateway,
        planner=planner,
        runtime_limits=RuntimeLimits(
            max_model_rounds=1,
            max_run_output_tokens=None,
        ),
    )
    try:
        result = await (await core.submit(_request())).wait()
    finally:
        await core.close()

    assert result.status is RunStatus.FAILED
    assert result.error == "planning_activation_budget_exhausted"
    assert len(gateway.invocations) == 1
    assert planner.calls == 0
