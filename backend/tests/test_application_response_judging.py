from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ReasoningMode,
    ResponseValidationResult,
    ToolCall,
    ToolChoiceMode,
)
from purra.errors import ModelGatewayError
from purra.api import AgentModelResponseJudge, AgentModelTaskRunner
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.model_protocol import generic_capability_snapshot


def _model_tasks(
    gateway,
    model_request: ModelRequest,
) -> AgentModelTaskRunner:
    return AgentModelTaskRunner(
        AgentModelInvocationManager(gateway),
        ModelInvocationContext(run_id="judge-test-run"),
        model_request,
    )


class _Gateway:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, invocation, signal=None):
        raise AssertionError("semantic judge must not stream")

    async def complete(self, messages, invocation, signal=None):
        self.calls.append((tuple(messages), invocation, signal))
        return ModelCompletion(
            applied_generation_limit=invocation.max_generation_tokens,
            message=AgentMessage(role="assistant", content='{"ok":true}'),
            model="judge-model",
            finish_reason=ModelFinishReason.STOP,
        )


class _Policy:
    def __init__(self):
        self.evaluated = []

    def build_messages(self, *, content, messages):
        assert content == "candidate"
        assert tuple(messages) == (AgentMessage(role="user", content="request"),)
        return (
            AgentMessage(role="system", content="judge"),
            AgentMessage(role="user", content='{"candidate":"data"}'),
        )

    def evaluate(self, *, judgment_content, candidate_content):
        self.evaluated.append((judgment_content, candidate_content))
        return ResponseValidationResult()


@pytest.mark.asyncio
async def test_model_backed_judge_disables_tools_without_rewriting_model_options():
    gateway = _Gateway()
    policy = _Policy()
    signal = asyncio.Event()
    model_request = ModelRequest(
        provider="fixture",
        model="writer-model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            profile_id="fixture:writer-model",
            max_generation_tokens=4_096,
        ),
        options={
            "temperature": 0.8,
            "tools": [{"name": "unsafe"}],
            "tool_choice": "required",
            "top_p": 0.4,
            "top_k": 99,
            "seed": 7,
            "functions": [{"name": "legacy_unsafe"}],
            "function_call": "auto",
            "parallel_tool_calls": True,
            "response_format": {"type": "text"},
            "baseURL": "https://provider.test/v1",
        },
    )
    judge = AgentModelResponseJudge(
        model_tasks=_model_tasks(gateway, model_request),
        model_request=model_request,
        policy=policy,
    )

    result = await judge.judge(
        content="candidate",
        messages=(AgentMessage(role="user", content="request"),),
        signal=signal,  # type: ignore[arg-type]
    )

    assert result.accepted is True
    assert len(gateway.calls) == 1
    messages, invocation, observed_signal = gateway.calls[0]
    assert [message.role.value for message in messages] == ["system", "user"]
    assert invocation.tools == ()
    assert invocation.tool_choice is ToolChoiceMode.NONE
    assert invocation.reasoning_mode is ReasoningMode.DEFAULT
    assert invocation.max_generation_tokens == 4_096
    assert invocation.request.options["temperature"] == 0.8
    assert invocation.request.options["baseURL"] == "https://provider.test/v1"
    assert invocation.request.profile_id == "fixture:writer-model"
    assert invocation.request.options["tools"] == ({"name": "unsafe"},)
    assert invocation.request.options["tool_choice"] == "required"
    assert invocation.request.options["top_p"] == 0.4
    assert invocation.request.options["top_k"] == 99
    assert invocation.request.options["seed"] == 7
    assert invocation.request.options["functions"] == ({"name": "legacy_unsafe"},)
    assert invocation.request.options["function_call"] == "auto"
    assert invocation.request.options["parallel_tool_calls"] is True
    assert invocation.request.options["response_format"] == {"type": "text"}
    assert observed_signal is not signal
    assert not observed_signal.is_set()
    signal.set()
    assert observed_signal.is_set()
    assert policy.evaluated == [('{"ok":true}', "candidate")]


@pytest.mark.asyncio
async def test_model_backed_judge_fails_closed_on_an_unexpected_tool_call():
    class _ToolCallingGateway(_Gateway):
        async def complete(self, messages, invocation, signal=None):
            return ModelCompletion(
                applied_generation_limit=invocation.max_generation_tokens,
                message=AgentMessage(
                    role="assistant",
                    content='{"ok":true}',
                    tool_calls=(ToolCall(
                        id="unexpected",
                        name="not-exposed",
                        arguments_json="{}",
                    ),),
                ),
                model="judge-model",
                finish_reason=ModelFinishReason.STOP,
            )

    model_request = ModelRequest(
        provider="fixture",
        model="model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            max_generation_tokens=4_096,
        ),
    )
    with pytest.raises(ModelGatewayError) as captured:
        await AgentModelResponseJudge(
            model_tasks=_model_tasks(_ToolCallingGateway(), model_request),
            model_request=model_request,
            policy=_Policy(),
        ).judge(
            content="candidate",
            messages=(AgentMessage(role="user", content="request"),),
        )
    assert captured.value.code == "unexpected_model_tool_calls"
