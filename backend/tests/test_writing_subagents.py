"""
services.writing_subagents 的特征测试。

聚焦两个工具循环（流式 / 非流式）与两个纯 helper 的既有行为，作为把内部重复
逻辑替换成 utils.chat_stream 共享实现时的回归网。provider 与 tool executor 用
fake 替身注入。
"""

from __future__ import annotations

import asyncio

import pytest

import services.writing_subagents as ws
from services.writing_subagents import (
    _filter_tools,
    _run_non_stream_tool_loop,
    _run_streaming_tool_loop,
    _tool_result_looks_like_json_error,
)


class _FakeStream:
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _content_chunk(text: str, finish_reason=None) -> dict:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


def _tool_call_chunk(call_id: str, name: str, args: str = "{}") -> dict:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


class TestFilterTools:
    def test_keeps_only_named_tools(self):
        all_tools = [
            {"function": {"name": "getX"}},
            {"function": {"name": "getY"}},
            {"function": {}},
            {"nope": 1},
        ]
        out = _filter_tools(all_tools, {"getX"})
        assert out == [{"function": {"name": "getX"}}]


class TestToolResultLooksLikeJsonError:
    def test_detects_error_payload(self):
        assert _tool_result_looks_like_json_error('{"error": "boom"}') is True

    def test_non_error_json(self):
        assert _tool_result_looks_like_json_error('{"ok": 1}') is False

    def test_non_json_and_non_str(self):
        assert _tool_result_looks_like_json_error("plain text") is False
        assert _tool_result_looks_like_json_error("") is False
        assert _tool_result_looks_like_json_error(123) is False  # type: ignore[arg-type]


class TestRunStreamingToolLoop:
    async def test_tool_round_then_final_text(self, monkeypatch):
        rounds = [
            # 第 1 轮：流出内容后发起工具调用
            [
                _content_chunk("Hel"),
                _content_chunk("lo"),
                _tool_call_chunk("c1", "getX"),
            ],
            # 第 2 轮：携工具结果后流出最终文本并 stop
            [_content_chunk("Done", finish_reason="stop")],
        ]

        async def fake_create_chat_stream(key, messages, params, provider, signal):
            return {"stream": _FakeStream(rounds.pop(0)), "model": "m1"}

        async def fake_run_tools(calls, ctx, cb):
            cb({"toolProgress": "running"})
            return [{"tool_call_id": calls[0]["id"], "content": "RESULT"}]

        monkeypatch.setattr(ws, "create_chat_stream", fake_create_chat_stream)
        monkeypatch.setattr(ws, "executor_run_tools", fake_run_tools)

        events: list[dict] = []
        final = await _run_streaming_tool_loop(
            loop_messages=[{"role": "user", "content": "hi"}],
            stage_tools=[{"function": {"name": "getX"}}],
            tool_ctx={},
            send_chunk=events.append,
            key="k",
            api_provider="openai",
            rp={"model": "m1"},
            signal=asyncio.Event(),
            used_model="m1",
        )

        assert final == "Done"
        deltas = [
            e["writingSubagentDelta"]["delta"]
            for e in events
            if "writingSubagentDelta" in e
        ]
        assert deltas == ["Hel", "lo", "Done"]
        tool_calls_events = [e for e in events if e.get("toolCallsInProgress")]
        assert len(tool_calls_events) == 1
        assert tool_calls_events[0]["partialContent"] == "Hello"
        assert any("toolResults" in e for e in events)
        assert any(e.get("toolProgress") == "running" for e in events)

    async def test_no_tool_calls_returns_content(self, monkeypatch):
        async def fake_create_chat_stream(key, messages, params, provider, signal):
            return {
                "stream": _FakeStream([_content_chunk("hi", finish_reason="stop")]),
                "model": "m1",
            }

        monkeypatch.setattr(ws, "create_chat_stream", fake_create_chat_stream)

        events: list[dict] = []
        final = await _run_streaming_tool_loop(
            loop_messages=[{"role": "user", "content": "x"}],
            stage_tools=[],
            tool_ctx={},
            send_chunk=events.append,
            key="k",
            api_provider="openai",
            rp={"model": "m1"},
            signal=asyncio.Event(),
            used_model="m1",
        )
        assert final == "hi"


class TestRunNonStreamToolLoop:
    async def test_tool_round_then_final_text(self, monkeypatch):
        responses = [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "getX", "arguments": "{}"},
                        }
                    ],
                }
            },
            {"message": {"content": "FINAL", "tool_calls": []}},
        ]

        async def fake_create_chat_no_stream(key, messages, opts, provider, signal):
            return responses.pop(0)

        async def fake_run_tools(calls, ctx, cb):
            return [{"tool_call_id": "c1", "content": "RESULT"}]

        monkeypatch.setattr(ws, "create_chat_no_stream", fake_create_chat_no_stream)
        monkeypatch.setattr(ws, "executor_run_tools", fake_run_tools)

        events: list[dict] = []
        final = await _run_non_stream_tool_loop(
            loop_messages=[{"role": "user", "content": "hi"}],
            stage_tools=[{"function": {"name": "getX"}}],
            tool_ctx={},
            send_chunk=events.append,
            key="k",
            api_provider="openai",
            rp={"model": "m1"},
            signal=asyncio.Event(),
        )

        assert final == "FINAL"
        assert any(e.get("toolCallsInProgress") for e in events)

    async def test_no_tool_calls_returns_immediately(self, monkeypatch):
        async def fake_create_chat_no_stream(key, messages, opts, provider, signal):
            return {"message": {"content": "answer", "tool_calls": []}}

        monkeypatch.setattr(ws, "create_chat_no_stream", fake_create_chat_no_stream)

        events: list[dict] = []
        final = await _run_non_stream_tool_loop(
            loop_messages=[{"role": "user", "content": "hi"}],
            stage_tools=[],
            tool_ctx={},
            send_chunk=events.append,
            key="k",
            api_provider="openai",
            rp={"model": "m1"},
            signal=asyncio.Event(),
        )
        assert final == "answer"
        assert events == []
