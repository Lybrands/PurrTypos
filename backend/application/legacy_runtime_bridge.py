"""Application bridge between Core Runtime updates and the existing SSE API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Mapping, Sequence

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    DomainContext,
    ExecutionState,
    ModelRequest,
    RuntimeOutcome,
    ToolSchema,
    TraceRecord,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.json_values import thaw_json_mapping
from agent_core.runtime import AgentRuntime
from application.legacy_tool_execution_gateway import LegacyToolExecutionGateway
from infrastructure.models.legacy_model_gateway import LegacyModelGateway

if TYPE_CHECKING:
    from agent_core.ports import CancellationSignal
    from services.agent_run_controller import AgentRunController


class LegacyRunObserver:
    """Expose only the todo/trace callbacks needed during stage 3."""

    def __init__(self, controller: "AgentRunController"):
        self._controller = controller

    def current_allowed_tool_names(self) -> frozenset[str]:
        return frozenset(
            self._controller.allowed_tool_names_for_current_transition()
        )

    async def record_trace(self, trace: TraceRecord) -> None:
        await self._controller.record_trace(
            trace.stage,
            trace.outcome,
            details=thaw_json_mapping(trace.details),
            duration_ms=trace.duration_ms,
        )

    async def on_model_delta(self) -> None:
        await self._controller.on_model_delta()

    async def on_tool_calls_started(self, tool_names: tuple[str, ...]) -> None:
        await self._controller.on_tool_calls_started(list(tool_names))

    async def on_tool_round_completed(self) -> None:
        await self._controller.on_tool_round_completed()


async def stream_core_runtime_as_legacy_chunks(
    *,
    api_key: str,
    api_provider: str,
    model: str,
    request_options: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    tool_definitions: Sequence[Mapping[str, Any]],
    executable_tools: bool,
    tool_execution_context: dict[str, Any],
    agent_run: "AgentRunController | None",
    agent_run_events: list[dict[str, Any]],
    session_id: str | int | None,
    mode: str | None,
    round_input_tokens: int,
    force_tool_choice: bool,
    on_required_tool_choice_unsupported: Callable[[], None] | None = None,
    signal: "CancellationSignal | None" = None,
) -> AsyncIterator[dict[str, Any]]:
    schemas = _tool_schemas(tool_definitions)
    options = dict(request_options)
    options.pop("tools", None)
    options.pop("tool_choice", None)
    request = AgentRunRequest(
        messages=tuple(AgentMessage.from_mapping(message) for message in messages),
        model=ModelRequest(
            provider=api_provider,
            model=model,
            options=options,
        ),
        domain_context=DomainContext(namespace="application.prepared"),
        session_id=session_id,
        mode=mode,
        tools_enabled=bool(schemas),
    )
    observer = LegacyRunObserver(agent_run) if agent_run is not None else None
    runtime = AgentRuntime(
        model_gateway=LegacyModelGateway(
            api_key,
            on_required_tool_choice_unsupported=(
                on_required_tool_choice_unsupported
            ),
        ),
        tool_execution_gateway=(
            LegacyToolExecutionGateway() if executable_tools else None
        ),
        observer=observer,
    )
    manage_plan_scope = bool(agent_run is not None and executable_tools)

    result: AgentRuntimeResult | None = None
    async for update in runtime.run(
        request,
        tools=schemas,
        execution_state=ExecutionState(domain=tool_execution_context),
        run_id=(agent_run.run_id if agent_run is not None else None),
        round_input_tokens=round_input_tokens,
        scope_tools_to_observer=manage_plan_scope,
        force_tool_choice=bool(manage_plan_scope and force_tool_choice),
        tools_executable=executable_tools,
        signal=signal,
    ):
        if isinstance(update, AgentEvent):
            for pending in _drain(agent_run_events):
                yield pending
            chunk = core_event_to_legacy_chunk(update)
            if chunk is not None:
                yield chunk
            continue
        result = update

    for pending in _drain(agent_run_events):
        yield pending
    if result is None:
        result = AgentRuntimeResult(
            run_id=(agent_run.run_id if agent_run is not None else None),
            outcome=RuntimeOutcome.FAILED,
            final_response="",
            model=model,
            round_count=0,
            error_code="runtime_returned_no_result",
        )

    if result.outcome is RuntimeOutcome.COMPLETED:
        if agent_run is not None:
            await agent_run.complete(final_response=result.final_response)
            for pending in _drain(agent_run_events):
                yield pending
        yield {"done": True, "model": result.model or model}
        return

    if result.outcome is RuntimeOutcome.CANCELED:
        if agent_run is not None:
            await agent_run.cancel(reason=result.error_code or "request_canceled")
            if result.error_code != "request_canceled":
                for pending in _drain(agent_run_events):
                    yield pending
            else:
                agent_run_events.clear()
        return

    user_message = _runtime_error_message(result.error_code)
    if agent_run is not None:
        await agent_run.fail(error=user_message)
        for pending in _drain(agent_run_events):
            yield pending
    yield {"error": user_message}


def core_event_to_legacy_chunk(event: AgentEvent) -> dict[str, Any] | None:
    payload = thaw_json_mapping(event.payload)
    if event.type == CoreEventType.MODEL_DELTA:
        return {"delta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.MODEL_THINKING_DELTA:
        return {"thinkingDelta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.TOOL_CALLS_STARTED:
        return {
            "toolCalls": [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": call.get("arguments_json", ""),
                    },
                }
                for call in payload.get("calls", [])
                if isinstance(call, Mapping)
            ],
            "toolCallsInProgress": bool(payload.get("in_progress")),
            "partialContent": str(payload.get("partial_content") or ""),
            "partialThinking": str(payload.get("partial_thinking") or ""),
            "model": payload.get("model"),
        }
    if event.type == CoreEventType.TOOL_RESULTS:
        return {
            "toolResults": [
                {
                    "tool_call_id": item.get("tool_call_id"),
                    "name": item.get("tool_name"),
                    "content": item.get("content", ""),
                }
                for item in payload.get("results", [])
                if isinstance(item, Mapping)
            ],
        }
    if event.type == CoreEventType.TOOL_CALL_COMPLETED:
        chunk = {"toolIndexCompleted": int(payload.get("index") or 0)}
        if payload.get("from_cache"):
            chunk["toolFromCache"] = True
        return chunk
    if event.type == CoreEventType.APPROVAL_REQUESTED:
        return {"toolApprovalRequired": payload}
    if event.type == "tool.read_cache_mask":
        return {"toolReadCacheMask": list(payload.get("mask") or [])}
    if event.type == "writing.proposed_chapter_diff":
        return {"proposedChapterDiff": payload}
    if event.type == "tool.progress":
        return payload
    if event.type == CoreEventType.TOOL_ROUND_COMPLETED:
        return None
    return {event.type: payload}


def _tool_schemas(
    definitions: Sequence[Mapping[str, Any]],
) -> tuple[ToolSchema, ...]:
    schemas: list[ToolSchema] = []
    for definition in definitions:
        function = definition.get("function")
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        parameters = function.get("parameters")
        schemas.append(ToolSchema(
            name=name,
            description=str(function.get("description") or ""),
            parameters=(dict(parameters) if isinstance(parameters, Mapping) else {}),
        ))
    return tuple(schemas)


def _drain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drained = list(events)
    events.clear()
    return drained


def _runtime_error_message(error_code: str | None) -> str:
    return {
        "context_overflow_after_tool": (
            "工具结果超过了剩余上下文窗口；宿主保留了最近的完整工具回合，"
            "并已停止继续执行。"
        ),
        "missing_required_tool_call": (
            "当前计划步骤必须调用工具，但模型没有返回结构化工具调用。"
            "本轮已停止，未把模型生成的伪调用文本展示或执行。"
        ),
        "max_model_rounds": (
            "Agent 已达到最大工具轮次；为避免执行一个无法再交给模型消费的"
            "工具结果，本轮未继续执行。"
        ),
        "tool_not_authorized": "模型请求了当前计划未授权的工具，Agent 已停止。",
        "approval_unavailable": "工具批准请求超时或当前不可用，未执行操作。",
        "tool_execution_failed": "工具执行失败，Agent 已停止；可在运行诊断中查看失败阶段。",
        "upstream_stream_interrupted": "模型服务流式响应中断，请检查网络或稍后重试。",
    }.get(
        str(error_code or ""),
        "Agent 运行过程中发生异常，已安全停止；请稍后重试。",
    )
