"""Buffered-response validation and deterministic final-response fallbacks."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from purra.contracts import AgentMessage, MessageRole


_DEFERRED_PREFIX_HOLD_LIMIT = 240
_DECLINED_FINAL_RESPONSE_ZH = "您已拒绝审批；操作未执行，相关数据仍保留。"
_DECLINED_FINAL_RESPONSE_EN = (
    "You rejected the approval. The operation was not executed, and the "
    "related data remains unchanged."
)
_FAILED_TOOL_FINAL_RESPONSE_ZH = (
    "工具步骤未能完成，本轮没有生成可应用的正式结果。已完成的前序结果仍会保留，"
    "请重试；系统没有把未执行的工具文本或参数 JSON 当作成功结果。"
)
_FAILED_TOOL_FINAL_RESPONSE_EN = (
    "The tool step did not complete, so this run produced no applicable formal "
    "result. Earlier completed work remains available; please retry. Plain-text "
    "tool markup or argument JSON was not treated as a successful result."
)
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_TEXTUAL_TOOL_PROTOCOL_MARKER = re.compile(
    r"<\s*/?\s*(?:"
    r"tool(?:\s*[_-]?\s*(?:c(?:a(?:l(?:l)?)?)?)?)?(?=\s|>|/|$)"
    r"|function\s*="
    r"|parameter\s*="
    r")",
    re.IGNORECASE,
)
_TOP_LEVEL_NUMBERED_ITEM = re.compile(
    r"^(?P<number>[1-9][0-9]*)[.\u3001\uff0e)]\s+\S",
    re.MULTILINE,
)
_DEFERRED_RESPONSE_STARTERS = (
    "好的，让我",
    "好的,让我",
    "好，让我",
    "好,让我",
    "让我",
    "我先",
    "我会先",
    "我将先",
    "接下来我",
    "首先我",
    "let me",
    "i'll first",
    "i will first",
)
_DEFERRED_ACTION_ZH = re.compile(
    r"^(?:好的?[，,。.!！]?)?"
    r"(?:让我|我(?:会|将)?(?:先|现在|马上|接下来)|接下来我|首先我)"
    r"[^：:\n]{0,100}"
    r"(?:查看|检查|读取|了解|分析|梳理|确认|搜索|查询|获取)"
)
_DEFERRED_DELIVERY_ZH = re.compile(
    r"(?:然后|再|随后|之后|稍后|马上)"
    r"[^：:\n]{0,100}"
    r"(?:(?:给|为|向)(?:你|您)?[^：:\n]{0,12})?"
    r"(?:提供|给出|提出|生成|制定|整理|反馈|回复|回答)"
)
_DEFERRED_ACTION_EN = re.compile(
    r"^(?:okay[,!.]?\s*)?"
    r"(?:let me|i(?:'ll| will)?\s+(?:first|now)|first[, ]+i(?:'ll| will))"
    r"[^:\n]{0,140}"
    r"(?:check|inspect|read|review|analy[sz]e|look up|search|retrieve)"
    r"[^:\n]{0,140}"
    r"(?:then|after that|afterwards|later)"
    r"[^:\n]{0,100}"
    r"(?:provide|give|prepare|create|draft|reply|answer)",
    re.IGNORECASE,
)


def is_textual_tool_call(content: str) -> bool:
    """Recognize inert text that imitates a structured tool protocol."""

    return bool(_TEXTUAL_TOOL_PROTOCOL_MARKER.search(str(content or "")))


def is_unstructured_tool_output(content: str) -> bool:
    """Reject inert protocol markup and JSON-shaped tool arguments."""

    normalized = str(content or "").strip()
    if is_textual_tool_call(normalized):
        return True
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3:
            normalized = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(normalized)
    except ValueError:
        return False
    return isinstance(value, dict) and len(value) >= 2


def top_level_numbered_items(content: str) -> tuple[int, ...]:
    return tuple(
        int(match.group("number"))
        for match in _TOP_LEVEL_NUMBERED_ITEM.finditer(str(content or ""))
    )


def exact_item_count_repair_guidance(expected_count: int) -> str:
    return (
        f"Rewrite the complete answer with exactly {expected_count} top-level "
        "items. Each item must start at column zero with consecutive Arabic "
        f"markers 1. through {expected_count}. Use bullets, not numbered "
        "sublists, for details inside an item. Do not add another top-level "
        "table, list, appendix, or optional item."
    )


def response_constraint_repair_guidance(requirements: Sequence[str]) -> str:
    joined = "\n".join(
        f"- {str(requirement).strip()}"
        for requirement in requirements
        if str(requirement).strip()
    )
    return (
        "The preceding final response was withheld because it violated one "
        "or more host-owned response constraints. Rewrite the complete answer "
        "to satisfy every requirement below:\n"
        f"{joined}\n"
        "Preserve the user's requested language and all other host "
        "instructions. Do not make a tool call. Return the answer only."
    )


def should_hold_potential_deferred_response(content: str) -> bool:
    normalized = " ".join(str(content or "").strip().lower().split())
    if not normalized or len(normalized) > _DEFERRED_PREFIX_HOLD_LIMIT:
        return False
    return any(
        starter.startswith(normalized) or normalized.startswith(starter)
        for starter in _DEFERRED_RESPONSE_STARTERS
    )


def is_deferred_action_only_response(content: str) -> bool:
    normalized = str(content or "").strip()
    if (
        not normalized
        or len(normalized) > _DEFERRED_PREFIX_HOLD_LIMIT
        or "\n" in normalized
        or ":" in normalized
        or "：" in normalized
    ):
        return False
    return bool(
        (
            _DEFERRED_ACTION_ZH.search(normalized)
            and _DEFERRED_DELIVERY_ZH.search(normalized)
        )
        or _DEFERRED_ACTION_EN.search(normalized)
    )


def declined_final_response(messages: Sequence[AgentMessage]) -> str:
    return _localized_response(
        messages,
        zh=_DECLINED_FINAL_RESPONSE_ZH,
        en=_DECLINED_FINAL_RESPONSE_EN,
    )


def failed_tool_final_response(messages: Sequence[AgentMessage]) -> str:
    return _localized_response(
        messages,
        zh=_FAILED_TOOL_FINAL_RESPONSE_ZH,
        en=_FAILED_TOOL_FINAL_RESPONSE_EN,
    )


def _localized_response(
    messages: Sequence[AgentMessage],
    *,
    zh: str,
    en: str,
) -> str:
    user_text = next(
        (
            str(message.content or "")
            for message in reversed(messages)
            if message.role is MessageRole.USER
        ),
        "",
    )
    return zh if _CJK_CHARACTER.search(user_text) else en


__all__ = [
    "declined_final_response",
    "exact_item_count_repair_guidance",
    "failed_tool_final_response",
    "is_deferred_action_only_response",
    "is_textual_tool_call",
    "is_unstructured_tool_output",
    "response_constraint_repair_guidance",
    "should_hold_potential_deferred_response",
    "top_level_numbered_items",
]
