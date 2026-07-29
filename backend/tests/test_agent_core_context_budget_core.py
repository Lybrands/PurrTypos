from __future__ import annotations

import pytest

from agent_core.context_budget import (
    allocate_context_budget,
    estimate_agent_messages_tokens,
    estimate_tool_schema_tokens,
    trim_agent_messages_by_turn,
)
from agent_core.contracts import (
    AgentMessage,
    ContextBudgetClaim,
    MessageOrigin,
    MessageRole,
    ToolSchema,
)
from agent_core.errors import ContextOverflowError


def test_generic_claims_share_one_exact_model_window():
    budget = allocate_context_budget(
        window_tokens=10_000,
        output_reserve_tokens=1_000,
        safety_reserve_tokens=500,
        runtime_reserve_tokens=500,
        minimum_message_tokens=500,
        claims=(
            ContextBudgetClaim(name="alpha", desired_tokens=9_000),
            ContextBudgetClaim(name="beta", desired_tokens=3_000),
        ),
    )

    assert (
        budget.provider_input_tokens
        + budget.runtime_reserve_tokens
        + budget.output_reserve_tokens
        + budget.safety_reserve_tokens
        + budget.tool_schema_tokens
    ) == budget.window_tokens
    assert sum(budget.context_allocations.values()) == budget.context_pool_tokens
    assert budget.allocation_for("alpha") == 5_625
    assert budget.allocation_for("beta") == 1_875


def test_claim_allocation_is_deterministic_and_rejects_duplicate_names():
    kwargs = dict(
        window_tokens=4_000,
        output_reserve_tokens=500,
        safety_reserve_tokens=300,
        runtime_reserve_tokens=200,
        minimum_message_tokens=100,
        claims=(
            ContextBudgetClaim(name="first", desired_tokens=2),
            ContextBudgetClaim(name="second", desired_tokens=2),
            ContextBudgetClaim(name="third", desired_tokens=2),
        ),
    )
    assert allocate_context_budget(**kwargs).context_allocations == allocate_context_budget(**kwargs).context_allocations

    with pytest.raises(ValueError, match="duplicate"):
        allocate_context_budget(
            window_tokens=4_000,
            output_reserve_tokens=500,
            safety_reserve_tokens=300,
            runtime_reserve_tokens=200,
            minimum_message_tokens=100,
            claims=(
                ContextBudgetClaim(name="same", desired_tokens=1),
                ContextBudgetClaim(name="same", desired_tokens=2),
            ),
        )


def test_fixed_reserves_fail_closed_when_the_request_cannot_fit():
    tool = ToolSchema(
        name="large",
        description="schema " * 1_000,
        parameters={"type": "object", "properties": {}},
    )
    assert estimate_tool_schema_tokens((tool,)) > 0

    with pytest.raises(ContextOverflowError):
        allocate_context_budget(
            window_tokens=1_000,
            output_reserve_tokens=500,
            safety_reserve_tokens=300,
            runtime_reserve_tokens=100,
            minimum_message_tokens=100,
            tools=(tool,),
        )


def test_trimming_keeps_system_developer_and_latest_complete_turn():
    messages = (
        AgentMessage(role=MessageRole.SYSTEM, content="system"),
        AgentMessage(role=MessageRole.DEVELOPER, content="policy"),
        AgentMessage(role=MessageRole.USER, content="old"),
        AgentMessage(role=MessageRole.ASSISTANT, content="old answer"),
        AgentMessage(role=MessageRole.USER, content="latest"),
    )
    required = (messages[0], messages[1], messages[-1])
    trimmed = trim_agent_messages_by_turn(
        messages,
        estimate_agent_messages_tokens(required),
    )

    assert trimmed.messages == required
    assert trimmed.dropped_count == 2
    assert trimmed.overflow_tokens == 0


def test_trimming_never_drops_host_compacted_conversation_summary():
    summary = AgentMessage(
        role=MessageRole.USER,
        content="host summary",
        origin=MessageOrigin.HOST_CONTEXT,
        attributes={"conversation_summary": True},
    )
    messages = (
        summary,
        AgentMessage(role=MessageRole.USER, content="old"),
        AgentMessage(role=MessageRole.ASSISTANT, content="old answer"),
        AgentMessage(role=MessageRole.USER, content="latest"),
    )
    required = (summary, messages[-1])

    trimmed = trim_agent_messages_by_turn(
        messages,
        estimate_agent_messages_tokens(required),
    )

    assert trimmed.messages == required
    assert trimmed.dropped_count == 2
    assert trimmed.overflow_tokens == 0


def test_host_only_message_metadata_is_not_charged_to_provider_budget():
    visible = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="visible outline context",
        origin=MessageOrigin.HOST_CONTEXT,
    )
    with_receipt = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="visible outline context",
        origin=MessageOrigin.HOST_CONTEXT,
        host_metadata={
            "writing_outline_sources": [{
                "outlineId": "outline-1",
                "text": "大纲" * 20_000,
            }],
        },
    )

    assert estimate_agent_messages_tokens((with_receipt,)) == (
        estimate_agent_messages_tokens((visible,))
    )
