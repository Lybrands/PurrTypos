"""Model-backed Agent Run todo planner.

The backend no longer fabricates todo content from keyword templates. It only
decides whether a run is eligible for todos, asks the model for a strict JSON
plan, then validates that plan against local executor/tool constraints.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.task_step_executor import TaskStepValidationError, validate_task_plan_steps

logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """你是 Agent Run 的 To-dos 规划器。
只输出 JSON，不要输出 Markdown，不要解释。

你的职责：
1. 判断本轮请求是否需要 To-dos。
2. 如果需要，输出 2-8 个短 todo，标题要像 Cursor To-dos 一样简洁。
3. To-dos 必须描述本轮 agent run 会做的真实阶段，不要承诺不存在的能力。
4. 写入确认由宿主的安全执行器处理；不要生成 type=confirm 步骤。
5. executor 只能是 model、tool。
6. executor=tool 时必须提供至少一个 expectedTools，且只能从用户消息列出的可用工具中选择。

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
      "type": "read|analyze|write|review",
      "executor": "model|tool",
      "expectedTools": ["executor=tool 时必填"],
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
    if not raw:
        logger.info("[agent-run][planner] rejected plan: empty or unparsable output")
        return None
    if not _truthy(raw.get("needsTodos")):
        logger.info("[agent-run][planner] rejected plan: needsTodos is not true")
        return None
    todos_raw = raw.get("todos", raw.get("steps"))
    if not isinstance(todos_raw, list) or not (1 <= len(todos_raw) <= 8):
        logger.info("[agent-run][planner] rejected plan: todos must contain 1-8 steps")
        return None

    steps: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(todos_raw):
        if not isinstance(item, dict):
            logger.info("[agent-run][planner] rejected plan: todo item is not object")
            return None
        expected_tools = item.get("expectedTools", item.get("suggestedTools")) or []
        if not isinstance(expected_tools, list):
            logger.info("[agent-run][planner] rejected plan: expectedTools is not list")
            return None
        expected_tool_names = [str(x).strip() for x in expected_tools if str(x).strip()]
        if any(tool not in available_tool_names for tool in expected_tool_names):
            logger.info(
                "[agent-run][planner] rejected plan: unknown expected tool(s) %s",
                [tool for tool in expected_tool_names if tool not in available_tool_names],
            )
            return None

        step_type = item.get("type") or "analyze"
        executor = item.get("executor") or ("tool" if step_type == "read" else "model")
        if executor == "tool" and not expected_tool_names:
            logger.info("[agent-run][planner] rejected plan: tool step missing expectedTools")
            return None

        step_id = _clean_id(item.get("id")) or f"todo-{idx + 1}"
        if step_id in seen_ids:
            step_id = f"{step_id}-{idx + 1}"
        seen_ids.add(step_id)

        step = {
            "id": step_id,
            "title": _clean_title(item.get("title")) or f"步骤 {idx + 1}",
            "description": _clean_optional(item.get("description")),
            "type": step_type,
            "status": "pending",
            "riskLevel": item.get("riskLevel") or "read",
            "executor": executor,
            "suggestedTools": expected_tool_names,
        }
        steps.append({
            key: value
            for key, value in step.items()
            if value not in (None, "", [])
        })

    try:
        validate_task_plan_steps(steps)
    except TaskStepValidationError as exc:
        logger.info("[agent-run][planner] rejected plan: %s", exc)
        return None

    plan = {
        "title": _clean_title(raw.get("title")) or "To-dos",
        "goal": _clean_optional(raw.get("goal")),
        "status": "planned",
        "steps": steps,
    }
    logger.info("[agent-run][planner] accepted plan with %d step(s)", len(steps))
    return plan


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
    options.pop("tool_choice", None)
    # Preserve provider/model sampling constraints.  Some reasoning models only
    # accept a specific temperature, so the control plane must not invent one.
    # Prefer non-thinking JSON planning, but retry with the original capability
    # profile when a provider rejects the override.
    original_thinking = options.get("thinking")
    options["thinking"] = {"type": "disabled"}
    options["max_tokens"] = min(int(options.get("max_tokens") or 900), 1200)
    try:
        result = await create_chat_no_stream(key, messages, options, api_provider, signal)
    except Exception as first_error:
        retry_options = dict(options)
        if original_thinking is None:
            retry_options.pop("thinking", None)
        else:
            retry_options["thinking"] = original_thinking
        logger.warning(
            "[agent-run][planner] non-thinking request failed; retrying provider profile: %s",
            type(first_error).__name__,
        )
        try:
            result = await create_chat_no_stream(
                key, messages, retry_options, api_provider, signal,
            )
        except Exception as retry_error:
            raise retry_error from first_error
    content = ((result.get("message") or {}).get("content") or "").strip()
    logger.info("[agent-run][planner] raw model output: %s", content[:1000])
    decision = parse_planner_json(content)
    if decision is not None and "needsTodos" in decision and not _truthy(decision.get("needsTodos")):
        return {
            "title": _clean_title(decision.get("title")) or "直接回答",
            "goal": user_text[:160] if user_text else None,
            "status": "planned",
            "steps": [{
                "id": "respond",
                "title": "直接回答",
                "type": "review",
                "executor": "model",
                "riskLevel": "read",
            }],
        }
    return normalize_model_task_plan(
        decision,
        available_tool_names=available_tool_names,
    )


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "是", "需要"}
    return False


def _clean_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    return raw[:48]


def _clean_title(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:48]


def _clean_optional(value: Any) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:160] if cleaned else None
