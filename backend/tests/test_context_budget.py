from __future__ import annotations

import pytest

from agent_core.context_budget import estimate_agent_messages_tokens
from agent_core.contracts import AgentMessage, MessageRole, ToolCall
from utils.context_budget import (
    allocate_context_budget,
    context_window_tokens,
    estimate_messages_tokens,
    estimate_text_tokens,
    trim_messages_by_turn,
)


def test_context_window_mapping_and_default():
    assert context_window_tokens("32k") == 32_000
    assert context_window_tokens("128k") == 128_000
    assert context_window_tokens("200k") == 200_000
    assert context_window_tokens("300K") == 300_000
    assert context_window_tokens("1m") == 1_000_000
    with pytest.raises(ValueError, match="不支持的上下文窗口"):
        context_window_tokens("unexpected")


def test_allocation_spends_from_one_window():
    plan = allocate_context_budget(
        context_window="200k",
        output_reserve_tokens=8192,
        tools=[{
            "type": "function",
            "function": {
                "name": "searchMemories",
                "description": "Search long-term memory",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        has_memory=True,
        has_associated_context=True,
    )

    assert (
        plan.provider_input_tokens
        + plan.runtime_reserve_tokens
        + plan.tool_schema_tokens
        + plan.output_reserve_tokens
        + plan.safety_reserve_tokens
    ) == plan.window_tokens
    assert (
        plan.memory_context_tokens
        + plan.associated_context_tokens
        + plan.minimum_message_tokens
    ) <= plan.provider_input_tokens


def test_tool_schemas_and_output_reduce_available_input():
    base = allocate_context_budget(
        context_window="200k",
        output_reserve_tokens=4096,
        tools=[],
        has_memory=False,
        has_associated_context=False,
    )
    expensive = allocate_context_budget(
        context_window="200k",
        output_reserve_tokens=16_384,
        tools=[{
            "type": "function",
            "function": {
                "name": "largeTool",
                "description": "schema " * 4000,
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        has_memory=False,
        has_associated_context=False,
    )

    assert expensive.tool_schema_tokens > 0
    assert expensive.provider_input_tokens < base.provider_input_tokens


def test_trim_keeps_system_and_latest_complete_tool_turn():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    newest_turn = [messages[0], *messages[3:]]
    budget = estimate_messages_tokens(newest_turn)

    trimmed = trim_messages_by_turn(messages, budget)

    assert trimmed.messages == newest_turn
    assert trimmed.dropped_count == 2
    assert trimmed.overflow_tokens == 0


def test_oversized_latest_user_turn_reports_overflow_without_slicing():
    messages = [{"role": "user", "content": "x" * 1000}]

    trimmed = trim_messages_by_turn(messages, 10)

    assert trimmed.messages == messages
    assert trimmed.overflow_tokens > 0


def test_old_tool_protocol_group_is_removed_as_one_turn():
    messages = [
        {"role": "user", "content": "old"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "old-call", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "old-call", "content": "old-result"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "new-answer"},
    ]
    newest_turn = messages[3:]

    trimmed = trim_messages_by_turn(
        messages,
        estimate_messages_tokens(newest_turn),
    )

    assert trimmed.messages == newest_turn
    assert all(row.get("tool_call_id") != "old-call" for row in trimmed.messages)


def test_shared_estimator_handles_mixed_text_monotonically():
    english = estimate_text_tokens("the pendant glows at midnight")
    mixed = estimate_text_tokens("玉佩 " + "the pendant glows at midnight")

    assert 0 < english < len("the pendant glows at midnight")
    assert mixed > english
    assert estimate_text_tokens("かな한글🙂") >= len("かな한글🙂")


def test_developer_messages_are_never_trimmed_as_old_history():
    messages = [
        {"role": "developer", "content": "mandatory policy"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new"},
    ]
    required = [messages[0], messages[-1]]

    trimmed = trim_messages_by_turn(messages, estimate_messages_tokens(required))

    assert trimmed.messages == required


def test_core_tool_continuation_estimate_covers_legacy_provider_wire():
    tool_call = ToolCall(
        id="call-search-1",
        name="searchMemories",
        arguments_json='{"query":"jade pendant"}',
    )
    typed_messages = [
        AgentMessage(
            role=MessageRole.ASSISTANT,
            content="",
            thinking="I should inspect the memory index first.",
            tool_calls=(tool_call,),
        ),
        AgentMessage(
            role=MessageRole.TOOL,
            content='{"success":true,"matches":["chapter 3"]}',
            tool_call_id=tool_call.id,
        ),
    ]
    legacy_provider_messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "I should inspect the memory index first.",
            "tool_calls": [{
                "id": "call-search-1",
                "type": "function",
                "function": {
                    "name": "searchMemories",
                    "arguments": '{"query":"jade pendant"}',
                },
            }],
        },
        {
            "role": "tool",
            "content": '{"success":true,"matches":["chapter 3"]}',
            "tool_call_id": "call-search-1",
        },
    ]

    assert estimate_agent_messages_tokens(typed_messages) >= (
        estimate_messages_tokens(legacy_provider_messages)
    )
