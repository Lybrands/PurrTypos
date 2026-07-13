"""Explicit execution policy for every Agent tool.

``SKILL.md`` defines what the model may request.  This module separately
defines what the host may execute without a human decision.  Keeping that
security boundary outside prompts and handlers makes it enforceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class ToolExecutionMode(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class ToolPolicy:
    mode: ToolExecutionMode
    title: str
    risk_level: str = "read"

    @property
    def requires_user_approval(self) -> bool:
        return self.mode is ToolExecutionMode.CONFIRM


# Security policy is deliberately exhaustive.  A newly registered tool must
# be classified here before the backend is allowed to start.
TOOL_POLICIES: dict[str, ToolPolicy] = {
    # Read-only tools
    "getBookStyle": ToolPolicy(ToolExecutionMode.READ, "读取写作风格"),
    "getChapterContent": ToolPolicy(ToolExecutionMode.READ, "读取章节正文"),
    "listWritingChapters": ToolPolicy(ToolExecutionMode.READ, "查看写作章节"),
    "batchGetChapterContents": ToolPolicy(ToolExecutionMode.READ, "批量读取章节"),
    "getBookCharacters": ToolPolicy(ToolExecutionMode.READ, "读取人物设定"),
    "listBookCharacters": ToolPolicy(ToolExecutionMode.READ, "查看人物列表"),
    "getStoryHealthDashboard": ToolPolicy(ToolExecutionMode.READ, "读取故事健康度"),
    "getWritingStatsDashboard": ToolPolicy(ToolExecutionMode.READ, "读取写作统计"),
    "searchSparkIdeas": ToolPolicy(ToolExecutionMode.READ, "检索设定与伏笔"),
    "searchMemories": ToolPolicy(ToolExecutionMode.READ, "检索长期记忆"),
    "queryOutline": ToolPolicy(ToolExecutionMode.READ, "读取大纲"),
    "getGlobalOutline": ToolPolicy(ToolExecutionMode.READ, "读取总纲"),
    "listOutlines": ToolPolicy(ToolExecutionMode.READ, "查看大纲列表"),
    "listSettingEntities": ToolPolicy(ToolExecutionMode.READ, "查看世界设定列表"),
    "getSettingEntities": ToolPolicy(ToolExecutionMode.READ, "读取世界设定"),
    "getStoryBackground": ToolPolicy(ToolExecutionMode.READ, "读取故事背景"),

    # These handlers only emit a user-reviewable diff; they do not persist it.
    "editChapterContent": ToolPolicy(ToolExecutionMode.PROPOSE, "提议修改章节", "write"),
    "updateCharacter": ToolPolicy(ToolExecutionMode.PROPOSE, "提议修改人物设定", "write"),
    "updateSettingEntity": ToolPolicy(ToolExecutionMode.PROPOSE, "提议修改世界设定", "write"),
    "editStoryBackground": ToolPolicy(ToolExecutionMode.PROPOSE, "提议修改故事背景", "write"),

    # Direct persistence requires an explicit approval card in the desktop UI.
    "createWritingChapter": ToolPolicy(ToolExecutionMode.CONFIRM, "创建写作章节", "write"),
    "createCharacter": ToolPolicy(ToolExecutionMode.CONFIRM, "创建人物", "write"),
    "deleteCharacter": ToolPolicy(ToolExecutionMode.CONFIRM, "删除人物", "destructive"),
    "addSparkIdea": ToolPolicy(ToolExecutionMode.CONFIRM, "新增设定", "write"),
    "updateSparkIdea": ToolPolicy(ToolExecutionMode.CONFIRM, "修改设定", "write"),
    "deleteSparkIdea": ToolPolicy(ToolExecutionMode.CONFIRM, "删除设定", "destructive"),
    "addForeshadowing": ToolPolicy(ToolExecutionMode.CONFIRM, "新增伏笔", "write"),
    "createMemory": ToolPolicy(ToolExecutionMode.CONFIRM, "保存长期记忆", "write"),
    "updateMemory": ToolPolicy(ToolExecutionMode.CONFIRM, "修改长期记忆", "write"),
    "archiveMemory": ToolPolicy(ToolExecutionMode.CONFIRM, "归档长期记忆", "write"),
    "linkMemories": ToolPolicy(ToolExecutionMode.CONFIRM, "关联长期记忆", "write"),
    "resolveForeshadowing": ToolPolicy(ToolExecutionMode.CONFIRM, "标记伏笔已回收", "write"),
    "editGlobalOutline": ToolPolicy(ToolExecutionMode.CONFIRM, "修改总纲", "write"),
    "updateOutline": ToolPolicy(ToolExecutionMode.CONFIRM, "修改大纲", "write"),
    "createSettingEntity": ToolPolicy(ToolExecutionMode.CONFIRM, "创建世界设定", "write"),
    "deleteSettingEntity": ToolPolicy(ToolExecutionMode.CONFIRM, "删除世界设定", "destructive"),
}


def policy_coverage(tool_names: Iterable[str]) -> tuple[set[str], set[str]]:
    """Return ``(unclassified_handlers, policies_without_handler)``."""

    registered = {str(name).strip() for name in tool_names if str(name).strip()}
    policy_names = set(TOOL_POLICIES)
    return registered - policy_names, policy_names - registered
