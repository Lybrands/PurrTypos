from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageRole,
    ModelCompletion,
    ModelRequest,
    PlanningCapabilities,
    PlanningKind,
    ReasoningMode,
    StepExecutor,
)
from agent_core.errors import InvalidPlannerOutputError, UnsupportedModelFeatureError
from agent_core.planner import AgentPlanner, normalize_task_plan, parse_planner_output


class FakeModelGateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        self.invocations.append((tuple(messages), invocation, signal))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ModelCompletion(
            message=AgentMessage(role=MessageRole.ASSISTANT, content=response),
            model="planner-model",
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise AssertionError("planner must not stream")


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="research then change it"),),
        model=ModelRequest(provider="test", model="model"),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )


@pytest.mark.asyncio
async def test_planner_uses_non_streaming_gateway_and_normalizes_safe_plan():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"Do work","goal":"finish",'
        '"todos":[{"id":"inspect","title":"Inspect","type":"read",'
        '"executor":"tool","expectedTools":["lookup"],"riskLevel":"read"},'
        '{"id":"answer","title":"Answer","type":"review",'
        '"executor":"model","expectedTools":[]}]}'
    )
    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(available_tool_names=frozenset({"lookup"})),
    )

    assert result.kind is PlanningKind.PLANNED
    assert result.model == "planner-model"
    assert result.plan.steps[0].executor is StepExecutor.TOOL
    assert result.plan.steps[0].suggested_tools == ("lookup",)
    assert gateway.invocations[0][1].reasoning_mode is ReasoningMode.DISABLED
    assert gateway.invocations[0][1].tools == ()


@pytest.mark.asyncio
async def test_planner_retries_only_an_explicit_unsupported_reasoning_feature():
    gateway = FakeModelGateway(
        UnsupportedModelFeatureError(),
        '{"needsTodos":false,"reason":"one response is enough"}',
    )
    result = await AgentPlanner(gateway).create_plan(
        _request(), PlanningCapabilities()
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    assert len(gateway.invocations) == 2
    assert gateway.invocations[1][1].reasoning_mode is ReasoningMode.DEFAULT


def test_planner_output_is_strict_and_never_expands_tool_authority():
    capabilities = PlanningCapabilities(available_tool_names=frozenset({"lookup"}))

    with pytest.raises(InvalidPlannerOutputError, match="needsTodos"):
        normalize_task_plan({"reason": "ambiguous"}, capabilities)
    with pytest.raises(InvalidPlannerOutputError, match="unavailable"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "bad",
                "title": "Bad",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["admin"],
            }],
        }, capabilities)
    with pytest.raises(InvalidPlannerOutputError, match="model steps"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "bad",
                "title": "Bad",
                "type": "review",
                "executor": "model",
                "expectedTools": ["lookup"],
            }],
        }, capabilities)


def test_parser_accepts_json_fence_but_rejects_non_object_output():
    assert parse_planner_output('```json\n{"needsTodos":false}\n```')["needsTodos"] is False
    with pytest.raises(InvalidPlannerOutputError, match="object"):
        parse_planner_output("[]")
