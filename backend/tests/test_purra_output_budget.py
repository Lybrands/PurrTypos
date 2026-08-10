from __future__ import annotations

from purra.output_budget import (
    ModelOutputCapabilities,
    OutputBudgetLimit,
    OutputBudgetPolicy,
    ThinkingTokenAccounting,
    resolve_output_budget,
)


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


def test_resolver_preserves_limit_precedence_when_values_tie():
    budget = resolve_output_budget(
        policy=OutputBudgetPolicy(
            key="tie",
            base_tokens=30_000,
            per_work_unit_tokens=0,
            safety_factor=1,
            hard_cap_tokens=20_000,
        ),
        capabilities=ModelOutputCapabilities(max_output_tokens=20_000),
        context_window_tokens=256_000,
    )

    assert budget.effective_tokens == 20_000
    assert budget.limiting_factor is OutputBudgetLimit.TASK_HARD_CAP


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
