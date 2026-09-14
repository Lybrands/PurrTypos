"""PurrA Operation-backed model runner for Screenplay replacement Parts."""

from __future__ import annotations

import asyncio
import json
import logging
from hashlib import sha256
from uuid import uuid4

from agents.screenplay.access_contract import WRITE_SCREENPLAY_CANDIDATE_PART
from agents.screenplay.candidate_artifact import (
    SCREENPLAY_CANDIDATE_REF_PREFIX,
    ScreenplayCandidateArtifactStore,
)
from agents.screenplay.capture_artifact import validate_screenplay_host_capture
from agents.screenplay.contracts import (
    SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
    ScreenplayPartCompletion,
    screenplay_part_definition,
)
from agents.screenplay.output_contract import ScreenplayPartOutputEvidence
from agents.screenplay.usage import ScreenplayOperationUsageStore
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
    RunProvenance,
)
from purra.errors import ModelGatewayError
from purra.model_protocol import FeatureRequirement, TaskCapabilityRequirements
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.structured_output import parse_json_object


_RETRYABLE_OPERATION_ERRORS = frozenset({
    "missing_required_tool_call",
    "model_output_truncated",
    "tool_call_truncated",
    "screenplay_candidate_missing",
    # PurrA reports the generic terminal code after its in-Operation response
    # repair budget is exhausted. The owning Part still has a separate bounded
    # attempt budget, so this remains a recoverable model-output failure here.
    "response_constraint_violation",
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


logger = logging.getLogger(__name__)


def _required_revision_roles(scope) -> tuple[str, ...]:
    roles = set(scope.deliverable_revision_scope)
    if scope.target_role in {"sceneList", "screenplayDraft"}:
        roles.discard(scope.target_role)
    return tuple(sorted(roles))


class SubmittedScreenplayCandidateValidator:
    def __init__(self, scope) -> None:
        self._scope = scope

    def validate(self, *, content, messages):
        del content
        submitted = {
            call.id
            for message in messages
            for call in message.tool_calls
            if call.name == WRITE_SCREENPLAY_CANDIDATE_PART
        }
        submitted_receipt = False
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
                    SCREENPLAY_CANDIDATE_REF_PREFIX
                )
            ):
                submitted_receipt = True
                break
        if not submitted_receipt:
            return ResponseValidationResult(
                violation_code="screenplay_candidate_missing",
                repair_guidance=(
                    f"Call {WRITE_SCREENPLAY_CANDIDATE_PART} with the complete "
                    "current Part candidate before finishing. Repair any tool error first."
                ),
            )
        required_roles = set(_required_revision_roles(self._scope))
        read_roles = set()
        for message in messages:
            for call in message.tool_calls:
                if call.name != "readScreenplayBoundRevisionV1":
                    continue
                try:
                    arguments = json.loads(call.arguments_json or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(arguments, dict):
                    role = str(arguments.get("role") or "").strip()
                    if role:
                        read_roles.add(role)
        missing_roles = sorted(required_roles - read_roles)
        if missing_roles:
            return ResponseValidationResult(
                violation_code="screenplay_required_revision_not_read",
                repair_guidance=(
                    "Read every required frozen revision before submitting again: "
                    + ", ".join(missing_roles)
                    + "."
                ),
            )
        return ResponseValidationResult()


class ScreenplayHostCaptureValidator:
    def __init__(self, scope) -> None:
        self._scope = scope

    def validate(self, *, content, messages):
        del messages
        try:
            validate_screenplay_host_capture(
                self._scope,
                parse_json_object(content),
            )
        except (TypeError, ValueError):
            return ResponseValidationResult(
                violation_code="screenplay_structured_output_invalid",
                repair_guidance=(
                    "Return one complete JSON object matching the exact host-capture "
                    "contract for this Part. Do not add fields or Markdown fences."
                ),
            )
        return ResponseValidationResult()


class PurrAScreenplayModelPartRunner:
    """Execute one model Part privately inside its owning Root Run."""

    def __init__(self, db, composition, runtime) -> None:
        self._db = db
        self._runs = AgentRunService(composition)
        self._runtime = runtime
        self._artifacts = ScreenplayCandidateArtifactStore(db)
        self._usage = ScreenplayOperationUsageStore(db)

    async def run(
        self,
        *,
        scope,
        enabled_tool_names,
        instruction,
        context,
        signal=None,
    ):
        definition = screenplay_part_definition(scope.part_kind)
        if definition.completion not in {
            ScreenplayPartCompletion.CANDIDATE_TOOL,
            ScreenplayPartCompletion.HOST_CAPTURE,
        }:
            raise ValueError("Screenplay model runner completion is unsupported")
        candidate_owned = definition.completion is ScreenplayPartCompletion.CANDIDATE_TOOL
        if candidate_owned and WRITE_SCREENPLAY_CANDIDATE_PART not in enabled_tool_names:
            raise ValueError("Screenplay Part is missing its submission capability")
        runtime = self._runtime
        model = model_request_from_runtime(
            runtime,
            json_object_output=not candidate_owned,
            task_reasoning_preference="economical",
            requirements=TaskCapabilityRequirements(
                reasoning_mode=reasoning_mode_from_options(runtime.options),
                tool_calling=FeatureRequirement.REQUIRED,
                structured_output_level=("none" if candidate_owned else "json_object"),
                streaming_required=True,
                cancellation_required=True,
            ),
        )
        payload = {
            "partKind": scope.part_kind.value,
            "partKey": scope.part_key,
            "targetRole": scope.target_role,
            "dependencyPartKeys": list(scope.dependency_part_keys),
            "requiredRevisionRoles": list(_required_revision_roles(scope)),
            **(
                {"candidateContract": _candidate_contract(scope)}
                if candidate_owned
                else {"hostCaptureContract": _host_capture_contract(scope)}
            ),
        }
        context_window = model.capability_snapshot.context_window_tokens
        if estimate_json_tokens(payload) + 3_072 > context_window:
            raise ModelGatewayError(
                "Screenplay Part exceeds its context budget",
                code="screenplay_context_budget_exceeded",
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
                        "You are executing one host-bounded screenplay Part. "
                        + instruction
                        + " Use only the enabled reads. Do not output private reasoning. "
                        + (
                            "Read every role in requiredRevisionRoles with "
                            "readScreenplayBoundRevisionV1 before submitting. "
                            if candidate_owned and payload["requiredRevisionRoles"]
                            else ""
                        )
                        + (
                            "Submit only this Part with " + WRITE_SCREENPLAY_CANDIDATE_PART
                            if candidate_owned
                            else "Return only the exact host-capture JSON object"
                        )
                        + ".\n"
                        + json.dumps(payload, ensure_ascii=False, allow_nan=False)
                    ),
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content="执行当前剧本 Part 并提交完整候选。",
                ),
            ),
            model=model,
            domain_context=DomainContext(
                namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    "schemaVersion": 1,
                    "projectId": scope.project_id,
                    "unit": scope.to_mapping(),
                },
            ),
            mode="screenplay_unit",
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
        lease_id = "screenplay-replacement:" + uuid4().hex
        admission = await health.acquire(provider_scope, lease_id)
        if not admission.allowed:
            raise ModelGatewayError(
                "model provider is temporarily unavailable for new work",
                code=str(admission.reason_code or "provider_circuit_open"),
                retryable=True,
            )
        operation_started = False
        primary_error: BaseException | None = None
        try:
            operation_started = True
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
                        (SubmittedScreenplayCandidateValidator(scope),)
                        if candidate_owned
                        else (ScreenplayHostCaptureValidator(scope),)
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
                            output_contract=(
                                "screenplay_candidate_artifact_v1"
                                if candidate_owned
                                else "screenplay_host_capture_v1"
                            ),
                            tool_protocol_contract="screenplay_replacement_tools_v1",
                        ),
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
            if operation_id != scope.operation_scope_id:
                raise ModelGatewayError(
                    "Screenplay Operation identity changed",
                    code="operation_scope_invalid",
                    retryable=False,
                )
            if result.outcome.value == "canceled":
                raise asyncio.CancelledError
            if result.outcome.value != "completed":
                code = str(result.error_code or "screenplay_part_failed")
                if code in _PROVIDER_FAILURE_CODES:
                    await health.record_failure(provider_scope, code)
                raise ModelGatewayError(
                    code,
                    code=code,
                    retryable=code in _RETRYABLE_OPERATION_ERRORS,
                )
            await health.record_success(provider_scope)
            if candidate_owned:
                try:
                    receipt = await self._artifacts.require_scope_receipt(scope)
                except ValueError as error:
                    raise ModelGatewayError(
                        str(error),
                        code="screenplay_candidate_missing",
                        retryable=True,
                    ) from error
                return ScreenplayPartOutputEvidence(
                    candidate_artifact_id=receipt.artifact_id
                )
            try:
                capture = validate_screenplay_host_capture(
                    scope,
                    parse_json_object(result.final_response),
                )
            except (TypeError, ValueError) as error:
                raise ModelGatewayError(
                    str(error),
                    code="screenplay_structured_output_invalid",
                    retryable=True,
                ) from error
            return ScreenplayPartOutputEvidence(host_capture=capture)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            supplementary_error: Exception | None = None
            if operation_started:
                try:
                    await self._usage.record_from_events(
                        scope=scope,
                        root_run_id=context.run_id,
                    )
                except Exception as error:
                    supplementary_error = error
                    logger.exception(
                        "Failed to persist Screenplay Operation usage",
                        extra={"operationScopeId": scope.operation_scope_id},
                    )
            try:
                await health.release(lease_id)
            except Exception as error:
                if supplementary_error is None:
                    supplementary_error = error
                logger.exception(
                    "Failed to release Screenplay Provider lease",
                    extra={"operationScopeId": scope.operation_scope_id},
                )
            if primary_error is None and supplementary_error is not None:
                raise supplementary_error


