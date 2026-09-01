"""Run screenplay Durable units through PurrA's canonical tool loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
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
    PlanningMode,
    RunBinding,
    RunProvenance,
    RunStatus,
)
from purra.errors import ModelGatewayError
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)

from application.agent_run_service import AgentRunService
from application.durable_agent_run import run_durable_agent_unit
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
    runtime_context_window_tokens,
)
from application.run_provenance import digest_model_endpoint
from application.screenplay_model_policy import screenplay_output_limit
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
    candidate_completion_projection,
    parse_candidate_validation_contract,
)
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from infrastructure.screenplay import ScreenplayCandidateArtifacts


BindRun = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ScreenplayCandidateRunResult:
    run_id: str
    candidate: Mapping[str, Any]


class ScreenplayToolCallingService:
    """Execute one real, persisted screenplay model/tool Run per AI unit."""

    def __init__(self, db, *, composition) -> None:
        self._db = db
        self._runs = AgentRunService(composition)
        self._candidates = ScreenplayCandidateArtifacts(db)

    async def run_candidate(
        self,
        *,
        runtime,
        session_id: int,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        domain_context: ScreenplayAgentDomainContext,
        conversation_turn_id: str,
        output_token_cap: int,
        bind_run: BindRun | None = None,
        candidate_validation_contract: Mapping[str, Any] | None = None,
        host_candidate_template: Mapping[str, Any] | None = None,
        signal=None,
    ) -> ScreenplayCandidateRunResult:
        validation_contract = parse_candidate_validation_contract(
            candidate_validation_contract
            if candidate_validation_contract is not None
            else {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": "generic",
            }
        )
        model_request = model_request_from_runtime(runtime)
        window = runtime_context_window_tokens(runtime)
        output_limit = screenplay_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
            part_cap=output_token_cap,
        )
        bound_context = ScreenplayAgentDomainContext(
            project_id=domain_context.project_id,
            task_id=domain_context.task_id,
            unit_id=domain_context.unit_id,
            target_role=domain_context.target_role,
            expected_part_type=domain_context.expected_part_type,
            expected_part_key=domain_context.expected_part_key,
            candidate_validation_contract=validation_contract,
            dependency_part_keys=domain_context.dependency_part_keys,
            deliverable_revision_scope=domain_context.deliverable_revision_scope,
            episode_number=domain_context.episode_number,
            tool_access=domain_context.tool_access,
            source_book_id=domain_context.source_book_id,
            source_scope=domain_context.source_scope,
            locale=domain_context.locale,
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
            domain_context=bound_context.to_core_context(),
            session_id=session_id,
            mode="screenplay_durable_unit",
            context_window=window,
            tools_enabled=True,
            planning_mode=PlanningMode.REACTIVE,
            metadata={"locale": domain_context.locale},
        )
        command_id = f"{domain_context.task_id}:{domain_context.unit_id}"
        options = AgentCoreRunOptions(
            turn_id=conversation_turn_id,
            output_limit=output_limit,
            default_context_window_tokens=window,
            force_planned_tool_choice=False,
            require_tool_call=True,
            reasoning_mode=reasoning_mode_from_options(runtime.options),
            provenance=screenplay_run_provenance(
                runtime,
                user_payload,
                output_contract="screenplay_candidate_artifact",
                tool_protocol_contract="screenplay_host_tools",
            ),
            binding=RunBinding(
                namespace="screenplay.agent.task",
                aggregate_id=domain_context.project_id,
                command_id=command_id,
                attributes=candidate_completion_projection(
                    scope=bound_context.to_core_context().payload,
                    turn_id=conversation_turn_id,
                    validation_contract=validation_contract,
                    host_candidate_template=host_candidate_template,
                ),
            ),
            response_transaction_policy=ResponseTransactionPolicy(
                mode=ResponseTransactionMode.VALIDATED_RESULT,
                public_presentation=PublicPresentationMode.NONE,
            ),
        )
        result = await run_screenplay_child(
            db=self._db,
            runs=self._runs,
            request=request,
            options=options,
            api_key=runtime.apiKey.get_secret_value(),
            signal=signal,
            bind_run=bind_run,
        )
        if result.status is RunStatus.CANCELED:
            raise asyncio.CancelledError
        if result.status is not RunStatus.DONE:
            code = str(result.error or "screenplay_tool_run_failed")
            failure = classify_screenplay_run_failure(code)
            raise ModelGatewayError(code, code=code, retryable=failure.retryable)
        candidate = await self._candidates.load_run(result.run_id)
        artifact = candidate.get("_artifact")
        if getattr(artifact, "status", None) is not ArtifactStatus.FINALIZED:
            raise ModelGatewayError(
                "screenplay candidate was not committed with its Run",
                code="candidate_commit_missing",
                retryable=True,
            )
        return ScreenplayCandidateRunResult(
            run_id=result.run_id,
            candidate={
                key: value
                for key, value in candidate.items()
                if not str(key).startswith("_")
            },
        )


async def run_screenplay_child(
    *,
    db,
    runs: AgentRunService,
    request: AgentRunRequest,
    options: AgentCoreRunOptions,
    api_key: str,
    signal,
    bind_run: BindRun | None,
) -> AgentRunResult:
    return await run_durable_agent_unit(
        db=db, runs=runs, request=request, options=options,
        api_key=api_key, signal=signal, bind_run=bind_run,
        active_error_code="screenplay_child_run_active",
    )


def screenplay_run_provenance(
    runtime,
    payload: Mapping[str, Any],
    *,
    output_contract: str,
    tool_protocol_contract: str,
) -> RunProvenance:
    request = model_request_from_runtime(runtime)
    window = runtime_context_window_tokens(runtime)
    profile = json.dumps(
        {
            "provider": runtime.apiProvider,
            "model": request.model,
            "contextWindow": window,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    reasoning_mode = reasoning_mode_from_options(runtime.options)
    return RunProvenance(
        model_provider=request.provider,
        model_name=request.model,
        context_window=window,
        endpoint_digest=digest_model_endpoint(runtime.baseURL),
        request_profile_digest=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
        capability_snapshot=request.capability_snapshot.to_mapping(
            include_digest=True
        ),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode,
            output_contract=output_contract,
            tool_protocol_contract=tool_protocol_contract,
        ),
    )


__all__ = [
    "BindRun",
    "ScreenplayCandidateRunResult",
    "ScreenplayToolCallingService",
    "run_screenplay_child",
    "screenplay_run_provenance",
]
