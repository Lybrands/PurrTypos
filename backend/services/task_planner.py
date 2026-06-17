"""Model-backed Agent Run todo planner.

The backend no longer fabricates todo content from keyword templates. It only
decides whether a run is eligible for todos, asks the model for a strict JSON
plan, then validates that plan against local executor/tool constraints.
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.task_step_executor import TaskStepValidationError, validate_task_plan_steps


PLANNER_SYSTEM_PROMPT = """你是 Agent Run 的 To-dos 规划器。
只输出 JSON，不要输出 Markdown，不要解释。

你的职责：
1. 判断本轮请求是否需要 To-dos。
2. 如果需要，输出 2-8 个短 todo，标题要像 Cursor To-dos 一样简洁。
3. To-dos 必须描述本轮 agent run 会做的真实阶段，不要承诺不存在的能力。
4. 涉及写入、保存、删除、覆盖正文或设定时，必须包含一个 type=confirm 的确认边界。
5. executor 只能是 model、tool。
6. executor=tool 时 expectedTools 只能从用户消息列出的可用工具中选择。

JSON 结构：
{
  "needsTodos": true,
  "reason": "为什么需要 To-dos",
  "title": "短标题",
  "goal": "用户目标摘要",
  "todos": [
    {
      "id": "kebab-case-id",
      "title": "短 todo 标题",
      "type": "read|analyze|write|review|confirm",
      "executor": "model|tool",
      "expectedTools": ["可选，仅 tool"],
      "riskLevel": "read|write|destructive"
    }
  ]
}

不需要 To-dos 时输出：
{"needsTodos": false, "reason": "原因", "todos": []}
"""


def should_request_task_plan(
    *,
    user_text: str,
    book_id: Any,
    enable_agent_tools: bool,
    chat_agent_mode: str | None,
) -> bool:
    """Backend eligibility gate. Todo content itself must come from the model."""

    text = (user_text or "").strip()
    if not text or not book_id:
        return False
    mode = (chat_agent_mode or "").strip().lower()
    if mode == "ask" and not enable_agent_tools:
        return False
    if mode not in {"agent"} and not enable_agent_tools:
        return False
    return len(text) >= 8


def build_planner_messages(
    *,
    user_text: str,
    chat_agent_mode: str | None,
    available_tool_names: list[str] | set[str] | tuple[str, ...],
) -> list[dict[str, str]]:
    tools = sorted({str(x).strip() for x in available_tool_names if str(x).strip()})
    user_payload = {
        "chatAgentMode": chat_agent_mode or "",
        "userText": user_text,
        "availableTools": tools,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def parse_planner_json(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def normalize_model_task_plan(
    raw: dict[str, Any] | None,
    *,
    available_tool_names: set[str],
) -> dict[str, Any] | None:
    if not raw or raw.get("needsTodos") is not True:
        return None
    todos_raw = raw.get("todos")
    if not isinstance(todos_raw, list) or not (2 <= len(todos_raw) <= 8):
        return None

    steps: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(todos_raw):
        if not isinstance(item, dict):
            return None
        expected_tools = item.get("expectedTools", item.get("suggestedTools")) or []
        if not isinstance(expected_tools, list):
            return None
        expected_tool_names = [str(x).strip() for x in expected_tools if str(x).strip()]
        if any(tool not in available_tool_names for tool in expected_tool_names):
            return None

        step_id = _clean_id(item.get("id")) or f"todo-{idx + 1}"
        if step_id in seen_ids:
            step_id = f"{step_id}-{idx + 1}"
        seen_ids.add(step_id)

        step = {
            "id": step_id,
            "title": _clean_title(item.get("title")) or f"步骤 {idx + 1}",
            "description": _clean_optional(item.get("description")),
            "type": item.get("type") or "analyze",
            "status": "pending",
            "riskLevel": item.get("riskLevel") or "read",
            "executor": item.get("executor") or "model",
            "suggestedTools": expected_tool_names,
        }
        steps.append({
            key: value
            for key, value in step.items()
            if value not in (None, "", [])
        })

    if _has_write_risk(steps) and not any(step["type"] == "confirm" for step in steps):
        return None

    try:
        validate_task_plan_steps(steps)
    except TaskStepValidationError:
        return None

    return {
        "title": _clean_title(raw.get("title")) or "To-dos",
        "goal": _clean_optional(raw.get("goal")),
        "status": "planned",
        "steps": steps,
    }


async def generate_model_task_plan(
    *,
    key: str,
    api_provider: str,
    planner_options: dict[str, Any],
    user_text: str,
    chat_agent_mode: str | None,
    available_tool_names: set[str],
    signal: Any = None,
) -> dict[str, Any] | None:
    from services.ai_provider import create_chat_no_stream

    messages = build_planner_messages(
        user_text=user_text,
        chat_agent_mode=chat_agent_mode,
        available_tool_names=available_tool_names,
    )
    options = dict(planner_options)
    options.pop("tools", None)
    options["temperature"] = 0
    options["max_tokens"] = min(int(options.get("max_tokens") or 900), 1200)
    result = await create_chat_no_stream(key, messages, options, api_provider, signal)
    content = ((result.get("message") or {}).get("content") or "").strip()
    return normalize_model_task_plan(
        parse_planner_json(content),
        available_tool_names=available_tool_names,
    )


def _has_write_risk(steps: list[dict[str, Any]]) -> bool:
    return any(
        step.get("type") == "write" or step.get("riskLevel") in {"write", "destructive"}
        for step in steps
    )


def _clean_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    return raw[:48]


def _clean_title(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:48]


def _clean_optional(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:160] if cleaned else None
