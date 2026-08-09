"""Map the product-specific screenplay transport into opaque Core contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ModelRequest,
    RunBinding,
    RunLineage,
    RunProvenance,
)
from agent_core.engine import AgentCoreRunOptions
from agent_core.ports import ResponseJudge
from application.output_budget_policies import resolve_request_output_budget
from application.request_mapping import (
    UnsupportedCallerToolContractError,
    _has_caller_tool_definitions,
    context_window_tokens,
)
from domains.screenplay.context import screenplay_context_claims
from domains.screenplay.contracts import ScreenplayDomainContext
from infrastructure.models.profiles.registry import resolve_model_profile
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest


def to_screenplay_agent_request(
    body: ScreenplayAgentRunRequest,
    provider_options: Mapping[str, Any],
) -> AgentRunRequest:
    body_options = dict(body.options or {})
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
            "composed screenplay agent"
        )
    model = str(options.pop("model", "") or "").strip()
    profile_id = str(options.pop("model_profile", "") or "").strip() or None
    options.pop("max_tokens", None)
    options.pop("tools", None)
    options.pop("tool_choice", None)
    window_label = body.contextWindow or options.pop("context_window", None)
    context = ScreenplayDomainContext(
        project_id=body.screenplayProjectId,
        requested_source_book_id=body.sourceBookId,
        active_document_id=body.activeDocumentId,
        requested_stage=body.activeStage,
        context_window_label=str(window_label) if window_label else None,
        task_intent=body.screenplayTaskIntent,
        draft_scene_count=body.screenplayDraftSceneCount,
        draft_scope=body.screenplayDraftScope,
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
            output_capabilities=resolve_model_profile(
                profile_id,
                model,
                str(options.get("baseURL") or ""),
            ).output_capabilities(),
            options=options,
        ),
        domain_context=context.to_core_context(),
        session_id=body.sessionId,
        mode=body.chatAgentMode,
        context_window=context_window_tokens(window_label),
        tools_enabled=bool(body.enableAgentTools),
        metadata={"locale": body.locale},
    )


def screenplay_run_options(
    request: AgentRunRequest,
    *,
    provenance: RunProvenance | None = None,
    lineage: RunLineage | None = None,
    binding: RunBinding | None = None,
    response_judges: Sequence[ResponseJudge] = (),
    agent_role: str | None = None,
    output_work_units: int = 1,
) -> AgentCoreRunOptions:
    output_budget = resolve_request_output_budget(
        request,
        agent_role=agent_role,
        work_units=output_work_units,
    )
    return AgentCoreRunOptions(
        context_claims=screenplay_context_claims(request),
        output_reserve_tokens=output_budget.effective_tokens,
        output_budget=output_budget,
        default_context_window_tokens=request.context_window or 200_000,
        provenance=provenance,
        lineage=lineage,
        binding=binding,
        response_judges=tuple(response_judges),
    )


__all__ = ["screenplay_run_options", "to_screenplay_agent_request"]
