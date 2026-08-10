"""Run screenplay generation through PurrA's canonical tool loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from purra.api import AgentCoreRunOptions
from purra.artifacts import ArtifactStatus
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    MessageOrigin,
    MessageRole,
    RunBinding,
    ReasoningMode,
    RunProvenance,
    RunStatus,
)
from purra.errors import ModelGatewayError
from purra.events import AgentEvent, CoreEventType
from purra.output_budget import OutputBudgetPolicy, resolve_output_budget

from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.request_mapping import context_window_tokens
from application.run_provenance import digest_model_endpoint
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from domains.screenplay_agent.candidate_projection import (
    candidate_completion_projection,
)
from application.screenplay_agent_stream import ScreenplayAgentChunkStore
from application.screenplay_progress_stream import visible_execution_progress
from application.sse_mapping import core_event_to_sse_chunk
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from infrastructure.screenplay import ScreenplayCandidateArtifacts


@dataclass(frozen=True, slots=True)
class ScreenplayCandidateRunResult:
    run_id: str
    candidate: Mapping[str, Any]


class ScreenplayToolCallingService:
    def __init__(self, db, *, composition) -> None:
        self._composition = composition
        self._chunks = ScreenplayAgentChunkStore(db)
        self._candidates = ScreenplayCandidateArtifacts(db)

    async def run_candidate(
        self,
        *,
        runtime,
        session_id: int,
        prompt: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        domain_context: ScreenplayAgentDomainContext,
        conversation_turn_id: str,
        output_policy: OutputBudgetPolicy,
        work_units: int = 1,
        reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT,
        validate_candidate: (
            Callable[[Mapping[str, Any]], Mapping[str, Any]] | None
        ) = None,
        host_candidate_template: Mapping[str, Any] | None = None,
        signal=None,
    ) -> ScreenplayCandidateRunResult:
        model_request = model_request_from_runtime(runtime)
        window = context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        )
        output_budget = resolve_output_budget(
            policy=output_policy,
            capabilities=model_request.output_capabilities,
            context_window_tokens=window,
            work_units=work_units,
            thinking_enabled=(
                reasoning_mode is not ReasoningMode.DISABLED
                and reasoning_mode_from_options(runtime.options).value != "disabled"
            ),
        )
        request = AgentRunRequest(
            messages=(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=system_instruction,
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            model=model_request,
            domain_context=domain_context.to_core_context(),
            session_id=session_id,
            mode="agent",
            context_window=window,
            tools_enabled=host_candidate_template is None,
            metadata={
                "locale": domain_context.locale,
                "screenplayPhase": domain_context.target_role,
            },
        )
        options = AgentCoreRunOptions(
            output_reserve_tokens=output_budget.effective_tokens,
            output_budget=output_budget,
            default_context_window_tokens=window,
            force_planned_tool_choice=False,
            require_tool_call=False,
            reasoning_mode=reasoning_mode,
            provenance=_provenance(runtime, user_payload, window),
            binding=RunBinding(
                namespace="screenplay.agent.task",
                aggregate_id=domain_context.project_id,
                command_id=f"{domain_context.task_id}:{domain_context.unit_id}",
                attributes=candidate_completion_projection(
                    scope=domain_context.to_core_context().payload,
                    host_candidate_template=host_candidate_template,
                ),
            ),
        )
        api_key = runtime.apiKey.get_secret_value()
        core = self._composition.create_core_for_request(
            request,
            api_key,
        )
        external_signal = signal or asyncio.Event()
        execution = self._composition.create_execution_session(external_signal)
        stream = core.run(request, options=options, signal=execution.signal)
        result: AgentRunResult | None = None
        run_id: str | None = None
        public_progress_seen = False
        host_progress = visible_execution_progress(
            (host_candidate_template or {}).get("processSummary")
        )
        host_progress_emitted = False
        try:
            async for update in stream:
                run_id = update.run_id or run_id
                if run_id:
                    await execution.bind(run_id)
                if isinstance(update, AgentEvent):
                    self._composition.observe_event(update)
                    chunk = _screenplay_chunk(
                        update,
                        domain_context,
                        include_candidate_progress=not public_progress_seen,
                    )
                    if chunk:
                        await self._chunks.append(
                            project_id=domain_context.project_id,
                            session_id=session_id,
                            turn_id=conversation_turn_id,
                            task_id=domain_context.task_id,
                            run_id=run_id,
                            chunk=chunk,
                        )
                        public_progress_seen = public_progress_seen or bool(
                            str(chunk.get("commentaryDelta") or "").strip()
                        )
                    if (
                        host_progress
                        and not host_progress_emitted
                        and update.type == CoreEventType.RUN_STARTED
                    ):
                        await self._chunks.append(
                            project_id=domain_context.project_id,
                            session_id=session_id,
                            turn_id=conversation_turn_id,
                            task_id=domain_context.task_id,
                            run_id=run_id,
                            chunk={"commentaryDelta": f"{host_progress}\n"},
                        )
                        host_progress_emitted = True
                        public_progress_seen = True
                else:
                    result = update
        finally:
            with suppress(Exception):
                await stream.aclose()
            if run_id:
                await self._composition.release_run(run_id)
            await execution.close()
        if result is None or run_id is None:
            raise RuntimeError("screenplay tool Run returned no terminal result")
        if result.status is RunStatus.CANCELED:
            raise asyncio.CancelledError
        if result.status is not RunStatus.DONE:
            code = str(result.error or "screenplay_tool_run_failed")
            failure = classify_screenplay_run_failure(code)
            raise ModelGatewayError(
                code,
                code=code,
                retryable=failure.retryable,
            )
        candidate = await self._candidates.load_run(run_id)
        artifact = candidate.get("_artifact")
        if getattr(artifact, "status", None) is not ArtifactStatus.FINALIZED:
            raise ModelGatewayError(
                "screenplay candidate was not committed with its Run",
                code="candidate_commit_missing",
                retryable=True,
            )
        public_candidate = {
            key: value for key, value in candidate.items()
            if not str(key).startswith("_")
        }
        if validate_candidate is not None:
            try:
                public_candidate = dict(validate_candidate(public_candidate))
            except (TypeError, ValueError) as error:
                raise ModelGatewayError(
                    str(error) or "candidate validation failed",
                    code="candidate_validation_failed",
                    retryable=True,
                ) from error
        candidate = public_candidate
        return ScreenplayCandidateRunResult(run_id=run_id, candidate=candidate)


def _screenplay_chunk(
    event: AgentEvent,
    context: ScreenplayAgentDomainContext,
    *,
    include_candidate_progress: bool = True,
) -> dict[str, Any] | None:
    # Candidate payload and the model's short terminal acknowledgement are not
    # conversation content. Commentary, reasoning diagnostics and tool events
    # remain canonical PurrA chunks.
    if event.type in {
        CoreEventType.ASSISTANT_FINAL_DELTA,
        CoreEventType.MODEL_CONTENT_DELTA,
    }:
        return None
    chunk = core_event_to_sse_chunk(event)
    if not chunk:
        return None
    completed = chunk.get("agentRunCompleted")
    if isinstance(completed, dict):
        # A checkpoint Run is internal execution progress, not the task's
        # formal answer. The task publishes one conclusion only after every
        # durable unit has completed.
        completed.pop("finalResponse", None)
    calls = chunk.get("toolCalls")
    if isinstance(calls, list):
        progress_items: list[str] = []
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if (
                isinstance(function, dict)
                and function.get("name") == "writeScreenplayCandidatePart"
            ):
                if include_candidate_progress:
                    progress = _candidate_execution_progress(
                        function.get("arguments")
                    )
                    if progress and progress not in progress_items:
                        progress_items.append(progress)
                function["arguments"] = json.dumps({
                    "partType": context.expected_part_type,
                    "partKey": context.expected_part_key,
                    "content": "<candidate payload omitted>",
                }, ensure_ascii=False, separators=(",", ":"))
        if progress_items:
            chunk["commentaryDelta"] = "\n".join(progress_items) + "\n"
    return chunk


def _candidate_execution_progress(arguments: object) -> str:
    try:
        decoded = json.loads(str(arguments or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(decoded, Mapping):
        return ""
    candidate = decoded.get("candidate")
    if not isinstance(candidate, Mapping):
        return ""
    for field in ("executionSummary", "processSummary"):
        progress = visible_execution_progress(candidate.get(field))
        if progress:
            return progress
    return ""


def _provenance(runtime, payload: Mapping[str, Any], window: int) -> RunProvenance:
    model = str(runtime.options.get("model") or "").strip()
    profile = json.dumps(
        {
            "provider": runtime.apiProvider,
            "model": model,
            "contextWindow": window,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = model_request_from_runtime(runtime)
    return RunProvenance(
        model_provider=str(runtime.apiProvider or "openai").strip().lower(),
        model_name=model,
        context_window=window,
        endpoint_digest=digest_model_endpoint(runtime.baseURL),
        request_profile_digest=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
        capability_snapshot=request.capability_snapshot.to_mapping(
            include_digest=True
        ),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode_from_options(runtime.options),
            output_contract="screenplay_candidate_artifact",
            tool_protocol_contract="screenplay_host_tools",
        ),
    )


__all__ = ["ScreenplayCandidateRunResult", "ScreenplayToolCallingService"]
