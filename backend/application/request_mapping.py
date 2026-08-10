"""Translate application DTOs into business-agnostic Core requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBudgetClaim,
    ModelRequest,
    RunProvenance,
    RunLineage,
)
from purra.api import AgentCoreRunOptions
from purra.ports import ResponseJudge
from domains.writing.context import writing_context_claims
from domains.writing.contracts import (
    WRITING_DOMAIN_NAMESPACE,
    WritingDomainContext,
)
from domains.writing.response import (
    writing_response_constraints,
    writing_response_validators,
)
from schemas.ai import ChatStreamRequest
from infrastructure.models.profiles.registry import resolve_model_profile
from application.model_runtime import reasoning_mode_from_options
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
        selected_foreshadowing_ids=tuple(body.selectedForeshadowingIds or ()),
        context_window_label=str(window_label) if window_label else None,
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
    return AgentRunRequest(
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
        metadata={"locale": body.locale},
    )


def to_agent_request(
    body: ChatStreamRequest,
    provider_options: Mapping[str, Any],
) -> AgentRunRequest:
    return to_writing_agent_request(body, provider_options)


def agent_context_claims(
    request: AgentRunRequest,
) -> tuple[ContextBudgetClaim, ...]:
    if request.domain_context.namespace == WRITING_DOMAIN_NAMESPACE:
        return writing_context_claims(request)
    raise ValueError(
        "generic Agent mapping supports only the Writing domain"
    )


def writing_run_options(
    request: AgentRunRequest,
    provider_options: Mapping[str, Any],
    *,
    force_planned_tool_choice: bool = True,
    provenance: RunProvenance | None = None,
    lineage: RunLineage | None = None,
    response_judges: Sequence[ResponseJudge] = (),
    agent_role: str | None = None,
    output_work_units: int = 1,
) -> AgentCoreRunOptions:
    del agent_role, output_work_units
    output_limit = resolve_invocation_output_limit(
        request.model.capability_snapshot,
        request.model.options.get("max_tokens"),
    )
    return AgentCoreRunOptions(
        context_claims=writing_context_claims(request),
        output_limit=output_limit,
        default_context_window_tokens=request.context_window or 200_000,
        force_planned_tool_choice=force_planned_tool_choice,
        reasoning_mode=reasoning_mode_from_options(provider_options),
        provenance=provenance,
        lineage=lineage,
        response_constraints=writing_response_constraints(request),
        response_validators=writing_response_validators(request),
        response_judges=tuple(response_judges),
    )


def agent_run_options(
    request: AgentRunRequest,
    provider_options: Mapping[str, Any],
    *,
    force_planned_tool_choice: bool = True,
    provenance: RunProvenance | None = None,
    lineage: RunLineage | None = None,
    response_judges: Sequence[ResponseJudge] = (),
    agent_role: str | None = None,
    output_work_units: int = 1,
) -> AgentCoreRunOptions:
    if request.domain_context.namespace != WRITING_DOMAIN_NAMESPACE:
        raise ValueError(
            "generic Agent run options support only the Writing domain"
        )
    return writing_run_options(
        request,
        provider_options,
        force_planned_tool_choice=force_planned_tool_choice,
        provenance=provenance,
        lineage=lineage,
        response_judges=response_judges,
        agent_role=agent_role,
        output_work_units=output_work_units,
    )


def _has_caller_tool_definitions(value: Any) -> bool:
    """Detect a caller-owned tool contract without silently discarding it."""

    if value is None:
        return False
    return not (isinstance(value, (list, tuple)) and not value)
