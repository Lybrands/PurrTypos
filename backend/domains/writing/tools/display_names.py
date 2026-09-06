"""Localized user-facing names for Writing domain tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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
    "searchWritingMethods": _names("检索写作方法目录", "Search Writing Methods"),
    "readContinuationSourceSection": _names(
        "读取冻结来源章节", "Read Frozen Source Chapter"
    ),
    "updateCharacter": _names("更新人物设定", "Update Character"),
    "updateMemory": _names("更新长期记忆", "Update Long-term Memory"),
    "updateOutline": _names("更新大纲", "Update Outline"),
    "updateSettingEntity": _names("更新世界设定", "Update World Setting"),
    "updateSparkIdea": _names("更新设定", "Update Story Note"),
}


_SETTING_TYPE_LABELS = {
    "location": "地点",
    "faction": "势力",
    "item": "物品",
    "other": "其他设定",
}
_SPARK_LAYER_LABELS = {
    0: "全局设定",
    1: "大纲设定",
    2: "人物设定",
    3: "章节设定",
    "全局": "全局设定",
    "大纲": "大纲设定",
    "人物": "人物设定",
    "章节": "章节设定",
    "伏笔": "伏笔",
}
_MEMORY_KIND_LABELS = {
    "canon": "正史记忆",
    "plot": "情节记忆",
    "character": "人物记忆",
    "world": "世界观记忆",
    "foreshadowing": "伏笔记忆",
    "style": "风格记忆",
    "summary": "阶段总结",
}
_MEMORY_RELATION_LABELS = {
    "supersedes": "覆盖关系",
    "contradicts": "冲突关系",
    "supports": "佐证关系",
    "relates_to": "关联关系",
}
_MEMORY_SCOPE_LABELS = {
    "chapter": "章节范围",
    "character": "人物范围",
    "outline": "大纲范围",
}


def writing_tool_display_names(
    tool_name: str,
    state: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> Mapping[str, str]:
    """Render one safe, replayable label from host state and call arguments."""

    names = dict(WRITING_TOOL_DISPLAY_NAMES.get(str(tool_name or ""), {}))
    label = names.get("zh-CN")
    if not label:
        return names
    detail = _writing_tool_detail(str(tool_name or ""), state, arguments)
    if detail:
        names["zh-CN"] = _writing_tool_label(
            str(tool_name or ""),
            detail,
            state,
            arguments,
        )
    return names


def _writing_tool_label(
    tool_name: str,
    detail: str,
    state: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str:
    templates = {
        "getChapterContent": "查看{target}正文",
        "editChapterContent": "编辑{target}正文",
        "batchGetChapterContents": "查看{target}正文",
        "createWritingChapter": "在{target}下创建章节",
        "addForeshadowing": "在{target}中添加伏笔",
        "resolveForeshadowing": "回收{target}中的伏笔",
        "addSparkIdea": "在{target}中添加设定",
        "updateSparkIdea": "更新{target}",
        "getBookCharacters": "查看{target}的资料",
        "createCharacter": "创建{target}",
        "updateCharacter": "更新{target}的设定",
        "deleteCharacter": "删除{target}",
        "getSettingEntities": "查看{target}详情",
        "createSettingEntity": "创建{target}",
        "updateSettingEntity": "更新{target}",
        "deleteSettingEntity": "删除{target}",
        "queryOutline": "查看{target}大纲",
        "updateOutline": "更新{target}大纲",
        "searchMemories": "检索与{target}相关的长期记忆",
        "searchWritingMethods": "检索与{target}相关的写作方法",
        "createMemory": "创建{target}",
        "updateMemory": "更新{target}",
        "archiveMemory": "归档{target}",
        "linkMemories": "建立长期记忆的{target}",
        "deleteSparkIdea": "删除{target}",
        "readContinuationSourceSection": "读取{target}的冻结来源正文",
    }
    if tool_name == "searchSparkIdeas":
        query = _clean_text(arguments.get("query"))
        layer = _SPARK_LAYER_LABELS.get(arguments.get("layer"))
        chapter = _chapter_display_target(
            arguments.get("chapterTitle")
            or _chapter_title(state, arguments.get("chapterId"))
        )
        scope = (
            f"{chapter}的{layer}"
            if chapter and layer
            else chapter or layer
        )
        quoted_query = f"“{query}”" if query else ""
        scope = scope or "设定"
        return (
            f"在{scope}中检索与{quoted_query}相关的设定"
            if quoted_query
            else f"检索{scope}"
        )
    template = templates.get(tool_name)
    return template.format(target=detail) if template else detail


def _chapter_display_target(value: object) -> str | None:
    title = _clean_text(value)
    return f"《{title}》" if title else None


def _writing_tool_detail(
    tool_name: str,
    state: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str | None:
    if tool_name in {"getChapterContent", "editChapterContent"}:
        return _chapter_target(state, arguments)
    if tool_name == "batchGetChapterContents":
        return _chapter_targets(state, arguments.get("chapterIds"))
    if tool_name == "createWritingChapter":
        parent = _chapter_title(state, arguments.get("parentId"))
        return f"《{parent}》" if parent else None
    if tool_name in {"addForeshadowing", "resolveForeshadowing"}:
        chapter = _chapter_title(
            state,
            arguments.get("resolvedChapterId", arguments.get("chapterId")),
        ) or _clean_text(state.get("currentChapterTitle"))
        return f"《{chapter}》" if chapter else None
    if tool_name in {"addSparkIdea", "updateSparkIdea", "searchSparkIdeas"}:
        parts = []
        layer = _SPARK_LAYER_LABELS.get(arguments.get("layer"))
        if layer:
            parts.append(layer)
        chapter = _chapter_title(state, arguments.get("chapterId"))
        if chapter:
            parts.append(f"《{chapter}》")
        query = _clean_text(arguments.get("query"))
        if query:
            parts.append(f"“{query}”")
        return " · ".join(parts) or None
    if tool_name in {"getBookCharacters", "createCharacter", "updateCharacter"}:
        names = _text_values(arguments.get("names"))
        name = _clean_text(arguments.get("name"))
        if name:
            names = (name,)
        if names:
            return _named_targets("人物", names)
        count = _value_count(arguments.get("characterIds"))
        return f"{count} 位指定人物" if count else None
    if tool_name == "deleteCharacter":
        return "指定人物"
    if tool_name in {"getSettingEntities", "createSettingEntity", "updateSettingEntity"}:
        names = _text_values(arguments.get("names"))
        name = _clean_text(arguments.get("name"))
        if name:
            names = (name,)
        if names:
            return _named_targets("设定", names)
        setting_type = _SETTING_TYPE_LABELS.get(
            str(arguments.get("entityType") or "")
        )
        if setting_type:
            return setting_type
        count = _value_count(arguments.get("entityIds"))
        return f"{count} 条指定设定" if count else None
    if tool_name == "deleteSettingEntity":
        return "指定设定"
    if tool_name in {"queryOutline", "updateOutline"}:
        title = _clean_text(arguments.get("outlineTitle"))
        if title:
            return f"《{title}》"
        ids = arguments.get("outlineIds")
        if ids is None and arguments.get("outlineId") is not None:
            ids = (arguments.get("outlineId"),)
        targets = _outline_targets(state, ids)
        if targets:
            return targets
        renamed = _clean_text(arguments.get("title"))
        return f"重命名为《{renamed}》" if renamed else None
    if tool_name in {"searchMemories", "searchWritingMethods"}:
        query = _clean_text(arguments.get("query"))
        return f"“{query}”" if query else None
    if tool_name in {"createMemory", "updateMemory"}:
        kind = _MEMORY_KIND_LABELS.get(str(arguments.get("kind") or ""))
        scope = _MEMORY_SCOPE_LABELS.get(str(arguments.get("scopeType") or ""))
        if kind and scope:
            return f"{kind} · {scope}"
        return kind
    if tool_name == "archiveMemory":
        return "指定记忆"
    if tool_name == "linkMemories":
        return _MEMORY_RELATION_LABELS.get(str(arguments.get("relation") or ""))
    if tool_name == "deleteSparkIdea":
        return "指定设定"
    if tool_name == "readContinuationSourceSection":
        binding = state.get("continuationBinding")
        if not isinstance(binding, Mapping):
            return None
        source = _clean_text(binding.get("sourceTitle"))
        fork_title = _clean_text(binding.get("forkSectionTitle"))
        if (
            fork_title
            and str(arguments.get("sectionId") or "")
            == str(binding.get("forkSectionId") or "")
        ):
            return f"《{source}》·《{fork_title}》" if source else f"《{fork_title}》"
        return f"《{source}》" if source else None
    return None


def _clean_text(value: object, limit: int = 48) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _clean_text(item)))


def _value_count(value: object) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _named_targets(kind: str, values: Iterable[str]) -> str:
    items = tuple(values)
    visible = "、".join(f"「{item}」" for item in items[:3])
    return f"{kind}{visible}" + (f"等 {len(items)} 项" if len(items) > 3 else "")


def _chapter_title(state: Mapping[str, Any], chapter_id: object) -> str:
    key = str(chapter_id or "").strip()
    if not key:
        return ""
    chapters = state.get("writingChapters")
    if not isinstance(chapters, (list, tuple)):
        return ""
    for item in chapters:
        if isinstance(item, Mapping) and str(item.get("id") or "") == key:
            return _clean_text(item.get("title"))
    return ""


def _chapter_target(
    state: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str | None:
    title = (
        _clean_text(arguments.get("chapterTitle"))
        or _clean_text(arguments.get("title"))
        or _chapter_title(state, arguments.get("chapterId"))
        or _clean_text(state.get("currentChapterTitle"))
    )
    return f"《{title}》" if title else None


def _chapter_targets(state: Mapping[str, Any], values: object) -> str | None:
    if not isinstance(values, (list, tuple)) or not values:
        return None
    titles = tuple(
        title for value in values if (title := _chapter_title(state, value))
    )
    if not titles:
        return f"{len(values)} 章"
    visible = "、".join(f"《{title}》" for title in titles[:3])
    return visible + (f"等 {len(values)} 章" if len(values) > 3 else "")


def _outline_targets(state: Mapping[str, Any], values: object) -> str | None:
    if not isinstance(values, (list, tuple)) or not values:
        return None
    outlines = state.get("availableOutlines")
    rows = outlines if isinstance(outlines, (list, tuple)) else ()
    titles = []
    for value in values:
        key = str(value or "").strip()
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id", item.get("outlineId"))
            if str(item_id or "") == key:
                title = _clean_text(item.get("title"))
                if title:
                    titles.append(title)
                break
    if not titles:
        return f"{len(values)} 条指定大纲"
    visible = "、".join(f"《{title}》" for title in titles[:3])
    return visible + (f"等 {len(values)} 条" if len(values) > 3 else "")


__all__ = ["WRITING_TOOL_DISPLAY_NAMES", "writing_tool_display_names"]
