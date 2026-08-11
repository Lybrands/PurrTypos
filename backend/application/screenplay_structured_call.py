"""Screenplay structured-output adaptation over managed PurrA calls.

The screenplay product owns prompts, schemas, validation and visible progress.
PurrA owns provider invocation construction, output-limit resolution,
capability fallback and terminal-reason classification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable

from purra.contracts import (
    AgentMessage,
    MessageOrigin,
    MessageRole,
    ModelStreamChunk,
    RunBinding,
    RunCreateParams,
    RunProvenance,
)
from purra.events import AgentEvent, CoreEventType
from purra.errors import ModelGatewayError
from purra.model_execution import (
    ManagedModelCall,
    ManagedModelExecutor,
)
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
from domains.screenplay_agent import ScreenplayIntentCommandMismatchError
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


class StructuredChunkProjection:
    """Project diagnostics and selected public fields without owning output."""

    def __init__(
        self,
        controller: AgentRunController,
        *,
        execution_progress_fields: Mapping[str, str] | None = None,
        emit_execution_progress: Callable[[str], Awaitable[None]] | None = None,
        emit_model_diagnostic: (
            Callable[[Mapping[str, Any]], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._controller = controller
        self._execution_progress_fields = execution_progress_fields
        self._emit_execution_progress = emit_execution_progress
        self._emit_model_diagnostic = emit_model_diagnostic
        self._progress_projector = JsonStringFieldProjector(
            execution_progress_fields or {}
        )
        self._progress_buffer = ""
        self._last_progress_flush = monotonic()
        self.projected_progress = False

    async def observe(self, chunk: ModelStreamChunk) -> None:
        if chunk.reasoning_delta and self._emit_model_diagnostic is not None:
            await self._emit_model_diagnostic({
                "reasoningDelta": chunk.reasoning_delta,
            })
        if chunk.content_delta:
            if self._emit_model_diagnostic is not None:
                await self._emit_model_diagnostic({
                    "modelContentDelta": chunk.content_delta,
                })
            if (
                self._execution_progress_fields
                and self._emit_execution_progress is not None
            ):
                await self._emit_progress(
                    self._progress_projector.feed(chunk.content_delta)
                )
        if chunk.usage is not None:
            await self._controller.record_event(
                CoreEventType.CONTEXT_USAGE_RECORDED,
                {
                    "inputTokens": chunk.usage.input_tokens,
                    "outputTokens": chunk.usage.output_tokens,
                    "totalTokens": chunk.usage.total_tokens,
                    "reasoningOutputTokens": chunk.usage.reasoning_output_tokens,
                },
            )

    async def close(self) -> None:
        await self._flush_progress()

    async def _emit_progress(self, projected: str) -> None:
        if not projected:
            return
        self.projected_progress = True
        self._progress_buffer += projected
        now = monotonic()
        if (
            "\n" in projected
            or len(self._progress_buffer) >= VISIBLE_STREAM_CHUNK_CHARS
            or now - self._last_progress_flush >= 0.04
        ):
            await self._flush_progress()

    async def _flush_progress(self) -> None:
        if (
            not self._progress_buffer
            or self._emit_execution_progress is None
        ):
            return
        pending = self._progress_buffer
        self._progress_buffer = ""
        for fragment in visible_stream_chunks(pending):
            await self._emit_execution_progress(fragment)
        self._last_progress_flush = monotonic()


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
        repair_instruction: str,
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
            projected_execution = False

            async def call(
                active_messages: Sequence[AgentMessage],
                call_phase: str,
            ) -> str:
                nonlocal projected_execution

                async def record_attempt(
                    parameters: Mapping[str, object],
                ) -> None:
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

                projection = StructuredChunkProjection(
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
                try:
                    result = await model_executor.stream_text(
                        active_messages,
                        managed_call,
                        execution.signal,
                        on_attempt=record_attempt,
                        on_chunk=projection.observe,
                    )
                    return result.content
                finally:
                    await projection.close()
                    projected_execution = (
                        projected_execution or projection.projected_progress
                    )

            def parse(candidate: str) -> dict[str, Any]:
                value = dict(parse_json_object(candidate))
                return validate(value) if validate is not None else value

            candidate = await call(messages, phase)
            if not candidate.strip():
                raise RuntimeError("Core returned an empty structured candidate")
            try:
                value = parse(candidate)
            except (TypeError, ValueError, json.JSONDecodeError) as first_error:
                repaired = await call((
                    AgentMessage(
                        role=MessageRole.SYSTEM,
                        content=(
                            f"{system_instruction}\n\n{repair_instruction}\n"
                            "只修复下面候选的 JSON 语法和协议字段，不重新分析或扩写"
                            "业务内容；只返回修复后的完整 JSON 对象。"
                        ),
                        origin=MessageOrigin.HOST_CONTEXT,
                    ),
                    AgentMessage(role=MessageRole.USER, content=candidate),
                ), f"{phase}_repair")
                try:
                    value = parse(repaired)
                except ScreenplayIntentCommandMismatchError:
                    raise
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise ModelGatewayError(
                        str(error) or str(first_error),
                        code="structured_output_invalid",
                        retryable=False,
                    ) from error

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
        capability_snapshot=request.capability_snapshot.to_mapping(
            include_digest=True
        ),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode_from_options(runtime.options),
            output_contract="json_object",
            tool_protocol_contract="no_tools",
        ),
    )


__all__ = [
    "ScreenplayStructuredCallService",
    "StructuredChunkProjection",
    "StructuredModelResult",
]
