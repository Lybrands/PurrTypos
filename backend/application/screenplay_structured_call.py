"""Screenplay structured-output adaptation over managed PurrA calls.

The screenplay product owns prompts, schemas, validation and visible progress.
PurrA owns provider invocation construction, output-budget resolution,
capability fallback and terminal-reason classification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Awaitable, Callable

from purra.contracts import (
    AgentMessage,
    MessageOrigin,
    MessageRole,
    ReasoningMode,
    RunBinding,
    RunCreateParams,
    RunProvenance,
)
from purra.events import AgentEvent, CoreEventType
from purra.model_execution import (
    ManagedModelCall,
    ManagedModelExecutor,
    ManagedModelStream,
)
from purra.output_budget import OutputBudgetPolicy
from purra.run_controller import AgentRunController
from purra.structured_output import parse_json_object
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.request_mapping import context_window_tokens
from application.run_execution_control import RunExecutionSession
from application.run_provenance import digest_model_endpoint
from application.screenplay_agent_stream import ScreenplayAgentChunkStore
from application.screenplay_progress_stream import (
    JsonStringFieldProjector,
    VISIBLE_STREAM_CHUNK_CHARS,
    visible_execution_progress,
    visible_stream_chunks,
)
from infrastructure.persistence.run_execution_store import SqliteExecutionLeaseStore
from infrastructure.persistence.sqlite_run_repository import (
    DEFAULT_RUN_LEASE_DURATION_MS,
    SqliteRunRepository,
)


class _Sink:
    async def emit(self, event: AgentEvent) -> None:
        del event


@dataclass(frozen=True, slots=True)
class StructuredModelResult:
    value: dict[str, Any]
    run_id: str


@dataclass(frozen=True, slots=True)
class StreamedModelText:
    content: str
    projected_progress: bool = False


class ScreenplayStructuredCallService:
    def __init__(
        self,
        db,
        *,
        model_executor_factory: Callable[[str], ManagedModelExecutor],
        lease_duration_ms: int = DEFAULT_RUN_LEASE_DURATION_MS,
    ) -> None:
        self._db = db
        self._model_executor_factory = model_executor_factory
        self._repository = SqliteRunRepository(
            db,
            lease_duration_ms=lease_duration_ms,
        )
        self._execution_leases = SqliteExecutionLeaseStore(db)

    async def run_json(
        self,
        *,
        runtime,
        session_id: int,
        prompt: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        binding_namespace: str,
        binding_aggregate_id: str,
        binding_command_id: str,
        conversation_turn_id: str | None = None,
        task_id: str | None = None,
        phase: str,
        output_policy: OutputBudgetPolicy,
        repair_instruction: str,
        work_units: int = 1,
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        execution_progress_fields: Mapping[str, str] | None = None,
        project_execution: (
            Callable[[dict[str, Any]], Sequence[str]] | None
        ) = None,
        signal=None,
    ) -> StructuredModelResult:
        request = model_request_from_runtime(
            runtime,
            json_object_output=True,
        )
        model = request.model
        managed_call = ManagedModelCall(
            request=request,
            output_policy=output_policy,
            context_window_tokens=context_window_tokens(
                runtime.contextWindow or runtime.options.get("context_window")
            ),
            work_units=work_units,
            reasoning_mode=reasoning_mode_from_options(runtime.options),
        )
        model_executor = self._model_executor_factory(
            runtime.apiKey.get_secret_value()
        )
        controller = AgentRunController(
            repository=self._repository,
            event_sink=_Sink(),
        )
        await controller.begin(RunCreateParams(
            session_id=session_id,
            prompt=prompt,
            mode=phase,
            provenance=_provenance(runtime, user_payload),
            binding=RunBinding(
                namespace=binding_namespace,
                aggregate_id=binding_aggregate_id,
                command_id=binding_command_id,
            ),
        ))
        if controller.run_id is None:
            raise RuntimeError("structured model Run has no id")
        turn_id = str(conversation_turn_id or binding_command_id or "").strip()
        if not turn_id:
            raise ValueError("screenplay structured call requires a Turn id")
        chunks = ScreenplayAgentChunkStore(self._db)

        async def emit_chunk(chunk: Mapping[str, Any]) -> None:
            await chunks.append(
                project_id=binding_aggregate_id,
                session_id=session_id,
                turn_id=turn_id,
                task_id=str(task_id or "").strip() or None,
                run_id=controller.run_id,
                chunk=chunk,
            )

        async def emit_public_text(field: str, value: object) -> None:
            for fragment in visible_stream_chunks(value):
                await emit_chunk({field: fragment})

        execution = RunExecutionSession(
            self._execution_leases,
            owner_id=self._repository.owner_id,
            lease_duration_ms=self._repository.lease_duration_ms,
            external_signal=signal or asyncio.Event(),
        )
        try:
            await execution.bind(controller.run_id)
            await emit_chunk({"model": model})
            messages = (
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=system_instruction,
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content=json.dumps(user_payload, ensure_ascii=False),
                ),
            )
            previous = ""
            projected_execution = False
            for attempt in range(2):
                active = messages if attempt == 0 else (
                    AgentMessage(
                        role=MessageRole.SYSTEM,
                        content=(
                            f"{system_instruction}\n\n{repair_instruction}\n"
                            "只修复下面候选的 JSON 语法和协议字段，不重新分析或扩写"
                            "业务内容；只返回修复后的完整 JSON 对象。"
                        ),
                        origin=MessageOrigin.HOST_CONTEXT,
                    ),
                    AgentMessage(role=MessageRole.USER, content=previous),
                )
                attempt_count = 0

                async def record_attempt(
                    parameters: Mapping[str, object],
                ) -> None:
                    nonlocal attempt_count
                    attempt_count += 1
                    call_phase = phase if attempt == 0 else f"{phase}_repair"
                    if attempt_count > 1:
                        call_phase = f"{call_phase}_reasoning_fallback"
                    await controller.record_event(
                        CoreEventType.MODEL_CALL_RECORDED,
                        {
                            "phase": call_phase,
                            "count": 1,
                            "toolNames": [],
                            "toolChoice": "none",
                            "parameters": dict(parameters),
                        },
                    )

                managed_stream = await model_executor.stream(
                    active,
                    (
                        managed_call
                        if attempt == 0
                        else replace(
                            managed_call,
                            reasoning_mode=ReasoningMode.DISABLED,
                            allow_reasoning_fallback=False,
                        )
                    ),
                    execution.signal,
                    on_attempt=record_attempt,
                )
                streamed = await _stream_text(
                    managed_stream,
                    controller,
                    execution_progress_fields=(
                        execution_progress_fields
                        if not projected_execution
                        else None
                    ),
                    emit_execution_progress=lambda delta: emit_public_text(
                        "commentaryDelta",
                        delta,
                    ),
                    emit_model_diagnostic=emit_chunk,
                )
                projected_execution = (
                    projected_execution or streamed.projected_progress
                )
                previous = streamed.content
                last_error: Exception | None = None
                for candidate in (streamed.content,):
                    if not candidate.strip():
                        continue
                    try:
                        value = dict(parse_json_object(candidate))
                        if validate is not None:
                            value = validate(value)
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        last_error = error
                        continue
                    reply = (
                        str(value.get("reply") or "").strip()
                        if phase == "screenplay_intent_planning"
                        else ""
                    )
                    if project_execution is not None and not projected_execution:
                        for item in project_execution(value):
                            progress = visible_execution_progress(item)
                            if progress:
                                await emit_public_text(
                                    "commentaryDelta",
                                    f"{progress}\n",
                                )
                    if reply:
                        await emit_public_text("delta", reply)
                    await controller.complete(reply)
                    return StructuredModelResult(value, controller.run_id)
                if attempt == 1:
                    raise last_error or ValueError(
                        "model output does not contain a complete JSON object"
                    )
            raise RuntimeError("structured model repair loop ended unexpectedly")
        except asyncio.CancelledError:
            snapshot = controller.snapshot
            if snapshot is not None and not snapshot.terminal:
                with suppress(Exception):
                    await controller.cancel("screenplay_agent_canceled")
            raise
        except Exception as error:
            snapshot = controller.snapshot
            if snapshot is not None and not snapshot.terminal:
                with suppress(Exception):
                    await controller.fail(f"{phase}_failed")
            with suppress(Exception):
                await emit_chunk({"error": str(error) or "剧本模型调用失败。"})
            raise
        finally:
            await execution.close()

async def _stream_text(
    stream: ManagedModelStream,
    controller: AgentRunController,
    *,
    execution_progress_fields: Mapping[str, str] | None = None,
    emit_execution_progress: Callable[[str], Awaitable[None]] | None = None,
    emit_model_diagnostic: (
        Callable[[Mapping[str, Any]], Awaitable[None]] | None
    ) = None,
) -> StreamedModelText:
    chunks = stream.chunks
    parts: list[str] = []
    progress_projector = JsonStringFieldProjector(
        execution_progress_fields or {}
    )
    progress_buffer = ""
    projected_progress = False
    last_progress_flush = monotonic()

    async def flush_progress() -> None:
        nonlocal progress_buffer
        nonlocal last_progress_flush
        if not progress_buffer or emit_execution_progress is None:
            return
        pending = progress_buffer
        progress_buffer = ""
        for fragment in visible_stream_chunks(pending):
            await emit_execution_progress(fragment)
        last_progress_flush = monotonic()

    async def emit_progress(projected: str) -> None:
        nonlocal progress_buffer
        nonlocal projected_progress
        if not projected:
            return
        projected_progress = True
        progress_buffer += projected
        now = monotonic()
        if (
            "\n" in projected
            or len(progress_buffer) >= VISIBLE_STREAM_CHUNK_CHARS
            or now - last_progress_flush >= 0.04
        ):
            await flush_progress()

    async def project_structured(delta: str) -> None:
        if not execution_progress_fields or emit_execution_progress is None:
            return
        await emit_progress(progress_projector.feed(delta))

    try:
        async for chunk in chunks:
            if chunk.reasoning_delta:
                if emit_model_diagnostic is not None:
                    await emit_model_diagnostic({
                        "reasoningDelta": chunk.reasoning_delta,
                    })
            if chunk.content_delta:
                parts.append(chunk.content_delta)
                if emit_model_diagnostic is not None:
                    await emit_model_diagnostic({
                        "modelContentDelta": chunk.content_delta,
                    })
                await project_structured(chunk.content_delta)
            if chunk.usage is not None:
                await controller.record_event(
                    CoreEventType.CONTEXT_USAGE_RECORDED,
                    {
                        "inputTokens": chunk.usage.input_tokens,
                        "outputTokens": chunk.usage.output_tokens,
                        "totalTokens": chunk.usage.total_tokens,
                        "reasoningOutputTokens": (
                            chunk.usage.reasoning_output_tokens
                        ),
                    },
                )
    finally:
        await flush_progress()
        close = getattr(chunks, "aclose", None)
        if callable(close):
            await close()
    return StreamedModelText(
        content="".join(parts),
        projected_progress=projected_progress,
    )


def _provenance(runtime, payload: Mapping[str, Any]) -> RunProvenance:
    model = str(runtime.options.get("model") or "").strip()
    profile = json.dumps(
        {
            "provider": runtime.apiProvider,
            "model": model,
            "contextWindow": runtime.contextWindow,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = model_request_from_runtime(runtime, json_object_output=True)
    return RunProvenance(
        model_provider=str(runtime.apiProvider or "openai").strip().lower(),
        model_name=model,
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        endpoint_digest=digest_model_endpoint(runtime.baseURL),
        request_profile_digest=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode_from_options(runtime.options),
            output_contract="json_object",
            tool_protocol_contract="no_tools",
        ),
    )


__all__ = [
    "ScreenplayStructuredCallService",
    "StreamedModelText",
    "StructuredModelResult",
]
