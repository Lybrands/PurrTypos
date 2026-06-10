"""
``/ai/chat/stream`` endpoint 的纯函数辅助。

把原先内联在 ``routers.ai.chat_stream`` 那个 ~300 行 async generator 里、与
I/O 无关的判定 / 拼装 / 消息改写逻辑抽出来，便于单测，也让 endpoint 本身回归
"瘦编排"。这里**不做**任何网络 / 数据库 / 流式 I/O。
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class ChatModes:
    """从请求里解析出的对话模式判定。"""

    agent_mode: str
    chat_agent_mode: str
    is_writing_expert_book: bool
    is_collab: bool
    should_inject_writing_prompt: bool


def resolve_chat_modes(
    agent_mode: str | None,
    chat_agent_mode: str | None,
    writing_mode: str | None,
    book_id: Any,
    subagent_role: Any,
) -> ChatModes:
    """归一化各模式字段并算出派生布尔量。"""
    am = (agent_mode or "").strip().lower()
    cam = (chat_agent_mode or "").strip().lower()
    wm = (writing_mode or "").strip().lower()
    has_book = bool(book_id)

    is_writing_expert_book = bool(
        has_book and (am == "subagent" or cam in ("expert", "subagent"))
    )
    is_collab = cam == "collab" or wm == "collab"
    should_inject_writing_prompt = bool(
        has_book and not subagent_role and (cam == "expert" or am == "subagent")
    )
    return ChatModes(
        agent_mode=am,
        chat_agent_mode=cam,
        is_writing_expert_book=is_writing_expert_book,
        is_collab=is_collab,
        should_inject_writing_prompt=should_inject_writing_prompt,
    )


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
    finish_reason: str | None = None
    has_choice: bool = False


class StreamAccumulator:
    """累积流式 chunk 的 content / thinking / tool_calls 状态。

    只负责状态累积与"该 yield 哪些 delta 事件"的计算，**不做任何 I/O**——
    结束原因驱动的控制流（done / 工具回合）仍留在 endpoint 里。
    """

    def __init__(self) -> None:
        self.content = ""
        self.thinking = ""
        self.tool_calls: list[dict] = []

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

        if content_delta:
            self.content, emitted = append_model_content(self.content, content_delta)
            if emitted:
                events.append({"delta": emitted})
        else:
            # 非流式 / 一次性返回的 message.content 兜底
            msg_content = (c0.get("message") or {}).get("content")
            if isinstance(msg_content, str) and msg_content:
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
