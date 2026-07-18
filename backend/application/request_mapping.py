"""Translate application DTOs into business-agnostic Core requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ModelRequest,
    RunProvenance,
    RunLineage,
)
from agent_core.engine import AgentCoreRunOptions
from agent_core.ports import ResponseJudge
from domains.writing.context import writing_context_claims
from domains.writing.contracts import WritingDomainContext
from domains.writing.response import (
    writing_response_constraints,
    writing_response_validators,
)
from schemas.ai import ChatStreamRequest


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
        writing_chapters=tuple(_mappings(body.writingChapters)),
        available_outlines=tuple(_mappings(body.availableOutlines)),
        associated_chapter_ids=tuple(body.associatedChapterIds or ()),
        associated_outline_ids=tuple(body.associatedOutlineIds or ()),
        selected_memory_ids=tuple(body.selectedMemoryIds or ()),
        selected_foreshadowing_ids=tuple(body.selectedForeshadowingIds or ()),
        context_window_label=str(window_label) if window_label else None,
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
            profile_id=profile_id,
            options=options,
        ),
        domain_context=context.to_core_context(),
        session_id=body.sessionId,
        mode=body.chatAgentMode,
        context_window=context_window_tokens(window_label),
        tools_enabled=bool(body.enableAgentTools and body.bookId),
    )


def writing_run_options(
    request: AgentRunRequest,
    provider_options: Mapping[str, Any],
    *,
    force_planned_tool_choice: bool = True,
    provenance: RunProvenance | None = None,
    lineage: RunLineage | None = None,
    response_judges: Sequence[ResponseJudge] = (),
) -> AgentCoreRunOptions:
    output_reserve = _positive_int(provider_options.get("max_tokens"), 8_192)
    if str(request.model.provider or "").strip().lower() == "anthropic":
        from infrastructure.models.capabilities import (
            build_anthropic_thinking_param,
            normalize_thinking_enabled,
        )

        _, output_reserve = build_anthropic_thinking_param(
            normalize_thinking_enabled(dict(provider_options)),
            output_reserve,
        )
    return AgentCoreRunOptions(
        context_claims=writing_context_claims(request),
        output_reserve_tokens=output_reserve,
        default_context_window_tokens=request.context_window or 200_000,
        force_planned_tool_choice=force_planned_tool_choice,
        provenance=provenance,
        lineage=lineage,
        response_constraints=writing_response_constraints(request),
        response_validators=writing_response_validators(request),
        response_judges=tuple(response_judges),
    )


def _mappings(values: list[Any] | None) -> list[dict[str, Any]]:
    return [dict(value) for value in (values or ()) if isinstance(value, Mapping)]


def _has_caller_tool_definitions(value: Any) -> bool:
    """Detect a caller-owned tool contract without silently discarding it."""

    if value is None:
        return False
    return not (isinstance(value, (list, tuple)) and not value)


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default
