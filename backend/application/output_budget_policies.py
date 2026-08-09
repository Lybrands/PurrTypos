"""Application-owned task policies for Agent Core output sizing."""

from __future__ import annotations

from collections.abc import Mapping

from agent_core.contracts import AgentRunRequest
from agent_core.output_budget import (
    OutputBudgetPolicy,
    OutputExecutionMode,
    ResolvedOutputBudget,
    resolve_output_budget,
)
from domains.screenplay.contracts import SCREENPLAY_DOMAIN_NAMESPACE
from domains.writing.contracts import WRITING_DOMAIN_NAMESPACE


_POLICIES = {
    "conversation": OutputBudgetPolicy(
        key="conversation",
        base_tokens=6_000,
        per_work_unit_tokens=0,
        safety_factor=1.25,
        hard_cap_tokens=16_000,
    ),
    "writing_agent": OutputBudgetPolicy(
        key="writing_agent",
        base_tokens=10_000,
        per_work_unit_tokens=0,
        safety_factor=1.2,
        hard_cap_tokens=24_000,
    ),
    "screenplay_agent": OutputBudgetPolicy(
        key="screenplay_agent",
        base_tokens=6_000,
        per_work_unit_tokens=0,
        safety_factor=1.25,
        hard_cap_tokens=16_000,
    ),
    "screenplay_writer": OutputBudgetPolicy(
        key="screenplay_writer",
        base_tokens=1_500,
        per_work_unit_tokens=3_200,
        safety_factor=1.2,
        hard_cap_tokens=24_000,
        reasoning_reserve_tokens=8_000,
        execution_mode=OutputExecutionMode.CHUNKED,
    ),
    "screenplay_reviewer": OutputBudgetPolicy(
        key="screenplay_reviewer",
        base_tokens=2_000,
        per_work_unit_tokens=500,
        safety_factor=1.2,
        hard_cap_tokens=48_000,
        reasoning_reserve_tokens=24_000,
        execution_mode=OutputExecutionMode.CHUNKED,
    ),
    "screenplay_rewriter": OutputBudgetPolicy(
        key="screenplay_rewriter",
        base_tokens=1_500,
        per_work_unit_tokens=3_200,
        safety_factor=1.2,
        hard_cap_tokens=24_000,
        reasoning_reserve_tokens=8_000,
        execution_mode=OutputExecutionMode.CHUNKED,
    ),
    "researcher": OutputBudgetPolicy(
        key="researcher",
        base_tokens=4_000,
        per_work_unit_tokens=0,
        safety_factor=1.25,
        hard_cap_tokens=8_000,
    ),
    "reviewer": OutputBudgetPolicy(
        key="reviewer",
        base_tokens=4_000,
        per_work_unit_tokens=0,
        safety_factor=1.25,
        hard_cap_tokens=8_000,
    ),
    "analyst": OutputBudgetPolicy(
        key="analyst",
        base_tokens=5_000,
        per_work_unit_tokens=0,
        safety_factor=1.2,
        hard_cap_tokens=10_000,
    ),
}


def output_budget_policy_for_request(
    request: AgentRunRequest,
    *,
    agent_role: str | None = None,
) -> OutputBudgetPolicy:
    role = str(agent_role or "").strip()
    if role in _POLICIES:
        return _POLICIES[role]
    if str(request.mode or "").strip().lower() != "agent":
        return _POLICIES["conversation"]
    if request.domain_context.namespace == SCREENPLAY_DOMAIN_NAMESPACE:
        return _POLICIES["screenplay_agent"]
    if request.domain_context.namespace == WRITING_DOMAIN_NAMESPACE:
        return _POLICIES["writing_agent"]
    return _POLICIES["conversation"]


def resolve_request_output_budget(
    request: AgentRunRequest,
    *,
    agent_role: str | None = None,
    work_units: int = 1,
) -> ResolvedOutputBudget:
    thinking = request.model.options.get("thinking")
    thinking_enabled = bool(
        request.model.options.get("thinking_enabled") is True
        or (
            isinstance(thinking, Mapping)
            and thinking.get("type") == "enabled"
        )
    )
    return resolve_output_budget(
        policy=output_budget_policy_for_request(
            request,
            agent_role=agent_role,
        ),
        capabilities=request.model.output_capabilities,
        context_window_tokens=request.context_window or 128_000,
        work_units=work_units,
        thinking_enabled=thinking_enabled,
    )


__all__ = [
    "output_budget_policy_for_request",
    "resolve_request_output_budget",
]
