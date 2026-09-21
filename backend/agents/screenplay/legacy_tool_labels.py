"""Pure labels for read-only calls persisted by the frozen Screenplay Agent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from purra.json_values import thaw_json_mapping


_DELIVERABLE_LABELS = {
    "sourceAnalysis": "原作分析",
    "creativeBrief": "创作简报",
    "structure": "分集结构",
    "sceneList": "场景表",
    "screenplayDraft": "剧本正文",
    "review": "审阅报告",
}
_DISPLAY_NAMES = {
    "readScreenplayTaskDependencies": "读取任务依赖",
    "readScreenplayDeliverable": "读取剧本交付物",
    "searchScreenplayDeliverables": "检索剧本交付物",
    "getScreenplaySceneContext": "读取当前场景材料",
    "inspectSourceStructure": "查看原作结构",
    "readSourceChapters": "读取原文章节",
    "searchSourceText": "检索原文",
    "listSourceCharacters": "查看原作人物",
    "readSourceCharacters": "读取人物资料",
    "listSourceWorldEntities": "查看世界设定",
    "readSourceWorldEntities": "读取世界设定",
    "querySourceStoryFacts": "检索故事事实",
    "readSourceOutline": "读取原作大纲",
}
_EPISODE_DISPLAY_NAMES = {
    "readScreenplayTaskDependencies": "读取第 {episode} 集任务依赖",
    "readScreenplayDeliverable": "读取第 {episode} 集剧本交付物",
    "searchScreenplayDeliverables": "为第 {episode} 集检索剧本交付物",
    "getScreenplaySceneContext": "读取第 {episode} 集当前场景材料",
    "inspectSourceStructure": "为第 {episode} 集查看原作结构",
    "readSourceChapters": "为第 {episode} 集读取原文章节",
    "searchSourceText": "为第 {episode} 集检索原文",
    "listSourceCharacters": "为第 {episode} 集查看原作人物",
    "readSourceCharacters": "为第 {episode} 集读取人物资料",
    "listSourceWorldEntities": "为第 {episode} 集查看世界设定",
    "readSourceWorldEntities": "为第 {episode} 集读取世界设定",
    "querySourceStoryFacts": "为第 {episode} 集检索故事事实",
    "readSourceOutline": "为第 {episode} 集读取原作大纲",
}
_DOCUMENT_SECTION_LABELS = {
    "sourceAnalysis": {
        "characters": "人物分析",
        "story": "故事分析",
        "world": "世界观分析",
        "themes": "主题分析",
        "adaptation_risks": "改编风险分析",
    },
    "creativeBrief": {
        "positioning": "项目定位",
        "premise": "核心命题",
        "characters": "核心人物设计",
        "world": "剧本世界设定",
        "adaptation_rules": "改编规则",
    },
    "sceneList": {"episode_plan": "场景规划"},
}
_WORLD_TYPE_LABELS = {
    "location": "地点",
    "faction": "势力",
    "item": "物品",
    "other": "其他设定",
}
_FACT_KIND_LABELS = {
    "character_state": "人物状态",
    "relationship_state": "关系状态",
    "world_fact": "世界事实",
    "timeline_event": "时间线事件",
    "plot_thread": "情节线索",
}


def legacy_screenplay_operation_display_params(
    domain: Mapping[str, Any],
    arguments: Mapping[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    domain = thaw_json_mapping(domain)
    arguments = thaw_json_mapping(arguments)
    value = arguments.get("episodeNumber")
    if tool_name == "readScreenplayDeliverable":
        params: dict[str, Any] = {}
        role = arguments.get("role")
        if isinstance(role, str) and role in _DELIVERABLE_LABELS:
            params["deliverableRole"] = role
        if _positive_int(value):
            params["episodeNumber"] = value
        bound = domain.get("boundEpisodeNumber")
        if _positive_int(bound):
            params["taskEpisodeNumber"] = bound
        representation = arguments.get("representation")
        params.update({
            "structured": {"targetDetail": "结构化内容"},
            "text": {"targetDetail": "渲染文本"},
            "section_index": {"targetDetail": "章节目录与长度"},
        }.get(str(representation), {}))
        sections = _values(arguments, "sectionKeys")
        if sections:
            labels = _DOCUMENT_SECTION_LABELS.get(str(role), {})
            params["targetDetail"] = "、".join(
                labels.get(str(key), "所选章节") for key in sections[:4]
            ) + (f"等 {len(sections)} 项" if len(sections) > 4 else "")
        return _complete_display_params(tool_name, params)

    if not _positive_int(value):
        value = domain.get("boundEpisodeNumber")
    if not _positive_int(value):
        value = None
    params = {"episodeNumber": value} if value is not None else {}
    if tool_name == "getScreenplaySceneContext":
        scene_id = str(domain.get("expectedPartKey") or "")
        by_episode = domain.get("sceneIdsByEpisode") or {}
        scene_ids = domain.get("sceneIds") or (
            by_episode.get(str(value), by_episode.get(value, ()))
            if isinstance(by_episode, Mapping)
            else ()
        )
        params["targetDetail"] = (
            f"第 {tuple(scene_ids).index(scene_id) + 1} 场的场景计划、衔接与改编依据"
            if scene_id and scene_id in scene_ids
            else "当前场景的计划、衔接与改编依据"
        )
    elif tool_name == "readScreenplayTaskDependencies":
        keys = arguments.get("partKeys")
        if _is_json_array(keys):
            params["readTargets"] = [
                _dependency_display_target(key, domain)
                for key in keys
                if isinstance(key, str)
            ]
        else:
            params["targetDetail"] = "可读产出清单"
    elif tool_name == "readSourceChapters":
        source_scope = domain.get("sourceScope")
        source_scope = source_scope if isinstance(source_scope, Mapping) else {}
        chapters = source_scope.get("chapters") or ()
        titles = {
            part["id"]: part["title"]
            for part in chapters
            if isinstance(part, Mapping)
            and part.get("id")
            and str(part.get("title") or "").strip()
        }
        requested = tuple(
            key
            for key in arguments.get("chapterIds", ())
            if isinstance(key, str)
        )
        if requested and all(key in titles for key in requested):
            params["readTargets"] = [
                _display_text(str(titles[key]), 48) for key in requested
            ]
        elif requested:
            params["targetDetail"] = f"所选 {len(requested)} 个原文章节"
    elif tool_name in {
        "searchSourceText",
        "searchScreenplayDeliverables",
        "querySourceStoryFacts",
        "listSourceCharacters",
        "listSourceWorldEntities",
    }:
        query = arguments.get("query")
        if isinstance(query, str) and query.strip():
            params["searchQuery"] = _display_text(query)
        if tool_name == "searchScreenplayDeliverables":
            labels = [
                _DELIVERABLE_LABELS.get(str(role))
                for role in _values(arguments, "roles")
            ]
            labels = [label for label in labels if label]
            if labels:
                params["targetDetail"] = "、".join(labels)
        elif tool_name == "querySourceStoryFacts":
            labels = [
                _FACT_KIND_LABELS.get(str(kind))
                for kind in _values(arguments, "kinds")
            ]
            labels = [label for label in labels if label]
            if labels:
                params["targetDetail"] = "、".join(labels)
        elif tool_name == "listSourceWorldEntities":
            labels = [
                _WORLD_TYPE_LABELS.get(str(kind))
                for kind in _values(arguments, "types")
            ]
            labels = [label for label in labels if label]
            if labels:
                params["targetDetail"] = "、".join(labels)
    elif tool_name == "readSourceCharacters":
        count = len(_values(arguments, "characterIds"))
        if count:
            params["targetDetail"] = f"所选 {count} 位原作人物"
    elif tool_name == "readSourceWorldEntities":
        count = len(_values(arguments, "entityIds"))
        if count:
            params["targetDetail"] = f"所选 {count} 条世界设定"
    elif tool_name == "readSourceOutline":
        count = len(_values(arguments, "outlineIds"))
        params["targetDetail"] = (
            f"所选 {count} 条原作大纲" if count else "原作大纲目录"
        )
    elif tool_name == "inspectSourceStructure":
        cursor = arguments.get("cursor")
        if _positive_int(cursor):
            params["targetDetail"] = "后续章节目录"
    return _complete_display_params(tool_name, params)


def _complete_display_params(
    tool_name: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    targets = params.get("readTargets")
    display_names = _display_names(
        tool_name,
        episode_number=params.get("episodeNumber"),
        deliverable_role=params.get("deliverableRole"),
        task_episode_number=params.get("taskEpisodeNumber"),
        read_targets=tuple(
            value for value in targets if isinstance(value, str)
        ) if _is_json_array(targets) else (),
        search_query=params.get("searchQuery"),
        target_detail=params.get("targetDetail"),
    )
    return {**params, "displayNames": display_names}


def _display_names(
    tool_name: str,
    *,
    episode_number: int | None = None,
    deliverable_role: str | None = None,
    task_episode_number: int | None = None,
    read_targets: tuple[str, ...] = (),
    search_query: str | None = None,
    target_detail: str | None = None,
) -> dict[str, str]:
    label = _DISPLAY_NAMES.get(str(tool_name or ""))
    if tool_name == "readScreenplayTaskDependencies":
        if read_targets:
            label = f"读取{_joined_targets(read_targets)}"
        elif target_detail == "可读产出清单":
            label = (
                f"查看第 {episode_number} 集当前可读取的任务产出"
                if _positive_int(episode_number)
                else "查看当前可读取的任务产出"
            )
    elif tool_name == "readScreenplayDeliverable":
        target = _DELIVERABLE_LABELS.get(deliverable_role, "剧本交付物")
        if _positive_int(episode_number):
            label = f"读取第 {episode_number} 集{target}"
        elif _positive_int(task_episode_number):
            label = f"为第 {task_episode_number} 集读取{target}"
        else:
            label = f"读取{target}"
        if target_detail == "结构化内容":
            label += "的结构化数据"
        elif target_detail == "渲染文本":
            label += "正文"
        elif target_detail:
            label += f"：{_display_text(target_detail, 56)}"
    elif _positive_int(episode_number):
        template = _EPISODE_DISPLAY_NAMES.get(tool_name)
        if template is not None:
            label = template.format(episode=episode_number)
    if tool_name == "readSourceChapters" and read_targets:
        label = f"读取{_joined_targets(read_targets)}的原文"
        if _positive_int(episode_number):
            label = f"为第 {episode_number} 集{label}"
    elif search_query:
        query = f"“{_display_text(search_query)}”"
        scope = _display_text(target_detail, 56) if target_detail else None
        label = {
            "searchSourceText": f"在原文中查找{query}",
            "searchScreenplayDeliverables": f"在{scope or '剧本交付物'}中查找{query}",
            "querySourceStoryFacts": f"在{scope or '故事事实'}中查找与{query}相关的记录",
            "listSourceCharacters": f"筛选与{query}相关的原作人物",
            "listSourceWorldEntities": f"在{scope or '世界设定'}中筛选与{query}相关的条目",
        }.get(tool_name, label)
        if _positive_int(episode_number) and label:
            label = f"为第 {episode_number} 集{label}"
    if label and target_detail and tool_name not in {
        "readScreenplayTaskDependencies",
        "readScreenplayDeliverable",
    } and not (
        search_query
        and tool_name in {
            "searchScreenplayDeliverables",
            "querySourceStoryFacts",
            "listSourceWorldEntities",
        }
    ):
        detail = _display_text(target_detail, 56)
        replacement = {
            "getScreenplaySceneContext": f"读取{detail}",
            "readSourceChapters": f"读取{detail}的原文",
            "readSourceCharacters": f"读取{detail}的资料",
            "readSourceWorldEntities": f"读取{detail}",
            "readSourceOutline": f"读取{detail}",
            "inspectSourceStructure": f"查看原作{detail}",
            "searchScreenplayDeliverables": f"查看{detail}",
            "querySourceStoryFacts": f"查看{detail}",
            "listSourceWorldEntities": f"查看{detail}设定",
        }.get(tool_name)
        if replacement:
            label = replacement
            if _positive_int(episode_number):
                label = f"为第 {episode_number} 集{label}"
    return {"zh-CN": label} if label else {}


def _dependency_display_target(key: str, domain: Mapping[str, Any]) -> str:
    parts = key.split(":")
    if len(parts) >= 3 and parts[0] == "draft" and parts[1].isdigit():
        episode = int(parts[1])
        scene_id = ":".join(parts[2:])
        by_episode = domain.get("sceneIdsByEpisode")
        by_episode = by_episode if isinstance(by_episode, Mapping) else {}
        scene_ids = by_episode.get(str(episode), by_episode.get(episode, ()))
        if not _is_json_array(scene_ids) or not scene_ids:
            scene_ids = (
                domain.get("sceneIds") or ()
                if domain.get("boundEpisodeNumber") == episode
                else ()
            )
        if scene_id in scene_ids:
            return f"第 {episode} 集第 {tuple(scene_ids).index(scene_id) + 1} 场已完成剧本"
        return f"第 {episode} 集指定场景的已完成剧本"
    if parts and parts[0] == "section":
        section_parts = parts[1:]
        if section_parts and section_parts[0] in _DELIVERABLE_LABELS:
            role = section_parts.pop(0)
            role_label = _DELIVERABLE_LABELS[role]
            if role != "structure":
                section_key = ":".join(section_parts)
                label = _DOCUMENT_SECTION_LABELS.get(role, {}).get(section_key)
                if label:
                    return label
                episode = section_key.removeprefix("episode-")
                if episode.isdigit() and int(episode) > 0:
                    return f"第 {int(episode)} 集{role_label}"
                return f"{role_label}分段"
        structural = _structure_dependency_target(section_parts)
        if structural:
            return structural
    structural = _structure_dependency_target(
        parts[1:] if parts and parts[0] == "structure" else parts
    )
    if structural:
        return structural
    if key.startswith("source-analysis:chapter:"):
        return "原作章节分析"
    if key.startswith("source-analysis:reduce:"):
        return "原作分析汇总"
    if len(parts) == 3 and parts[0] == "review" and parts[1].isdigit():
        return f"第 {int(parts[1])} 集审阅结果"
    if key == "document:evidence":
        return "项目依据"
    if len(parts) == 2 and parts[0] == "evidence" and parts[1].isdigit():
        return f"第 {int(parts[1])} 集素材包"
    if len(parts) == 3 and parts[0] == "episode" and parts[1].isdigit():
        suffix = {"metadata": "剧集信息", "validation": "校验结果"}.get(parts[2])
        if suffix:
            return f"第 {int(parts[1])} 集{suffix}"
    return "指定任务产出"


def _structure_dependency_target(parts: Sequence[str]) -> str | None:
    normalized = tuple(parts)
    if normalized and normalized[0] == "structure":
        normalized = normalized[1:]
    if normalized == ("series_arc", "index"):
        return "全剧主线阶段目录"
    if len(normalized) >= 3 and normalized[:2] == ("series_arc", "phase"):
        return "指定全剧主线阶段"
    if normalized == ("series_arc",):
        return "全剧主线"
    if normalized == ("episode_plan", "index"):
        return "分集结构目录"
    if (
        len(normalized) == 2
        and normalized[0] == "episode_plan"
        and normalized[1].startswith("episode-")
        and normalized[1].removeprefix("episode-").isdigit()
    ):
        return f"第 {int(normalized[1].removeprefix('episode-'))} 集分集结构"
    if normalized == ("episode_plan",):
        return "分集结构"
    if normalized == ("character_arcs", "index"):
        return "人物弧目录"
    if len(normalized) >= 3 and normalized[:2] == ("character_arcs", "character"):
        return "指定人物弧"
    if normalized == ("character_arcs",):
        return "人物弧"
    if normalized == ("hooks",):
        return "剧情钩子"
    return None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _values(arguments: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    value = arguments.get(key)
    return tuple(value) if _is_json_array(value) else ()


def _is_json_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _display_text(value: str, limit: int = 72) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _joined_targets(values: Sequence[str]) -> str:
    targets = "、".join(_display_text(value, 48) for value in values[:2])
    return targets + (f"等 {len(values)} 项" if len(values) > 2 else "")


__all__ = ["legacy_screenplay_operation_display_params"]
