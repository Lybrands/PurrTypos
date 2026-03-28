"""
Tool router embedding query builder — port of electron/toolRouterQueryText.js.
"""

from __future__ import annotations

import re

VECTOR_HISTORY_ROUNDS = 3
TURN_CONTENT_MAX = 500
SUBAGENT_STAGE_TASK_MAX = 2000


def _trim_for_embed(s: str, max_len: int = TURN_CONTENT_MAX) -> str:
    t = re.sub(r"\s+", " ", (s or "")).strip()
    if not t:
        return ""
    return t if len(t) <= max_len else t[:max_len] + "…"


def build_tool_router_embedding_query(messages: list[dict]) -> str:
    if not messages:
        return ""

    linear = [m for m in messages if m and m.get("role") in ("user", "assistant")]
    if not linear:
        return ""

    last_user_idx = -1
    for i in range(len(linear) - 1, -1, -1):
        if linear[i]["role"] == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return ""

    current_question = _trim_for_embed(linear[last_user_idx].get("content", ""))
    if not current_question:
        return ""

    before = linear[:last_user_idx]
    rounds: list[dict[str, str]] = []
    i = 0
    while i < len(before):
        m = before[i]
        if m["role"] != "user":
            i += 1
            continue
        u = _trim_for_embed(m.get("content", ""))
        nxt = before[i + 1] if i + 1 < len(before) else None
        a = _trim_for_embed(nxt.get("content", "")) if nxt and nxt["role"] == "assistant" else ""
        rounds.append({"user": u, "assistant": a})
        if nxt and nxt["role"] == "assistant":
            i += 1
        i += 1

    tail_rounds = rounds[-VECTOR_HISTORY_ROUNDS:]

    lines = ["【对话历史】"]
    if not tail_rounds:
        lines.append("（无）")
    else:
        for idx, r in enumerate(tail_rounds):
            lines.append(f"第{idx + 1}轮")
            lines.append(f"用户：{r['user'] or '（空）'}")
            lines.append(f"助手：{r['assistant'] or '（无）'}")
            lines.append("")
    lines.append("【当前提问】")
    lines.append(current_question)

    return "\n".join(lines).strip()


def build_subagent_stage_router_query(
    pipeline_messages: list[dict], stage_user_content: str
) -> str:
    base = build_tool_router_embedding_query(pipeline_messages or [])
    task = _trim_for_embed(stage_user_content, SUBAGENT_STAGE_TASK_MAX)
    if not task:
        return base
    if not base:
        return "\n".join(["【对话历史】", "（无）", "", "【当前提问】", task])
    return f"{base}\n\n【专家阶段任务】\n{task}"
