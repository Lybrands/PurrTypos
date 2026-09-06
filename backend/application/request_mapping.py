"""Translate application DTOs into business-agnostic Core requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import SimpleNamespace
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
from application.agent_conversation_input import conversation_messages, conversation_input_metadata
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
)
from purra.model_protocol import (
    FeatureRequirement,
    TaskCapabilityRequirements,
)


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
    window_label = body.contextWindow or options.get("context_window")
    runtime = SimpleNamespace(options=options, contextWindow=window_label,
                              baseURL=options.get("baseURL") or body.baseURL,
                              apiProvider=body.apiProvider)
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
    model_request = model_request_from_runtime(runtime, requirements=TaskCapabilityRequirements(
        reasoning_mode=reasoning_mode_from_options(options),
        tool_calling=FeatureRequirement.REQUIRED if body.enableAgentTools and body.bookId else FeatureRequirement.OPTIONAL,
        structured_output_level="none", streaming_required=True, cancellation_required=True,
    ))
    selected_context_window = model_request.capability_snapshot.context_window_tokens
    request = AgentRunRequest(
        messages=conversation_messages(tuple(
            AgentMessage.from_mapping(message)
            for message in body.messages
            if isinstance(message, Mapping)
        ), context_window=selected_context_window),
        model=model_request,
        domain_context=context.to_core_context(),
        session_id=body.sessionId,
        mode=body.chatAgentMode,
        context_window=selected_context_window,
        tools_enabled=bool(body.enableAgentTools and body.bookId),
        metadata={
            **conversation_input_metadata(source="client_public_messages", scope=f"writing:{body.bookId}:{body.sessionId}"),
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
    context_window = request.context_window or 200_000
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
        default_context_window_tokens=context_window,
        force_planned_tool_choice=force_planned_tool_choice,
        reasoning_mode=reasoning_mode_from_options(request.model.options),
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
