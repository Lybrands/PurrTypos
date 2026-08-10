"""Application-owned task policies for PurrA output sizing."""

from __future__ import annotations

from collections.abc import Mapping

from purra.contracts import AgentRunRequest
from purra.output_budget import (
    OutputBudgetPolicy,
    ResolvedOutputBudget,
    resolve_output_budget,
)
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

SCREENPLAY_INTENT_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_intent",
    base_tokens=1_800,
    per_work_unit_tokens=0,
    safety_factor=1.25,
    hard_cap_tokens=4_096,
    reasoning_reserve_tokens=1_024,
)

SCREENPLAY_EPISODE_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_episode",
    base_tokens=1_800,
    per_work_unit_tokens=1_300,
    safety_factor=1.15,
    hard_cap_tokens=24_000,
    reasoning_reserve_tokens=12_000,
)

SCREENPLAY_SCENE_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_scene",
    base_tokens=5_000,
    per_work_unit_tokens=0,
    safety_factor=1.15,
    hard_cap_tokens=8_000,
)

SCREENPLAY_FRAGMENT_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_fragment",
    base_tokens=4_000,
    per_work_unit_tokens=0,
    safety_factor=1.15,
    hard_cap_tokens=12_000,
    reasoning_reserve_tokens=6_000,
)

SCREENPLAY_METADATA_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_metadata",
    base_tokens=1_200,
    per_work_unit_tokens=0,
    safety_factor=1.1,
    hard_cap_tokens=2_000,
)

SCREENPLAY_DELIVERABLE_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_deliverable",
    base_tokens=8_000,
    per_work_unit_tokens=0,
    safety_factor=1.2,
    hard_cap_tokens=24_000,
    reasoning_reserve_tokens=8_000,
)

SCREENPLAY_REVIEW_OUTPUT_POLICY = OutputBudgetPolicy(
    key="screenplay_review",
    base_tokens=8_000,
    per_work_unit_tokens=0,
    safety_factor=1.2,
    hard_cap_tokens=40_000,
    reasoning_reserve_tokens=24_000,
)


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
    "SCREENPLAY_DELIVERABLE_OUTPUT_POLICY",
    "SCREENPLAY_EPISODE_OUTPUT_POLICY",
    "SCREENPLAY_FRAGMENT_OUTPUT_POLICY",
    "SCREENPLAY_INTENT_OUTPUT_POLICY",
    "SCREENPLAY_METADATA_OUTPUT_POLICY",
    "SCREENPLAY_REVIEW_OUTPUT_POLICY",
    "SCREENPLAY_SCENE_OUTPUT_POLICY",
    "output_budget_policy_for_request",
    "resolve_request_output_budget",
]
