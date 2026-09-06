"""Build the instance-scoped screenplay Tool Catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from purra.contracts import (
    AgentRunRequest,
    DomainEffect,
    ExecutionState,
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPayloadMode,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import CancellationSignal, ToolRegistration
from purra.json_values import thaw_json_mapping
from purra.tools import InMemoryToolCatalog

from domains.screenplay.source_scope import is_restricted_source_scope
from domains.screenplay_agent.contracts import SCREENPLAY_DELIVERABLE_LABELS
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
    ScreenplayAgentDomainContext,
)
from domains.screenplay_agent.tools.schemas import (
    SCREENPLAY_TOOL_DESCRIPTIONS,
    SCREENPLAY_TOOL_SCHEMAS,
)


_SOURCE_TOOLS = frozenset({
    "inspectSourceStructure",
    "readSourceChapters",
    "searchSourceText",
    "listSourceCharacters",
    "readSourceCharacters",
    "listSourceWorldEntities",
    "readSourceWorldEntities",
    "readSourceBackground",
    "querySourceStoryFacts",
    "readSourceOutline",
})
_UNSCOPED_SOURCE_TOOLS = frozenset({
    "listSourceCharacters",
    "readSourceCharacters",
    "listSourceWorldEntities",
    "readSourceWorldEntities",
    "readSourceBackground",
})
_PROJECT_TOOLS = frozenset({
    "inspectScreenplayProject",
    "readScreenplayDeliverable",
    "searchScreenplayDeliverables",
})
_CANDIDATE_TOOLS = frozenset({
    "writeScreenplayCandidatePart",
    "inspectScreenplayCandidate",
})
_CANDIDATE_WRITE_TOOL = frozenset({"writeScreenplayCandidatePart"})
_DEPENDENCY_READ_TOOL = frozenset({"readScreenplayTaskDependencies"})
_DRAFT_SOURCE_TOOLS = frozenset({
    "readSourceChapters",
    "querySourceStoryFacts",
})
_DOCUMENT_SECTION_TOOLS = (
    _PROJECT_TOOLS
    | _SOURCE_TOOLS
    | _DEPENDENCY_READ_TOOL
    | _CANDIDATE_WRITE_TOOL
)
_READ_TOOLS = _PROJECT_TOOLS | _SOURCE_TOOLS | {"getScreenplayEpisodeContext"}
_HOST_CAPTURED_TEXT_PARTS = frozenset({"scene"})
_HOST_CAPTURED_JSON_PARTS = frozenset({"episode_metadata"})
_ROLE_TOOLS = {
    "sourceAnalysis": _PROJECT_TOOLS | _SOURCE_TOOLS | _CANDIDATE_TOOLS,
    "creativeBrief": _PROJECT_TOOLS | _SOURCE_TOOLS | _CANDIDATE_TOOLS,
    "structure": _PROJECT_TOOLS | _SOURCE_TOOLS | _CANDIDATE_TOOLS,
    "sceneList": _PROJECT_TOOLS | _SOURCE_TOOLS | _CANDIDATE_TOOLS,
    "screenplayDraft": (
        _PROJECT_TOOLS
        | _SOURCE_TOOLS
        | _CANDIDATE_TOOLS
        | {"getScreenplayEpisodeContext"}
    ),
    "review": frozenset(SCREENPLAY_TOOL_SCHEMAS),
}
_TOOL_PROFILES = {
    "draft_scene": (
        frozenset({"getScreenplaySceneContext", "readScreenplayDeliverable"})
        | _DRAFT_SOURCE_TOOLS
    ),
    "episode_metadata": _DEPENDENCY_READ_TOOL,
    "review_dimension": _PROJECT_TOOLS | frozenset({
        "getScreenplayEpisodeContext",
        "writeScreenplayCandidatePart",
    }),
    "source_chapter_digest": frozenset({
        "inspectSourceStructure",
        "readSourceChapters",
        "writeScreenplayCandidatePart",
    }),
    "source_digest_reduction": (
        _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "source_analysis_section": (
        _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "creative_brief_section": _PROJECT_TOOLS | frozenset({
        "readScreenplayTaskDependencies",
        "writeScreenplayCandidatePart",
    }),
    "series_arc_index": _PROJECT_TOOLS | _CANDIDATE_WRITE_TOOL,
    "series_arc_phase": (
        _PROJECT_TOOLS | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "episode_plan_index": (
        _PROJECT_TOOLS | {"readSourceOutline"} | _DEPENDENCY_READ_TOOL
        | _CANDIDATE_WRITE_TOOL
    ),
    "episode_plan_fragment": (
        {"inspectSourceStructure", "readSourceChapters"}
        | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "character_arcs_index": (
        _PROJECT_TOOLS | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "character_arc_fragment": (
        _PROJECT_TOOLS | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "scene_list_episode": _PROJECT_TOOLS | _CANDIDATE_WRITE_TOOL,
    "final_response": frozenset(),
}
_DISPLAY_NAMES = {
    "readScreenplayTaskDependencies": "读取任务依赖",
    "inspectScreenplayProject": "查看剧本项目",
    "readScreenplayDeliverable": "读取剧本交付物",
    "searchScreenplayDeliverables": "检索剧本交付物",
    "getScreenplayEpisodeContext": "读取分集上下文",
    "getScreenplaySceneContext": "读取当前场景材料",
    "inspectSourceStructure": "查看原作结构",
    "readSourceChapters": "读取原文章节",
    "searchSourceText": "检索原文",
    "listSourceCharacters": "查看原作人物",
    "readSourceCharacters": "读取人物资料",
    "listSourceWorldEntities": "查看世界设定",
    "readSourceWorldEntities": "读取世界设定",
    "readSourceBackground": "读取故事背景",
    "querySourceStoryFacts": "检索故事事实",
    "readSourceOutline": "读取原作大纲",
    "writeScreenplayCandidatePart": "写入剧本候选稿",
    "inspectScreenplayCandidate": "检查剧本候选稿",
}

_EPISODE_DISPLAY_NAMES = {
    "readScreenplayTaskDependencies": "读取第 {episode} 集任务依赖",
    "inspectScreenplayProject": "查看第 {episode} 集所属剧本项目",
    "readScreenplayDeliverable": "读取第 {episode} 集剧本交付物",
    "searchScreenplayDeliverables": "为第 {episode} 集检索剧本交付物",
    "getScreenplayEpisodeContext": "读取第 {episode} 集上下文",
    "getScreenplaySceneContext": "读取第 {episode} 集当前场景材料",
    "inspectSourceStructure": "为第 {episode} 集查看原作结构",
    "readSourceChapters": "为第 {episode} 集读取原文章节",
    "searchSourceText": "为第 {episode} 集检索原文",
    "listSourceCharacters": "为第 {episode} 集查看原作人物",
    "readSourceCharacters": "为第 {episode} 集读取人物资料",
    "listSourceWorldEntities": "为第 {episode} 集查看世界设定",
    "readSourceWorldEntities": "为第 {episode} 集读取世界设定",
    "readSourceBackground": "为第 {episode} 集读取故事背景",
    "querySourceStoryFacts": "为第 {episode} 集检索故事事实",
    "readSourceOutline": "为第 {episode} 集读取原作大纲",
    "writeScreenplayCandidatePart": "写入第 {episode} 集剧本候选稿",
    "inspectScreenplayCandidate": "检查第 {episode} 集剧本候选稿",
}


def screenplay_tool_display_names(
    tool_name: str,
    *,
    episode_number: int | None = None,
    deliverable_role: str | None = None,
    task_episode_number: int | None = None,
    read_targets: tuple[str, ...] = (),
    search_query: str | None = None,
    target_detail: str | None = None,
) -> dict[str, str]:
    normalized_name = str(tool_name or "")
    label = _DISPLAY_NAMES.get(normalized_name)
    if normalized_name == "readScreenplayTaskDependencies":
        if read_targets:
            label = f"读取{_joined_targets(read_targets)}"
        elif target_detail == "可读产出清单":
            label = (
                f"查看第 {episode_number} 集当前可读取的任务产出"
                if episode_number is not None and episode_number > 0
                else "查看当前可读取的任务产出"
            )
    elif normalized_name == "readScreenplayDeliverable":
        target = SCREENPLAY_DELIVERABLE_LABELS.get(deliverable_role, "剧本交付物")
        if episode_number is not None and episode_number > 0:
            label = f"读取第 {episode_number} 集{target}"
        elif task_episode_number is not None and task_episode_number > 0:
            label = f"为第 {task_episode_number} 集读取{target}"
        else:
            label = f"读取{target}"
        if target_detail == "结构化内容":
            label += "的结构化数据"
        elif target_detail == "渲染文本":
            label += "正文"
        elif target_detail:
            label += f"：{_display_text(target_detail, 56)}"
    elif episode_number is not None and episode_number > 0:
        template = _EPISODE_DISPLAY_NAMES.get(normalized_name)
        if template is not None:
            label = template.format(episode=episode_number)
    if normalized_name == "readSourceChapters" and read_targets:
        label = f"读取{_joined_targets(read_targets)}的原文"
        if episode_number is not None and episode_number > 0:
            label = f"为第 {episode_number} 集{label}"
    elif search_query:
        query = f"“{_display_text(search_query)}”"
        deliverable_scope = (
            _display_text(target_detail, 56)
            if target_detail
            else "剧本交付物"
        )
        fact_scope = (
            _display_text(target_detail, 56)
            if target_detail
            else "故事事实"
        )
        world_scope = (
            _display_text(target_detail, 56)
            if target_detail
            else "世界设定"
        )
        search_labels = {
            "searchSourceText": f"在原文中查找{query}",
            "searchScreenplayDeliverables": f"在{deliverable_scope}中查找{query}",
            "querySourceStoryFacts": f"在{fact_scope}中查找与{query}相关的记录",
            "listSourceCharacters": f"筛选与{query}相关的原作人物",
            "listSourceWorldEntities": f"在{world_scope}中筛选与{query}相关的条目",
        }
        label = search_labels.get(normalized_name, label)
        if episode_number is not None and episode_number > 0 and label:
            label = f"为第 {episode_number} 集{label}"
    if (
        label
        and target_detail
        and normalized_name not in {
            "readScreenplayTaskDependencies",
            "readScreenplayDeliverable",
        }
        and not (
            search_query
            and normalized_name in {
                "searchScreenplayDeliverables",
                "querySourceStoryFacts",
                "listSourceWorldEntities",
            }
        )
    ):
        detail = _display_text(target_detail, 56)
        detail_labels = {
            "getScreenplaySceneContext": f"读取{detail}",
            "readSourceChapters": f"读取{detail}的原文",
            "readSourceCharacters": f"读取{detail}的资料",
            "readSourceWorldEntities": f"读取{detail}",
            "readSourceOutline": f"读取{detail}",
            "inspectSourceStructure": f"查看原作{detail}",
            "searchScreenplayDeliverables": f"查看{detail}",
            "querySourceStoryFacts": f"查看{detail}",
            "listSourceWorldEntities": f"查看{detail}设定",
            "writeScreenplayCandidatePart": f"写入{detail}候选稿",
            "inspectScreenplayCandidate": f"检查{detail}候选稿",
        }
        replacement = detail_labels.get(normalized_name)
        if replacement:
            if normalized_name == "writeScreenplayCandidatePart":
                label = (
                    f"写入第 {episode_number} 集{detail}候选稿"
                    if episode_number is not None and episode_number > 0
                    else replacement
                )
            elif normalized_name == "inspectScreenplayCandidate":
                label = (
                    f"检查第 {episode_number} 集{detail}候选稿"
                    if episode_number is not None and episode_number > 0
                    else replacement
                )
            else:
                label = replacement
                if episode_number is not None and episode_number > 0:
                    label = f"为第 {episode_number} 集{label}"
    return {"zh-CN": label} if label else {}


def _display_text(value: str, limit: int = 72) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _joined_targets(values: Sequence[str]) -> str:
    targets = "、".join(_display_text(value, 48) for value in values[:2])
    return targets + (f"等 {len(values)} 项" if len(values) > 2 else "")


def _dependency_display_target(
    key: str,
    domain: Mapping[str, Any],
) -> str:
    parts = key.split(":")
    if (
        len(parts) >= 3
        and parts[0] == "draft"
        and parts[1].isdigit()
        and int(parts[1]) > 0
    ):
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
            return (
                f"第 {episode} 集第 {tuple(scene_ids).index(scene_id) + 1} 场"
                "已完成剧本"
            )
        return f"第 {episode} 集指定场景的已完成剧本"
    if parts and parts[0] == "section":
        section_parts = parts[1:]
        if section_parts and section_parts[0] in SCREENPLAY_DELIVERABLE_LABELS:
            role = section_parts.pop(0)
            role_label = SCREENPLAY_DELIVERABLE_LABELS[role]
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
    structural_parts = parts[1:] if parts and parts[0] == "structure" else parts
    structural = _structure_dependency_target(structural_parts)
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
        suffix = {
            "metadata": "剧集信息",
            "validation": "校验结果",
        }.get(parts[2])
        if suffix:
            return f"第 {int(parts[1])} 集{suffix}"
    return "指定任务产出"


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
        return (
            f"第 {int(normalized[1].removeprefix('episode-'))} 集分集结构"
        )
    if normalized == ("episode_plan",):
        return "分集结构"
    if normalized == ("character_arcs", "index"):
        return "人物弧目录"
    if len(normalized) >= 3 and normalized[:2] == (
        "character_arcs",
        "character",
    ):
        return "指定人物弧"
    if normalized == ("character_arcs",):
        return "人物弧"
    if normalized == ("hooks",):
        return "剧情钩子"
    return None


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
_REVIEW_DIMENSION_LABELS = {
    "continuity": "连续性审阅",
    "character_arc": "人物弧审阅",
    "structure_rhythm": "结构节奏审阅",
    "dialogue": "对白审阅",
    "format": "格式审阅",
}


def _values(arguments, key: str) -> tuple[Any, ...]:
    value = arguments.get(key)
    return tuple(value) if _is_json_array(value) else ()


def _is_json_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _candidate_display_target(
    domain: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str | None:
    candidate = arguments.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    dimension = candidate.get("reviewDimension")
    if isinstance(dimension, str) and dimension.strip():
        return _REVIEW_DIMENSION_LABELS.get(
            dimension,
            f"{_display_text(dimension, 24)}审阅",
        )
    if domain.get("expectedPartType") == "scene":
        scene_id = str(domain.get("expectedPartKey") or "")
        scene_ids = tuple(domain.get("sceneIds") or ())
        if scene_id in scene_ids:
            return f"第 {scene_ids.index(scene_id) + 1} 场"
        return "场景"
    role = str(domain.get("targetRole") or "")
    role_label = SCREENPLAY_DELIVERABLE_LABELS.get(role)
    if role_label:
        return role_label
    return None


def _complete_operation_display_params(
    tool_name: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    targets = params.get("readTargets")
    display_names = screenplay_tool_display_names(
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


def screenplay_operation_display_params(
    domain: Mapping[str, Any],
    arguments: Mapping[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    """Project one call into bounded business-semantic display metadata.

    This pure entrypoint is shared by live execution and historical read
    models.  The durable journal remains immutable; an older operation whose
    label was produced by a defective projector can be rendered again from
    its persisted call arguments and persisted execution scope.
    """

    domain = thaw_json_mapping(domain)
    arguments = thaw_json_mapping(arguments)
    value = arguments.get("episodeNumber")
    if tool_name == "readScreenplayDeliverable":
        params = {}
        role = arguments.get("role")
        if isinstance(role, str) and role in SCREENPLAY_DELIVERABLE_LABELS:
            params["deliverableRole"] = role
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            params["episodeNumber"] = value
        bound = domain.get("boundEpisodeNumber")
        if isinstance(bound, int) and not isinstance(bound, bool) and bound > 0:
            params["taskEpisodeNumber"] = bound
        representation = arguments.get("representation")
        if representation == "structured":
            params["targetDetail"] = "结构化内容"
        elif representation == "text":
            params["targetDetail"] = "渲染文本"
        elif representation == "section_index":
            params["targetDetail"] = "章节目录与长度"
        sections = _values(arguments, "sectionKeys")
        if sections:
            labels = _DOCUMENT_SECTION_LABELS.get(str(role), {})
            params["targetDetail"] = "、".join(
                labels.get(str(key), "所选章节") for key in sections[:4]
            ) + (f"等 {len(sections)} 项" if len(sections) > 4 else "")
        return _complete_operation_display_params(tool_name, params)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        value = domain.get("boundEpisodeNumber")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        value = None
    params = {"episodeNumber": value} if value is not None else {}
    if tool_name == "getScreenplaySceneContext":
        scene_id = str(domain.get("expectedPartKey") or "")
        by_episode = domain.get("sceneIdsByEpisode") or {}
        scene_ids = domain.get("sceneIds") or (
            by_episode.get(str(value), by_episode.get(value, ()))
            if isinstance(by_episode, Mapping) else ()
        )
        if scene_id and scene_id in scene_ids:
            params["targetDetail"] = f"第 {tuple(scene_ids).index(scene_id) + 1} 场的场景计划、衔接与改编依据"
        else:
            params["targetDetail"] = "当前场景的计划、衔接与改编依据"
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
            key for key in arguments.get("chapterIds", ())
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
            roles = _values(arguments, "roles")
            labels = [
                SCREENPLAY_DELIVERABLE_LABELS.get(str(role))
                for role in roles
            ]
            labels = [label for label in labels if label]
            if labels:
                params["targetDetail"] = "、".join(labels)
        elif tool_name == "querySourceStoryFacts":
            kinds = _values(arguments, "kinds")
            labels = [
                _FACT_KIND_LABELS.get(str(kind)) for kind in kinds
            ]
            labels = [label for label in labels if label]
            if labels:
                params["targetDetail"] = "、".join(labels)
        elif tool_name == "listSourceWorldEntities":
            types = _values(arguments, "types")
            labels = [
                _WORLD_TYPE_LABELS.get(str(kind)) for kind in types
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
        if isinstance(cursor, int) and not isinstance(cursor, bool) and cursor > 0:
            params["targetDetail"] = "后续章节目录"
    elif tool_name in {
        "writeScreenplayCandidatePart",
        "inspectScreenplayCandidate",
    }:
        target = _candidate_display_target(domain, arguments)
        if target:
            params["targetDetail"] = target
    return _complete_operation_display_params(tool_name, params)


def _operation_display_params(state, arguments, tool_call) -> dict[str, Any]:
    return screenplay_operation_display_params(
        state.domain,
        arguments,
        tool_call.name,
    )


def build_screenplay_tool_catalog(
    *,
    handlers: Mapping[str, Any],
) -> InMemoryToolCatalog:
    schema_names = set(SCREENPLAY_TOOL_SCHEMAS)
    handler_names = set(handlers)
    if schema_names != handler_names:
        missing = schema_names - handler_names
        hidden = handler_names - schema_names
        details = []
        if missing:
            details.append("missingHandlers=" + ",".join(sorted(missing)))
        if hidden:
            details.append("hiddenHandlers=" + ",".join(sorted(hidden)))
        raise RuntimeError(
            "Screenplay tool catalog contract is incomplete: "
            + "; ".join(details)
        )

    registrations = tuple(
        ToolRegistration(
            schema=ToolSchema(
                name=name,
                description=SCREENPLAY_TOOL_DESCRIPTIONS[name],
                parameters=parameters,
                display_names=screenplay_tool_display_names(name),
            ),
            handler=_adapt_handler(name, handlers[name]),
            policy=(
                ToolPolicy(
                    ToolExecutionMode.PROPOSE,
                    _DISPLAY_NAMES[name],
                    ToolRiskLevel.WRITE,
                )
                if name == "writeScreenplayCandidatePart"
                else ToolPolicy(ToolExecutionMode.READ, _DISPLAY_NAMES[name])
            ),
            scope_validator=_scope_validator,
            cancellation_linearizable=(name == "writeScreenplayCandidatePart"),
            host_managed_durability=(name == "writeScreenplayCandidatePart"),
            data_contract=ToolDataContract(
                model_owned_paths=_model_paths(parameters),
                host_bound_paths=(
                    "projectId",
                    "taskId",
                    "unitId",
                    "targetRole",
                    "expectedPartType",
                    "expectedPartKey",
                    "dependencyPartKeys",
                    "deliverableRevisionScope",
                    "boundEpisodeNumber",
                    "sourceBookId",
                    "sourceScope",
                ),
                payload_mode=(
                    ToolPayloadMode.BATCH
                    if name == "writeScreenplayCandidatePart"
                    else ToolPayloadMode.INLINE
                ),
            ),
            max_argument_chars=(
                100_000 if name == "writeScreenplayCandidatePart" else 12_000
            ),
            operation_display_params=_operation_display_params,
        )
        for name, parameters in SCREENPLAY_TOOL_SCHEMAS.items()
    )
    return InMemoryToolCatalog(registrations, _enabled_tools)


def _adapt_handler(tool_name: str, handler):
    async def _run(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        result = await handler(state, dict(arguments), signal)
        content = str(result.get("content") or "")
        effect = result.get("effect")
        return ToolHandlerResult(
            content=content,
            from_cache=bool(result.get("fromCache", False)),
            effects=(
                (DomainEffect(type=str(effect[0]), payload=dict(effect[1])),)
                if isinstance(effect, tuple) and len(effect) == 2
                else ()
            ),
            error_code=str(result.get("errorCode") or "") or None,
            effect_state=ToolEffectState(
                str(result.get("effectState") or ToolEffectState.UNKNOWN.value)
            ),
        )

    _run.__name__ = f"run_{tool_name}"
    return _run


async def _scope_validator(
    state: ExecutionState,
    arguments: Mapping[str, Any],
    signal: CancellationSignal | None = None,
) -> str | None:
    del arguments, signal
    if not str(state.domain.get("projectId") or "").strip():
        return "The screenplay project scope is missing."
    if not str(state.domain.get("taskId") or "").strip():
        return "The screenplay task scope is missing."
    return None


def _enabled_tools(request: AgentRunRequest) -> frozenset[str]:
    if request.domain_context.namespace != SCREENPLAY_AGENT_DOMAIN_NAMESPACE:
        return frozenset()
    context = ScreenplayAgentDomainContext.from_core_context(
        request.domain_context
    )
    if context.is_root:
        return _scoped_read_tools(context, _READ_TOOLS)
    if context.tool_access in _TOOL_PROFILES:
        return _scoped_profile_tools(
            context,
            _TOOL_PROFILES[context.tool_access],
        )
    if context.expected_part_type in _HOST_CAPTURED_TEXT_PARTS:
        # Long text stays ordinary model output, but its dynamic evidence must
        # still be acquired through recorded read tools.
        return _scoped_read_tools(
            context,
            set(_ROLE_TOOLS.get(context.target_role, ())) & _READ_TOOLS,
        )
    if context.expected_part_type in _HOST_CAPTURED_JSON_PARTS:
        return _DEPENDENCY_READ_TOOL
    if context.tool_access == "candidate_write":
        return _CANDIDATE_TOOLS
    if context.tool_access == "evidence_read":
        return _scoped_read_tools(
            context,
            set(_ROLE_TOOLS.get(context.target_role, ())) & _READ_TOOLS,
        )
    enabled = set(_ROLE_TOOLS.get(context.target_role, ()))
    if not context.source_book_id:
        enabled.difference_update(_SOURCE_TOOLS)
    elif is_restricted_source_scope(context.source_scope or {}):
        # These catalogs are whole-book records and cannot prove that each
        # returned fact belongs to the licensed chapter subset. Do not
        # advertise tools that the query boundary must reject; the scoped
        # querySourceStoryFacts/readSourceChapters paths remain available.
        enabled.difference_update(_UNSCOPED_SOURCE_TOOLS)
    return frozenset(enabled)


def _scoped_read_tools(
    context: ScreenplayAgentDomainContext,
    tools,
) -> frozenset[str]:
    enabled = set(tools) & _READ_TOOLS
    if not context.source_book_id:
        enabled.difference_update(_SOURCE_TOOLS)
    elif is_restricted_source_scope(context.source_scope or {}):
        enabled.difference_update(_UNSCOPED_SOURCE_TOOLS)
    return frozenset(enabled)


def _scoped_profile_tools(
    context: ScreenplayAgentDomainContext,
    tools,
) -> frozenset[str]:
    enabled = set(tools)
    if not context.source_book_id:
        enabled.difference_update(_SOURCE_TOOLS)
    elif is_restricted_source_scope(context.source_scope or {}):
        enabled.difference_update(_UNSCOPED_SOURCE_TOOLS)
    return frozenset(enabled)


def _model_paths(schema: Mapping[str, Any]) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    return tuple(str(name) for name in properties)


__all__ = [
    "build_screenplay_tool_catalog",
    "screenplay_operation_display_params",
    "screenplay_tool_display_names",
]
