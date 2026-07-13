from __future__ import annotations

import json

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    DomainContext,
    ExecutionState,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    RuntimeOutcome,
    ToolCallDelta,
    ToolSchema,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.runtime import AgentRuntime
from application.legacy_tool_execution_gateway import LegacyToolExecutionGateway
from services.tool_approval_service import resolve_tool_approval
from services.tool_executor import TOOL_HANDLERS, ToolResult


class _ModelGateway:
    def __init__(self):
        self.round = 0

    async def stream(self, messages, invocation, signal=None):
        self.round += 1

        async def _chunks():
            if self.round == 1:
                yield ModelStreamChunk(
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id="call-delete",
                        type="function",
                        name="deleteCharacter",
                        arguments_fragment='{"characterId":7}',
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )
            else:
                yield ModelStreamChunk(
                    content_delta="finished",
                    finish_reason=ModelFinishReason.STOP,
                )

        return ModelStream(chunks=_chunks(), model="model")

    async def complete(self, messages, invocation, signal=None):
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="unused"),
            model="model",
        )


class _Observer:
    def __init__(self):
        self.tool_done = False

    def current_allowed_tool_names(self):
        return frozenset() if self.tool_done else frozenset({"deleteCharacter"})

    async def record_trace(self, trace):
        pass

    async def on_model_delta(self):
        pass

    async def on_tool_calls_started(self, tool_names):
        pass

    async def on_tool_round_completed(self):
        self.tool_done = True


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True], ids=["rejected", "approved"])
async def test_core_runtime_streams_real_approval_before_executor_resumes(approved):
    handler_calls: list[dict] = []
    original_handler = TOOL_HANDLERS["deleteCharacter"]

    async def _handler(ctx, args, send_chunk):
        handler_calls.append(args)
        return ToolResult(json.dumps({"success": True}, ensure_ascii=False))

    TOOL_HANDLERS["deleteCharacter"] = _handler
    state = ExecutionState(domain={})
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="delete duplicate"),),
        model=ModelRequest(provider="openai", model="model"),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )
    runtime = AgentRuntime(
        model_gateway=_ModelGateway(),
        tool_execution_gateway=LegacyToolExecutionGateway(),
        observer=_Observer(),
    )
    updates = []
    try:
        async for update in runtime.run(
            request,
            tools=(ToolSchema(
                name="deleteCharacter",
                description="Delete character",
                parameters={"type": "object", "properties": {}},
            ),),
            execution_state=state,
            scope_tools_to_observer=True,
            force_tool_choice=True,
        ):
            updates.append(update)
            if (
                isinstance(update, AgentEvent)
                and update.type == CoreEventType.APPROVAL_REQUESTED
            ):
                approval_id = str(update.payload["approvalId"])
                assert resolve_tool_approval(approval_id, approved) is not None
    finally:
        TOOL_HANDLERS["deleteCharacter"] = original_handler

    result = updates[-1]
    assert isinstance(result, AgentRuntimeResult)
    assert result.outcome is RuntimeOutcome.COMPLETED
    assert result.final_response == "finished"
    assert handler_calls == ([{"characterId": 7}] if approved else [])
    assert "allowedToolNames" not in state.domain
    event_types = [
        update.type for update in updates if isinstance(update, AgentEvent)
    ]
    assert event_types.index(CoreEventType.APPROVAL_REQUESTED) < event_types.index(
        CoreEventType.TOOL_CALL_COMPLETED
    )
    assert CoreEventType.TOOL_RESULTS in event_types
