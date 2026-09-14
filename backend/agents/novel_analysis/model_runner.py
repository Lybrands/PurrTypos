"""PurrA Operation-backed model runner for replacement analysis Units."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from uuid import uuid4

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS,
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)
from agents.novel_analysis.recipe import AnalysisUnitKind
from agents.novel_analysis.submission_tool import SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
from application.agent_run_service import AgentRunService
from application.durable_agent_run import run_durable_operation
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.run_provenance import digest_model_endpoint
from infrastructure.persistence.provider_health_repository import (
    ProviderHealthRepository,
    ProviderHealthScope,
)
from purra.api import AgentCoreRunOptions
from purra.context_budget import estimate_json_tokens
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageOrigin,
    MessageRole,
    PlanningMode,
    ResponseValidationResult,
    RunBinding,
    RunProvenance,
)
from purra.errors import ModelGatewayError
from purra.model_protocol import FeatureRequirement, TaskCapabilityRequirements
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)


_RETRYABLE_OPERATION_ERRORS = frozenset({
    "missing_required_tool_call",
    "model_output_truncated",
    "tool_call_truncated",
    "novel_analysis_result_not_submitted",
    "provider_circuit_open",
    "provider_capacity_limited",
    "provider_rate_limited",
    "provider_unavailable",
    "upstream_stream_interrupted",
    "model_activity_deadline_exceeded",
    "model_progress_deadline_exceeded",
    "model_invocation_deadline_exceeded",
})
_PROVIDER_FAILURE_CODES = frozenset({
    "model_gateway_error",
    "provider_rate_limited",
    "provider_unavailable",
    "upstream_stream_interrupted",
    "model_activity_deadline_exceeded",
    "model_progress_deadline_exceeded",
    "model_invocation_deadline_exceeded",
})


class SubmittedNovelAnalysisUnitResultValidator:
    def validate(self, *, content, messages):
        del content
        submitted = {
            call.id
            for message in messages
            for call in message.tool_calls
            if call.name == SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
        }
        for message in messages:
            if (
                message.role is not MessageRole.TOOL
                or message.tool_call_id not in submitted
            ):
                continue
            try:
                receipt = json.loads(message.content or "")
            except (TypeError, ValueError):
                continue
            if (
                isinstance(receipt, dict)
                and str(receipt.get("artifactRef") or "").startswith(
                    "novel-analysis-v1://"
                )
            ):
                return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code="novel_analysis_result_not_submitted",
            repair_guidance=(
                f"Call {SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT} with the complete "
                "current Unit result before finishing. Repair any tool error first."
            ),
        )


class PurrANovelAnalysisModelUnitRunner:
    """Execute each model Unit privately inside its owning Root Run."""

    def __init__(self, db, composition, runtime) -> None:
        self._db = db
        self._runs = AgentRunService(composition)
        self._runtime = runtime
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def run(
        self,
        *,
        kind: AnalysisUnitKind,
        scope,
        dependency_payloads,
        observations,
        enabled_tool_names,
        instruction,
        context,
        signal=None,
    ):
        if SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT not in enabled_tool_names:
            raise ValueError("analysis model Unit is missing its submission capability")
        runtime = self._runtime
        model = model_request_from_runtime(
            runtime,
            task_reasoning_preference="economical",
            requirements=TaskCapabilityRequirements(
                reasoning_mode=reasoning_mode_from_options(runtime.options),
                tool_calling=FeatureRequirement.REQUIRED,
                structured_output_level="none",
                streaming_required=True,
                cancellation_required=True,
            ),
        )
        payload = {
            "unitKind": kind.value,
            "dependencyResults": list(dependency_payloads),
            "availableObservationCount": len(observations),
            "resultContract": _result_contract(kind),
        }
        context_window = model.capability_snapshot.context_window_tokens
        if estimate_json_tokens(payload) + 3_072 > context_window:
            raise ModelGatewayError(
                "novel analysis Unit exceeds its context budget",
                code="novel_analysis_context_budget_exceeded",
                retryable=False,
            )
        request_profile = {
            "instruction": instruction,
            "payload": payload,
            "model": model.model,
            "contextWindow": context_window,
        }
        request = AgentRunRequest(
            messages=(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    origin=MessageOrigin.HOST_CONTEXT,
                    content=(
                        "You are executing one host-bounded novel-analysis Unit. "
                        + instruction
                        + " The dependency results and exact result contract follow. "
                        + "The submitted result must contain exactly the keys shown "
                        + "for this Unit; keep every nested field at the shown level "
                        + "and do not add fields at the result root. "
                        + "Do not output private reasoning. After required reads, call "
                        + SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
                        + ".\n"
                        + json.dumps(payload, ensure_ascii=False, allow_nan=False)
                    ),
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content="执行当前分析单元并提交完整结果。",
                ),
            ),
            model=model,
            domain_context=DomainContext(
                namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    **scope.to_mapping(),
                    "unit": {
                        "kind": kind.value,
                        "observations": list(observations),
                    },
                },
            ),
            mode="novel_analysis_unit",
            tools_enabled=True,
            planning_mode=PlanningMode.REACTIVE,
            context_window=context_window,
            metadata={
                "locale": "zh-CN",
                "responseAudience": "internal",
                "progressAudience": "internal",
            },
        )
        provider_scope = ProviderHealthScope(
            provider=model.provider,
            model=model.model,
            endpoint_digest=digest_model_endpoint(runtime.baseURL),
        )
        health = ProviderHealthRepository(self._db)
        lease_id = "novel-analysis-replacement:" + uuid4().hex
        admission = await health.acquire(provider_scope, lease_id)
        if not admission.allowed:
            raise ModelGatewayError(
                "model provider is temporarily unavailable for new work",
                code=str(admission.reason_code or "provider_circuit_open"),
                retryable=True,
            )
        try:
            result, operation_id = await run_durable_operation(
                db=self._db,
                runs=self._runs,
                request=request,
                options=AgentCoreRunOptions(
                    turn_id=context.run_id,
                    default_context_window_tokens=context_window,
                    force_planned_tool_choice=False,
                    require_tool_call=True,
                    response_validators=(
                        SubmittedNovelAnalysisUnitResultValidator(),
                    ),
                    reasoning_mode=reasoning_mode_from_options(model.options),
                    provenance=RunProvenance(
                        model_provider=model.provider,
                        model_name=model.model,
                        context_window=context_window,
                        endpoint_digest=digest_model_endpoint(runtime.baseURL),
                        request_profile_digest=sha256(
                            json.dumps(
                                request_profile,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        capability_snapshot=model.capability_snapshot.to_mapping(
                            include_digest=True
                        ),
                        execution_intent=run_execution_intent(
                            model,
                            reasoning_mode_from_options(model.options),
                            output_contract="novel_analysis_unit_artifact_v1",
                            tool_protocol_contract="novel_analysis_replacement_tools_v1",
                        ),
                    ),
                    binding=RunBinding(
                        namespace="novel_analysis.replacement.unit",
                        aggregate_id=scope.source_revision_id,
                        command_id=f"{context.task.id}:{context.unit.id}",
                        attributes={
                            "taskId": context.task.id,
                            "unitId": context.unit.id,
                        },
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.VALIDATED_RESULT,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                ),
                api_key=runtime.apiKey.get_secret_value(),
                signal=signal,
                bind_run=None,
                owning_run_id=context.run_id,
                task_id=context.task.id,
                unit_id=context.unit.id,
            )
            if result.outcome.value == "canceled":
                raise asyncio.CancelledError
            if result.outcome.value != "completed":
                code = str(result.error_code or "novel_analysis_unit_failed")
                if code in _PROVIDER_FAILURE_CODES:
                    await health.record_failure(provider_scope, code)
                raise ModelGatewayError(
                    code,
                    code=code,
                    retryable=code in _RETRYABLE_OPERATION_ERRORS,
                )
            await health.record_success(provider_scope)
            try:
                return await self._artifacts.load_operation_payload(
                    task_id=context.task.id,
                    operation_id=operation_id,
                )
            except ValueError as error:
                raise ModelGatewayError(
                    str(error),
                    code="novel_analysis_model_output_invalid",
                    retryable=True,
                ) from error
        finally:
            await health.release(lease_id)


def _result_contract(kind: AnalysisUnitKind) -> dict[str, object]:
    evidence = {"segmentId": "<listed segment id>", "sourceSpanId": "<read Sx-y>"}
    if kind in {AnalysisUnitKind.EXTRACT, AnalysisUnitKind.NORMALIZE}:
        observation = {
            "cardKind": "<kind>",
            "title": "<title>",
            "bodyMarkdown": "<analysis>",
            "evidenceRefs": [evidence],
        }
        if kind is AnalysisUnitKind.NORMALIZE:
            observation["mergedObservationIds"] = ["<input observation id>"]
        return {
            "facts": [{
                "factKind": (
                    "<one of: "
                    + ", ".join(sorted(NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS))
                    + ">"
                ),
                "subjectKey": "<stable subject>",
                "predicate": "<predicate>",
                "value": "<JSON value>",
                "evidenceRefs": [evidence],
            }],
            "observations": [observation],
        }
    if kind is AnalysisUnitKind.OVERVIEW:
        return {"storyOverview": {
            "summaryMarkdown": "<overview>",
            "evidenceRefs": [evidence],
        }}
    if kind is AnalysisUnitKind.DISTILL_TECHNIQUE:
        return {
            "techniqueResult": {
                "status": "generated",
                "techniques": [{
                    "title": "<title>",
                    "bodyMarkdown": "<technique>",
                    "observationIds": ["<input observation id>"],
                }],
            },
        }
    raise ValueError(f"{kind.value} is not a model Unit")


__all__ = [
    "PurrANovelAnalysisModelUnitRunner",
    "SubmittedNovelAnalysisUnitResultValidator",
]