__all__ = [
    "PurrAScreenplayModelPartRunner",
    "SubmittedScreenplayCandidateValidator",
]


def _candidate_contract(scope):
    if scope.target_role == "sceneList" and scope.part_kind.value == "document_section":
        return {
            "episodeNumber": scope.episode_number,
            "title": "<episode title>",
            "scenes": [{
                "id": "<globally unique stable scene id>",
                "heading": "<scene heading>",
                "objective": "<scene objective>",
                "conflict": "<scene conflict>",
                "turn": "<scene turn>",
                "synopsis": "<scene synopsis>",
            }],
        }
    if scope.target_role == "review" and scope.part_kind.value == "review_dimension":
        return {
            "verdict": "<ready | revise | major_rework>",
            "issues": [{
                "id": "<stable unique issue id>",
                "severity": "<minor | major | critical>",
                "description": "<specific problem, impact, and revision direction>",
                "sceneIds": ["<affected scene id>"],
            }],
        }
    return {
        "type": "object",
        "description": "Complete content for only the current Part.",
    }


def _host_capture_contract(scope):
    if scope.part_kind.value == "draft_scene":
        return {"sceneId": scope.scene_id, "sceneText": "<complete scene text>"}
    return {
        "episodeNumber": scope.episode_number,
        "title": "<episode title>",
        "continuitySummary": "<continuity summary>",
    }
