"""Translate application DTOs into business-agnostic Core requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

from purra.api import AgentCoreRunOptions, PlanningMode
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBudgetClaim,
    ModelRequest,
    RunBinding,
    RunProvenance,
)
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.ports import ResponseJudgePolicy
from domains.writing.context import writing_context_claims
from domains.writing.contracts import (
    WRITING_DOMAIN_NAMESPACE,
    WritingDomainContext,
)
from domains.writing.response import (
    writing_response_contract_for_request,
    writing_response_constraints,
    writing_response_validators,
)
from domains.writing.public_facts import WritingPublicFactsProvider
from schemas.ai import ChatStreamRequest
from infrastructure.models.profiles.registry import resolve_model_profile
from application.model_runtime import (
    fit_output_limit_to_context,
    reasoning_mode_from_options,
)
from purra.model_protocol import (
    FeatureRequirement,
    TaskCapabilityRequirements,
    preflight_capabilities,
    resolve_invocation_output_limit,
)


CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "256k": 256_000,
    "300k": 300_000,
    "1m": 1_000_000,
}
class UnsupportedCallerToolContractError(ValueError):
    """The single Writing Agent path cannot execute caller-defined tools."""


def build_chat_provider_options(
    model: str,
    options: Mapping[str, Any],
    base_url: str,
    temperature: Any = None,
) -> dict[str, Any]:
    """Build the provider options owned by the single application path."""

    result = {"model": model, **dict(options), "baseURL": base_url}
    if temperature is not None:
        result["temperature"] = temperature
    return result


def context_window_tokens(value: Any) -> int:
    key = str(value or "").strip().lower()
    if not key:
        return CONTEXT_WINDOW_TOKENS["200k"]
    if key not in CONTEXT_WINDOW_TOKENS:
        raise ValueError(f"不支持的上下文窗口配置：{value}")
    return CONTEXT_WINDOW_TOKENS[key]


def to_writing_agent_request(
    body: ChatStreamRequest,
    provider_options: Mapping[str, Any],
) -> AgentRunRequest:
    """Map the stable HTTP request shape to Core plus opaque domain context."""

    body_options = dict(body.options or {})
    # Keep model runtime metadata (for example ``model_profile``) when this
    # mapper is used outside the HTTP router.  Router-owned normalized values
    # still win for connection routing, model name, and temperature.
    options = {**body_options, **dict(provider_options)}
    if (
        _has_caller_tool_definitions(body.tools)
        or _has_caller_tool_definitions(body_options.get("tools"))
        or body_options.get("tool_choice") is not None
        or _has_caller_tool_definitions(options.get("tools"))
        or options.get("tool_choice") is not None
    ):
        raise UnsupportedCallerToolContractError(
            "caller-owned tools and tool_choice are not supported by the "
            "composed writing agent"
        )
    model = str(options.pop("model", "") or "").strip()
    profile_id = str(options.pop("model_profile", "") or "").strip() or None
    options.pop("tools", None)
    options.pop("tool_choice", None)
    window_label = body.contextWindow or options.pop("context_window", None)
    context = WritingDomainContext(
        book_id=body.bookId,
        chapter_id=body.chapterId,
        current_chapter_title=body.currentChapterTitle,
        associated_chapter_ids=tuple(body.associatedChapterIds or ()),
        associated_outline_ids=tuple(body.associatedOutlineIds or ()),
        selected_memory_ids=tuple(body.selectedMemoryIds or ()),
        selected_long_term_memory_ids=tuple(
            body.selectedLongTermMemoryIds or ()
        ),
        selected_foreshadowing_ids=tuple(body.selectedForeshadowingIds or ()),
        context_window_label=str(window_label) if window_label else None,
        writing_method_overrides=(
            body.writingMethodOverrides.model_dump()
            if body.writingMethodOverrides is not None
            else {}
        ),
        writing_method_recommendation_requested=(
            _is_writing_method_recommendation_request(body.messages)
        ),
    )
    profile = resolve_model_profile(
        profile_id,
        model,
        str(options.get("baseURL") or ""),
    )
    reasoning_mode = reasoning_mode_from_options(options)
    selected_context_window = context_window_tokens(window_label)
    snapshot = profile.capability_snapshot(
        context_window_tokens=selected_context_window,
    )
    preflight_capabilities(
        snapshot,
        TaskCapabilityRequirements(
            reasoning_mode=reasoning_mode,
            tool_calling=(
                FeatureRequirement.REQUIRED
                if body.enableAgentTools and body.bookId
                else FeatureRequirement.OPTIONAL
            ),
            structured_output_level="none",
            streaming_required=True,
            cancellation_required=True,
        ),
    )
    request = AgentRunRequest(
        messages=tuple(
            AgentMessage.from_mapping(message)
            for message in body.messages
            if isinstance(message, Mapping)
        ),
        model=ModelRequest(
            provider=body.apiProvider,
            model=model,
            capability_snapshot=snapshot,
            options=options,
        ),
        domain_context=context.to_core_context(),
        session_id=body.sessionId,
        mode=body.chatAgentMode,
        context_window=selected_context_window,
        tools_enabled=bool(body.enableAgentTools and body.bookId),
        metadata={
            "locale": body.locale,
            **({"streamId": body.streamId} if body.streamId else {}),
        },
    )
    return (
        request
        if body.planningMode is None
        else replace(request, planning_mode=PlanningMode(body.planningMode))
    )


def validate_writing_request_contract(
    body: ChatStreamRequest,
    provider_options: Mapping[str, Any],
) -> None:
    """Application facade for product preflight without leaking assembly to HTTP."""

    to_writing_agent_request(body, provider_options)


def _is_writing_method_recommendation_request(messages: Sequence[Mapping[str, Any]]) -> bool:
    latest_user = next((
        str(message.get("content") or "").strip()
        for message in reversed(messages)
        if str(message.get("role") or "").strip().lower() == "user"
    ), "")
    return latest_user.startswith("[写作方法推荐]")


def writing_run_options(
    request: AgentRunRequest,
    provider_options: Mapping[str, Any],
    *,
    force_planned_tool_choice: bool = True,
    provenance: RunProvenance | None = None,
    response_judge_policies: Sequence[ResponseJudgePolicy] = (),
) -> AgentCoreRunOptions:
    context = WritingDomainContext.from_core_context(request.domain_context)
    output_limit = resolve_invocation_output_limit(
        request.model.capability_snapshot,
        request.model.options.get("max_tokens"),
    )
    context_window = request.context_window or 200_000
    output_limit = fit_output_limit_to_context(output_limit, context_window)
    response_constraints = writing_response_constraints(request)
    response_validators = writing_response_validators(request)
    judge_policies = tuple(response_judge_policies)
    requires_validated_result = bool(
        response_constraints.exact_top_level_item_count is not None
        or response_validators
        or judge_policies
    )
    binding = None
    if request.session_id is not None and request.metadata.get("streamId"):
        binding = RunBinding(
            namespace="writing.chat.request",
            aggregate_id=str(request.session_id),
            command_id=str(request.metadata["streamId"]),
        )
    elif context.book_id:
        binding = RunBinding(
            namespace="writing.context",
            aggregate_id=context.book_id,
            command_id=str(request.metadata.get("streamId") or uuid4().hex),
        )
    return AgentCoreRunOptions(
        context_claims=writing_context_claims(request),
        turn_id=(
            str(request.metadata["streamId"])
            if request.metadata.get("streamId")
            else None
        ),
        output_limit=output_limit,
        default_context_window_tokens=context_window,
        force_planned_tool_choice=force_planned_tool_choice,
        reasoning_mode=reasoning_mode_from_options(provider_options),
        provenance=provenance,
        binding=binding,
        response_constraints=response_constraints,
        response_validators=response_validators,
        response_judge_policies=judge_policies,
        response_transaction_policy=(
            ResponseTransactionPolicy(
                mode=ResponseTransactionMode.VALIDATED_RESULT,
                public_presentation=PublicPresentationMode.MODEL_LIVE,
            )
            if requires_validated_result
            else None
        ),
        committed_result_facts_provider=(
            WritingPublicFactsProvider(
                writing_response_contract_for_request(request)
            )
            if requires_validated_result
            else None
        ),
    )
def _has_caller_tool_definitions(value: Any) -> bool:
    """Detect a caller-owned tool contract without silently discarding it."""

    if value is None:
        return False
    return not (isinstance(value, (list, tuple)) and not value)
