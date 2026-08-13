"""Run screenplay generation through PurrA's canonical tool loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from purra.api import AgentCoreRunOptions
from purra.artifacts import ArtifactStatus
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    RunBinding,
    ReasoningMode,
    RunProvenance,
    RunStatus,
)
from purra.errors import ModelGatewayError
from purra.model_protocol import resolve_invocation_output_limit
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)

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
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from infrastructure.screenplay import ScreenplayCandidateArtifacts


@dataclass(frozen=True, slots=True)
class ScreenplayCandidateRunResult:
    run_id: str
    candidate: Mapping[str, Any]


class ScreenplayToolCallingService:
    def __init__(self, db, *, composition) -> None:
        self._composition = composition
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
        output_limit = resolve_invocation_output_limit(
            model_request.capability_snapshot,
            explicit_user_override=model_request.options.get("max_tokens"),
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
            turn_id=conversation_turn_id,
            output_limit=output_limit,
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
            response_transaction_policy=ResponseTransactionPolicy(
                mode=ResponseTransactionMode.VALIDATED_RESULT,
                public_presentation=PublicPresentationMode.NONE,
            ),
        )
        options = self._composition.bind_run_profile(request, options)
        api_key = runtime.apiKey.get_secret_value()
        core = self._composition.create_core_for_request(
            request,
            api_key,
        )
        handle = None
        cancel_watcher: asyncio.Task[None] | None = None
        try:
            handle = await core.submit(request, options=options)
            if signal is not None and hasattr(signal, "wait"):
                cancel_watcher = asyncio.create_task(
                    _cancel_on_signal(signal, handle)
                )
            result = await handle.wait()
        finally:
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                await asyncio.gather(cancel_watcher, return_exceptions=True)
            self._composition.release_core(core)
        if handle is None:
            raise RuntimeError("screenplay tool Run returned no handle")
        run_id = handle.run_id
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

async def _cancel_on_signal(signal, handle) -> None:
    await signal.wait()
    await handle.cancel("screenplay_agent_canceled")


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
