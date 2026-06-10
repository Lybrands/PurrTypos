"""
routers.ai.chat_stream 抽出的纯函数辅助（utils.chat_stream）的特征测试。

这些断言刻画的是**重构前 endpoint 内联逻辑的既有行为**，作为后续把逻辑搬出去/
继续拆分时的回归网。
"""

from __future__ import annotations

from utils.chat_stream import (
    StreamAccumulator,
    build_chat_request_params,
    build_tool_results_display,
    build_tool_round_messages,
    inject_system_prompt,
    last_user_message_text,
    merge_stream_tool_calls,
    resolve_chat_modes,
    valid_named_tool_calls,
)


def _content_chunk(text: str, finish_reason=None) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


class TestBuildChatRequestParams:
    def test_minimal(self):
        p = build_chat_request_params("m1", {}, "https://api")
        assert p == {"model": "m1", "baseURL": "https://api"}

    def test_rest_merged_and_temperature_when_present(self):
        p = build_chat_request_params(
            "m1", {"top_p": 0.9}, "https://api", temperature=0.5,
        )
        assert p == {
            "model": "m1",
            "top_p": 0.9,
            "baseURL": "https://api",
            "temperature": 0.5,
        }

    def test_temperature_zero_is_kept(self):
        # 0 是有效温度，不能被当成"未设置"丢掉
        p = build_chat_request_params("m1", {}, "u", temperature=0)
        assert p["temperature"] == 0

    def test_tools_only_when_nonempty(self):
        assert "tools" not in build_chat_request_params("m", {}, "u", tools=[])
        assert "tools" not in build_chat_request_params("m", {}, "u", tools=None)
        tools = [{"type": "function"}]
        assert build_chat_request_params("m", {}, "u", tools=tools)["tools"] == tools


class TestResolveChatModes:
    def test_plain_chat(self):
        modes = resolve_chat_modes("", "", "", None, None)
        assert modes.is_writing_expert_book is False
        assert modes.is_collab is False
        assert modes.should_inject_writing_prompt is False

    def test_expert_book_triggers_writing_expert_and_prompt(self):
        modes = resolve_chat_modes(None, "Expert", None, "book1", None)
        assert modes.is_writing_expert_book is True
        assert modes.should_inject_writing_prompt is True

    def test_subagent_role_suppresses_writing_prompt(self):
        # 单次子专家调用（带 subagentRole）不应再注入主写作 system prompt
        modes = resolve_chat_modes("subagent", "", None, "book1", "polish")
        assert modes.is_writing_expert_book is True
        assert modes.should_inject_writing_prompt is False

    def test_expert_without_book_is_not_writing_expert(self):
        modes = resolve_chat_modes("", "expert", None, None, None)
        assert modes.is_writing_expert_book is False
        assert modes.should_inject_writing_prompt is False

    def test_collab_via_either_field(self):
        assert resolve_chat_modes("", "collab", "", None, None).is_collab is True
        assert resolve_chat_modes("", "", "collab", None, None).is_collab is True

    def test_fields_are_normalized(self):
        modes = resolve_chat_modes("  SubAgent ", "  EXPERT ", None, "b", None)
        assert modes.agent_mode == "subagent"
        assert modes.chat_agent_mode == "expert"


class TestValidNamedToolCalls:
    def test_filters_unnamed_and_none(self):
        tcs = [
            {"id": "a", "function": {"name": "getX"}},
            {"id": "b", "function": {"name": ""}},
            {"id": "c", "function": {}},
            {"id": "d"},
        ]
        out = valid_named_tool_calls(tcs)
        assert [t["id"] for t in out] == ["a"]

    def test_empty_and_none(self):
        assert valid_named_tool_calls([]) == []
        assert valid_named_tool_calls(None) == []


class TestInjectSystemPrompt:
    def test_prepends_to_existing_system(self):
        messages = [
            {"role": "system", "content": "原始"},
            {"role": "user", "content": "hi"},
        ]
        inject_system_prompt(messages, "注入")
        assert messages[0]["content"] == "注入\n\n原始"
        assert len(messages) == 2

    def test_inserts_when_no_system(self):
        messages = [{"role": "user", "content": "hi"}]
        inject_system_prompt(messages, "注入")
        assert messages[0] == {"role": "system", "content": "注入"}
        assert messages[1]["role"] == "user"

    def test_empty_system_content_becomes_injection(self):
        messages = [{"role": "system", "content": ""}]
        inject_system_prompt(messages, "注入")
        assert messages[0]["content"] == "注入"

    def test_empty_injection_is_noop(self):
        messages = [{"role": "user", "content": "hi"}]
        inject_system_prompt(messages, "")
        assert messages == [{"role": "user", "content": "hi"}]

    def test_only_first_system_touched(self):
        messages = [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
        ]
        inject_system_prompt(messages, "X")
        assert messages[0]["content"] == "X\n\nA"
        assert messages[1]["content"] == "B"


