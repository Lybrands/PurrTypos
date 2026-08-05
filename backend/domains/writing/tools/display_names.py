"""Localized user-facing names for Writing domain tools."""

from __future__ import annotations

from typing import Mapping


def _names(zh_cn: str, en_us: str) -> Mapping[str, str]:
    return {"zh-CN": zh_cn, "en-US": en_us}


WRITING_TOOL_DISPLAY_NAMES: Mapping[str, Mapping[str, str]] = {
    "addForeshadowing": _names("添加伏笔", "Add Foreshadowing"),
    "addSparkIdea": _names("添加设定", "Add Story Note"),
    "archiveMemory": _names("归档长期记忆", "Archive Long-term Memory"),
    "batchGetChapterContents": _names("查看多章内容", "Read Multiple Chapters"),
    "createCharacter": _names("创建人物", "Create Character"),
    "createMemory": _names("创建长期记忆", "Create Long-term Memory"),
    "createSettingEntity": _names("创建世界设定", "Create World Setting"),
    "createWritingChapter": _names("创建章节", "Create Chapter"),
    "deleteCharacter": _names("删除人物", "Delete Character"),
    "deleteSettingEntity": _names("删除世界设定条目", "Delete World Setting"),
    "deleteSparkIdea": _names("删除设定", "Delete Story Note"),
    "editChapterContent": _names("编辑章节内容", "Edit Chapter Content"),
    "editGlobalOutline": _names("编辑总纲", "Edit Master Outline"),
    "editStoryBackground": _names("编辑小说背景", "Edit Story Background"),
    "getBookCharacters": _names("查看人物信息", "Read Character Profiles"),
    "getBookStyle": _names("查看风格基调", "Read Writing Style"),
    "getChapterContent": _names("查看章节内容", "Read Chapter Content"),
    "getGlobalOutline": _names("查看总纲", "Read Master Outline"),
    "getSettingEntities": _names("查看世界设定详情", "Read World Settings"),
    "getStoryBackground": _names("查看小说背景", "Read Story Background"),
    "getStoryHealthDashboard": _names(
        "查看故事健康度仪表盘", "View Story Health Dashboard"
    ),
    "getWritingStatsDashboard": _names(
        "查看写作统计仪表盘", "View Writing Stats Dashboard"
    ),
    "linkMemories": _names("关联长期记忆", "Link Long-term Memories"),
    "listBookCharacters": _names("查看人物列表", "List Characters"),
    "listOutlines": _names("查看大纲列表", "List Outlines"),
    "listSettingEntities": _names("查看世界设定列表", "List World Settings"),
    "listWritingChapters": _names("查看章节目录", "List Chapters"),
    "queryOutline": _names("查看大纲详情", "Read Outline Details"),
    "resolveForeshadowing": _names("回收伏笔", "Resolve Foreshadowing"),
    "searchMemories": _names("检索长期记忆", "Search Long-term Memory"),
    "searchSparkIdeas": _names("检索设定", "Search Story Notes"),
    "updateCharacter": _names("更新人物设定", "Update Character"),
    "updateMemory": _names("更新长期记忆", "Update Long-term Memory"),
    "updateOutline": _names("更新大纲", "Update Outline"),
    "updateSettingEntity": _names("更新世界设定", "Update World Setting"),
    "updateSparkIdea": _names("更新设定", "Update Story Note"),
}


__all__ = ["WRITING_TOOL_DISPLAY_NAMES"]
