"""
Settings CRUD – key/value store, port from database.js.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

BOOL_SETTINGS_KEYS = ["sync_outline_chapter"]
STRING_SETTINGS_KEYS = ["ai_system_prompt", "ai_model_configs", "ai_agent_mode"]
SETTINGS_KEYS = [*BOOL_SETTINGS_KEYS, *STRING_SETTINGS_KEYS]

DEFAULT_SYSTEM_PROMPT = (
    "你是一名小说写作智能体，使用 ReAct 工作流完成任务。\n"
    "\n"
    "请遵循以下流程：\n"
    "1) Thought：先用 1-3 句明确目标、约束与缺失信息；\n"
    "2) Action：若信息不足，优先调用工具获取证据（章节、设定、大纲、记忆）；禁止臆造未检索到的事实；\n"
    "3) Observation：简要记录工具返回的关键结论，并判断是否满足继续写作条件；\n"
    "4) Reflection：若冲突或信息不足，先向用户澄清，再继续；\n"
    "5) Final：给出可执行结果（提纲、改写片段、章节草稿或明确下一步）。\n"
    "\n"
    "行为约束：\n"
    "- 以“先分析、后执行、可追溯”为原则，避免一次性无依据长篇输出；\n"
    "- 涉及改写正文或写入时，先说明将修改的范围与意图；\n"
    "- 保持人物动机、时间线、世界观一致，发现冲突需显式提示；\n"
    "- 输出结构优先使用：结论 / 依据 / 下一步。"
)


def _parse_ai_model_configs(raw: str | None) -> list[Any]:
    if raw is None or raw == "":
        return []
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def get_settings(db: DatabaseConnection) -> dict[str, Any]:
    kv: dict[str, str | None] = {}
    for key in SETTINGS_KEYS:
        row = await db.fetch_one(
            "SELECT value FROM settings WHERE key = ?", [key]
        )
        kv[key] = row["value"] if row else None
    return {
        "sync_outline_chapter": kv["sync_outline_chapter"] == "1",
        "ai_system_prompt": kv["ai_system_prompt"]
        if kv["ai_system_prompt"] is not None
        else DEFAULT_SYSTEM_PROMPT,
        "ai_model_configs": _parse_ai_model_configs(kv["ai_model_configs"]),
        "ai_agent_mode": (
            "subagent" if kv["ai_agent_mode"] == "subagent" else "legacy"
        ),
    }


async def set_settings(db: DatabaseConnection, data: dict[str, Any]) -> None:
    for key in BOOL_SETTINGS_KEYS:
        if key in data:
            val = data[key]
            if isinstance(val, bool):
                store = "1" if val else "0"
            else:
                store = "1" if val == "1" else "0"
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [key, store],
            )
    for key in STRING_SETTINGS_KEYS:
        if key in data:
            val = data[key]
            if key == "ai_model_configs" and isinstance(val, list):
                store = json.dumps(val)
            else:
                store = str(val)
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [key, store],
            )