class TestLastUserMessageText:
    def test_returns_last_user(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        assert last_user_message_text(messages) == "second"

    def test_none_user(self):
        assert last_user_message_text([{"role": "assistant", "content": "x"}]) == ""
        assert last_user_message_text([]) == ""
        assert last_user_message_text(None) == ""


class TestMergeStreamToolCalls:
    def test_noop_on_empty_delta(self):
        acc = [{"id": "a"}]
        assert merge_stream_tool_calls(acc, None) is acc

    def test_accumulates_arguments_across_deltas(self):
        acc: list[dict] = []
        acc = merge_stream_tool_calls(
            acc, [{"index": 0, "id": "c1", "type": "function",
                   "function": {"name": "getX", "arguments": '{"a":'}}],
        )
        acc = merge_stream_tool_calls(
            acc, [{"index": 0, "function": {"arguments": "1}"}}],
        )
        assert acc[0]["id"] == "c1"
        assert acc[0]["function"]["name"] == "getX"
        assert acc[0]["function"]["arguments"] == '{"a":1}'

    def test_multiple_indices(self):
        acc = merge_stream_tool_calls(
            [],
            [
                {"index": 0, "id": "a", "function": {"name": "f0"}},
                {"index": 2, "id": "c", "function": {"name": "f2"}},
            ],
        )
        assert len(acc) == 3
        assert acc[0]["id"] == "a"
        # 中间被跳过的占位项从未被触碰，保持空 dict
        assert acc[1] == {}
        assert acc[2]["id"] == "c"


class TestStreamAccumulator:
    def test_no_choices_chunk(self):
        acc = StreamAccumulator()
        outcome = acc.process_chunk({"choices": []})
        assert outcome.has_choice is False
        assert outcome.events == []
        assert outcome.finish_reason is None

    def test_content_accumulates_and_emits_delta(self):
        acc = StreamAccumulator()
        o1 = acc.process_chunk(_content_chunk("Hel"))
        o2 = acc.process_chunk(_content_chunk("lo"))
        assert acc.content == "Hello"
        assert {"delta": "Hel"} in o1.events
        assert {"delta": "lo"} in o2.events

    def test_thinking_accumulates_separately(self):
        acc = StreamAccumulator()
        outcome = acc.process_chunk(
            {"choices": [{"delta": {"reasoning_content": "想"}}]}
        )
        assert acc.thinking == "想"
        assert outcome.events == [{"thinkingDelta": "想"}]
        assert acc.content == ""

    def test_non_streaming_message_content_fallback(self):
        acc = StreamAccumulator()
        outcome = acc.process_chunk(
            {"choices": [{"delta": {}, "message": {"content": "整段"}}]}
        )
        assert acc.content == "整段"
        assert {"delta": "整段"} in outcome.events

    def test_finish_reason_passthrough(self):
        acc = StreamAccumulator()
        outcome = acc.process_chunk(_content_chunk("x", finish_reason="stop"))
        assert outcome.finish_reason == "stop"

    def test_tool_calls_merged_into_state(self):
        acc = StreamAccumulator()
        acc.process_chunk({
            "choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "t1", "function": {"name": "getX", "arguments": "{}"}}
            ]}}]
        })
        assert acc.tool_calls[0]["function"]["name"] == "getX"


class TestBuildToolResultsDisplay:
    def test_maps_names_by_tool_call_id(self):
        valid_calls = [
            {"id": "c1", "function": {"name": "getX"}},
            {"id": "c2", "function": {"name": "getY"}},
        ]
        tool_results = [
            {"tool_call_id": "c2", "content": "Y-res"},
            {"tool_call_id": "c1", "content": "X-res"},
        ]
        out = build_tool_results_display(tool_results, valid_calls)
        assert out == [
            {"tool_call_id": "c2", "name": "getY", "content": "Y-res"},
            {"tool_call_id": "c1", "name": "getX", "content": "X-res"},
        ]

    def test_unknown_id_gets_empty_name(self):
        out = build_tool_results_display(
            [{"tool_call_id": "zzz", "content": "r"}],
            [{"id": "c1", "function": {"name": "getX"}}],
        )
        assert out[0]["name"] == ""


class TestBuildToolRoundMessages:
    def test_assistant_then_tools(self):
        valid_calls = [{"id": "c1", "function": {"name": "getX"}}]
        tool_results = [{"tool_call_id": "c1", "content": "res"}]
        out = build_tool_round_messages(valid_calls, "答", "想", tool_results)
        assert out[0] == {
            "role": "assistant",
            "tool_calls": valid_calls,
            "content": "答",
            "reasoning_content": "想",
        }
        assert out[1] == {"role": "tool", "tool_call_id": "c1", "content": "res"}

    def test_omits_empty_content_and_thinking(self):
        out = build_tool_round_messages(
            [{"id": "c1", "function": {"name": "f"}}], "", "", [],
        )
        assert out == [{"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "f"}}
        ]}]

    def test_tool_content_defaults_to_empty_string(self):
        out = build_tool_round_messages(
            [{"id": "c1", "function": {"name": "f"}}], "x", "",
            [{"tool_call_id": "c1"}],
        )
        assert out[1]["content"] == ""
