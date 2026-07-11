"""
``/ai/chat/stream`` endpoint 的纯函数辅助。

把原先内联在 ``routers.ai.chat_stream`` 那个 ~300 行 async generator 里、与
I/O 无关的判定 / 拼装 / 消息改写逻辑抽出来，便于单测，也让 endpoint 本身回归
"瘦编排"。这里**不做**任何网络 / 数据库 / 流式 I/O。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from utils.streaming import append_model_content


def build_chat_request_params(
    model: str,
    rest: dict[str, Any],
    base_url: str,
    temperature: Any = None,
    tools: list[dict] | None = None,
) -> dict[str, Any]:
    """拼装传给 provider 的请求参数。

    ``temperature`` 仅在非 None 时带上；``tools`` 仅在非空时带上（与原 endpoint
    中主调用 / 子专家两处拼装行为一致）。
    """
    params: dict[str, Any] = {"model": model, **rest, "baseURL": base_url}
    if temperature is not None:
        params["temperature"] = temperature
    if tools:
        params["tools"] = tools
    return params


def valid_named_tool_calls(tool_calls: list[dict] | None) -> list[dict]:
    """只保留 ``function.name`` 非空的 tool_call（过滤掉流式过程中还没拼出名字的占位项）。"""
    return [
        tc
        for tc in (tool_calls or [])
        if (tc.get("function") or {}).get("name")
    ]


def inject_system_prompt(messages: list[dict], injection: str) -> None:
    """把 ``injection`` 前插到首个 system 消息，没有 system 消息则在开头插一条。

    **原地修改** ``messages``（与原 endpoint 行为一致）。``injection`` 为空时不动。
    """
    if not injection:
        return
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            cur = str(m.get("content") or "")
            m["content"] = f"{injection}\n\n{cur}" if cur else injection
            return
    messages.insert(0, {"role": "system", "content": injection})


def last_user_message_text(messages: list[dict] | None) -> str:
    """取最后一条 user 消息的文本内容；没有则返回空串。"""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


@dataclass
class ChunkOutcome:
    """``StreamAccumulator.process_chunk`` 的结果。

    ``events`` 是本 chunk 需要原样 yield 给前端的 SSE 事件（``thinkingDelta`` /
    ``delta``）；``finish_reason`` 是该 chunk 的结束原因（无 choice 时为 None）；
    ``has_choice`` 标记本 chunk 是否含有效 choice（用于复刻原先 ``continue`` 行为）。
    """

    events: list[dict] = field(default_factory=list)
    todo_events: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    has_choice: bool = False


class RuntimeTodoExtractor:
    """Extract hidden ``<agent_todos>{...}</agent_todos>`` events from content deltas."""

    START = "<agent_todos>"
    END = "</agent_todos>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        if not text:
            return "", []
        self._buffer += text
        visible: list[str] = []
        todos: list[dict[str, Any]] = []

        while self._buffer:
            if not self._inside:
                start_idx = self._buffer.find(self.START)
                if start_idx < 0:
                    flush_len = self._safe_flush_len(self._buffer)
                    if flush_len <= 0:
                        break
                    visible.append(self._buffer[:flush_len])
                    self._buffer = self._buffer[flush_len:]
                    continue
                if start_idx > 0:
                    visible.append(self._buffer[:start_idx])
                self._buffer = self._buffer[start_idx + len(self.START):]
                self._inside = True
                continue

            end_idx = self._buffer.find(self.END)
            if end_idx < 0:
                break
            raw = self._buffer[:end_idx].strip()
            parsed = self._parse_payload(raw)
            if parsed is not None:
                todos.append(parsed)
            self._buffer = self._buffer[end_idx + len(self.END):]
            self._inside = False

        return "".join(visible), todos

    def _safe_flush_len(self, text: str) -> int:
        marker_start = text.rfind("<")
        if marker_start < 0:
            return len(text)
        suffix = text[marker_start:]
        if self.START.startswith(suffix):
            return marker_start
        return len(text)

    def _parse_payload(self, raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


class StreamAccumulator:
    """累积流式 chunk 的 content / thinking / tool_calls 状态。

    只负责状态累积与"该 yield 哪些 delta 事件"的计算，**不做任何 I/O**——
    结束原因驱动的控制流（done / 工具回合）仍留在 endpoint 里。
    """

    def __init__(self, todo_extractor: RuntimeTodoExtractor | None = None) -> None:
        self.content = ""
        self.thinking = ""
        self.tool_calls: list[dict] = []
        self.todo_extractor = todo_extractor

    def process_chunk(self, chunk: dict) -> ChunkOutcome:
        choices = chunk.get("choices") or []
        if not choices:
            return ChunkOutcome(has_choice=False)

        c0 = choices[0]
        delta = c0.get("delta") or {}
        events: list[dict] = []

        content_delta = delta.get("content") or ""
        thinking_delta = delta.get("reasoning_content") or ""

        if thinking_delta:
            self.thinking += thinking_delta
            events.append({"thinkingDelta": thinking_delta})

        todo_events: list[dict[str, Any]] = []

        if content_delta:
            visible_delta = content_delta
            if self.todo_extractor:
                visible_delta, todo_events = self.todo_extractor.feed(content_delta)
            self.content, emitted = append_model_content(self.content, visible_delta)
            if emitted:
                events.append({"delta": emitted})
        else:
            # 非流式 / 一次性返回的 message.content 兜底
            msg_content = (c0.get("message") or {}).get("content")
            if isinstance(msg_content, str) and msg_content:
                if self.todo_extractor:
                    msg_content, todo_events = self.todo_extractor.feed(msg_content)
                self.content, emitted = append_model_content(
                    self.content, "", msg_content,
                )
                if emitted:
                    events.append({"delta": emitted})

        raw_tool_calls = delta.get("tool_calls")
        if raw_tool_calls and isinstance(raw_tool_calls, list):
            self.tool_calls = merge_stream_tool_calls(self.tool_calls, raw_tool_calls)

        return ChunkOutcome(
            events=events,
            todo_events=todo_events,
            finish_reason=c0.get("finish_reason"),
            has_choice=True,
        )


def build_tool_results_display(
    tool_results: list[dict], valid_calls: list[dict]
) -> list[dict]:
    """把工具执行结果整理成前端展示用的精简结构（带工具名）。"""
    display: list[dict] = []
    for r in tool_results:
        tc_name = next(
            (
                (tc.get("function") or {}).get("name", "")
                for tc in valid_calls
                if tc.get("id") == r.get("tool_call_id")
            ),
            "",
        )
        display.append({
            "tool_call_id": r.get("tool_call_id"),
            "name": tc_name,
            "content": r.get("content"),
        })
    return display


def build_tool_round_messages(
    valid_calls: list[dict],
    content: str,
    thinking: str,
    tool_results: list[dict],
) -> list[dict]:
    """构造一个工具回合后要追加进历史的消息序列：``[assistant, *tool]``。"""
    asst_msg: dict[str, Any] = {"role": "assistant", "tool_calls": valid_calls}
    if content:
        asst_msg["content"] = content
    if thinking:
        asst_msg["reasoning_content"] = thinking
    out: list[dict] = [asst_msg]
    for r in tool_results:
        out.append({
            "role": "tool",
            "tool_call_id": r.get("tool_call_id"),
            "content": r.get("content", ""),
        })
    return out


def merge_stream_tool_calls(
    accumulated: list[dict], delta_tool_calls: list[dict] | None
) -> list[dict]:
    """按 ``index`` 把增量 tool_calls deltas 合并进运行中的列表。"""
    if not delta_tool_calls:
        return accumulated
    result = list(accumulated)
    for dtc in delta_tool_calls:
        idx = dtc.get("index", 0)
        while len(result) <= idx:
            result.append({})
        cur = result[idx]
        if "id" not in cur and dtc.get("id") is not None:
            cur["id"] = dtc["id"]
        if "type" not in cur and dtc.get("type") is not None:
            cur["type"] = dtc["type"]
        fn_delta = dtc.get("function") or {}
        fn_cur = cur.setdefault("function", {})
        if fn_delta.get("name") is not None:
            fn_cur["name"] = fn_delta["name"]
        if fn_delta.get("arguments") is not None:
            fn_cur["arguments"] = fn_cur.get("arguments", "") + fn_delta["arguments"]
        result[idx] = cur
    return result
