from __future__ import annotations

from agent_core.contracts import AgentRunRequest, DomainContext, ModelRequest
from agent_core.output_budget import (
    ModelOutputCapabilities,
    OutputBudgetLimit,
    OutputBudgetPolicy,
    ThinkingTokenAccounting,
    resolve_output_budget,
)
from application.output_budget_policies import resolve_request_output_budget


def test_resolver_separates_task_estimate_from_model_capability():
    budget = resolve_output_budget(
        policy=OutputBudgetPolicy(
            key="fixture",
            base_tokens=2_000,
            per_work_unit_tokens=3_000,
            safety_factor=1.2,
            hard_cap_tokens=32_000,
        ),
        capabilities=ModelOutputCapabilities(max_output_tokens=128_000),
        context_window_tokens=256_000,
        work_units=4,
    )

    assert budget.target_tokens == 14_000
    assert budget.requested_tokens == 16_800
    assert budget.effective_tokens == 16_800
    assert budget.model_max_output_tokens == 128_000
    assert budget.limiting_factor is OutputBudgetLimit.TASK_ESTIMATE


def test_resolver_reports_the_boundary_that_reduces_the_request():
    policy = OutputBudgetPolicy(
        key="fixture",
        base_tokens=20_000,
        per_work_unit_tokens=0,
        safety_factor=1.5,
        hard_cap_tokens=24_000,
    )

    task_limited = resolve_output_budget(
        policy=policy,
        capabilities=ModelOutputCapabilities(max_output_tokens=128_000),
        context_window_tokens=256_000,
    )
    model_limited = resolve_output_budget(
        policy=policy,
        capabilities=ModelOutputCapabilities(max_output_tokens=12_000),
        context_window_tokens=256_000,
    )

    assert task_limited.effective_tokens == 24_000
    assert task_limited.limiting_factor is OutputBudgetLimit.TASK_HARD_CAP
    assert model_limited.effective_tokens == 12_000
    assert model_limited.limiting_factor is OutputBudgetLimit.MODEL_CAPABILITY


def test_screenplay_writer_budget_scales_with_assigned_scene_count():
    request = AgentRunRequest(
        messages=(),
        model=ModelRequest(
            provider="openai",
            model="mimo-v2.5-pro",
            output_capabilities=ModelOutputCapabilities(
                max_output_tokens=131_072,
            ),
        ),
        domain_context=DomainContext(namespace="screenplay", payload={}),
        mode="agent",
        context_window=1_000_000,
    )

    one_scene = resolve_request_output_budget(
        request,
        agent_role="screenplay_writer",
        work_units=1,
    )
    four_scenes = resolve_request_output_budget(
        request,
        agent_role="screenplay_writer",
        work_units=4,
    )

    assert one_scene.policy_key == "screenplay_writer"
    assert one_scene.effective_tokens == 5_640
    assert four_scenes.effective_tokens == 17_160
    assert four_scenes.execution_mode.value == "chunked"


def test_screenplay_reviewer_reserves_shared_reasoning_budget_when_enabled():
    request = AgentRunRequest(
        messages=(),
        model=ModelRequest(
            provider="deepseek",
            model="deepseek-v4",
            options={"thinking": {"type": "enabled"}},
            output_capabilities=ModelOutputCapabilities(
                max_output_tokens=65_536,
                thinking_token_accounting=ThinkingTokenAccounting.INCLUDED,
            ),
        ),
        domain_context=DomainContext(namespace="screenplay", payload={}),
        mode="ask",
        context_window=256_000,
    )

    budget = resolve_request_output_budget(
        request,
        agent_role="screenplay_reviewer",
        work_units=13,
    )

    assert budget.target_tokens == 8_500
    assert budget.reasoning_reserve_tokens == 24_000
    assert budget.effective_tokens == 34_200
    assert budget.thinking_enabled is True


def test_separate_reasoning_accounting_does_not_consume_visible_output_budget():
    budget = resolve_output_budget(
        policy=OutputBudgetPolicy(
            key="fixture",
            base_tokens=2_000,
            per_work_unit_tokens=0,
            safety_factor=1.2,
            hard_cap_tokens=32_000,
            reasoning_reserve_tokens=10_000,
        ),
        capabilities=ModelOutputCapabilities(
            max_output_tokens=64_000,
            thinking_token_accounting=ThinkingTokenAccounting.SEPARATE,
        ),
        context_window_tokens=128_000,
        thinking_enabled=True,
    )

    assert budget.reasoning_reserve_tokens == 0
    assert budget.effective_tokens == 2_400


def test_small_context_window_clamps_before_context_allocation():
    budget = resolve_output_budget(
        policy=OutputBudgetPolicy(
            key="large-task",
            base_tokens=100_000,
            per_work_unit_tokens=0,
            safety_factor=1,
            hard_cap_tokens=100_000,
        ),
        capabilities=ModelOutputCapabilities(),
        context_window_tokens=32_000,
    )

    assert budget.effective_tokens == 16_000
    assert budget.limiting_factor is OutputBudgetLimit.CONTEXT_AVAILABLE
