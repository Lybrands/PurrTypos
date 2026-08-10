"""Model-visible screenplay tool schemas."""

from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50}
_CURSOR = {"type": "integer", "minimum": 0}
_ROLES = {
    "type": "array",
    "items": {
        "type": "string",
        "enum": [
            "sourceAnalysis",
            "creativeBrief",
            "structure",
            "sceneList",
            "screenplayDraft",
            "review",
        ],
    },
    "maxItems": 6,
    "uniqueItems": True,
}


SCREENPLAY_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "inspectScreenplayProject": _object({}),
    "readScreenplayDeliverable": _object({
        "role": _ROLES["items"],
        "revisionId": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": (
                "同一项目、同一 role 的 revisionId；省略时读取当前已接受版本。"
            ),
        },
        "episodeNumber": {"type": "integer", "minimum": 1},
    }, ("role",)),
    "searchScreenplayDeliverables": _object({
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "roles": _ROLES,
        "limit": _LIMIT,
    }, ("query",)),
    "getScreenplayEpisodeContext": _object({
        "episodeNumber": {"type": "integer", "minimum": 1},
        "draftRevisionId": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": (
                "screenplayDraft 的 revisionId，不接受其他交付物版本 ID。"
            ),
        },
    }, ("episodeNumber",)),
    "inspectSourceStructure": _object({"cursor": _CURSOR, "limit": _LIMIT}),
    "readSourceChapters": _object({
        "chapterIds": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": (
                    "只能使用 inspectSourceStructure.chapterId 或 "
                    "readSourceOutline.chapterId，不能使用 outlineId。"
                ),
            },
            "minItems": 1,
            "maxItems": 12,
            "uniqueItems": True,
        },
    }, ("chapterIds",)),
    "searchSourceText": _object({
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "limit": _LIMIT,
    }, ("query",)),
    "listSourceCharacters": _object({
        "query": {"type": "string", "maxLength": 200},
        "cursor": _CURSOR,
        "limit": _LIMIT,
    }),
    "readSourceCharacters": _object({
        "characterIds": {
            "type": "array",
            "items": {
                "type": "integer",
                "minimum": 1,
                "description": "listSourceCharacters 返回的 characterId。",
            },
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
        },
    }, ("characterIds",)),
    "listSourceWorldEntities": _object({
        "types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["location", "faction", "item", "other"],
            },
            "maxItems": 4,
            "uniqueItems": True,
        },
        "query": {"type": "string", "maxLength": 200},
        "cursor": _CURSOR,
        "limit": _LIMIT,
    }),
    "readSourceWorldEntities": _object({
        "entityIds": {
            "type": "array",
            "items": {
                "type": "integer",
                "minimum": 1,
                "description": "listSourceWorldEntities 返回的 entityId。",
            },
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
        },
    }, ("entityIds",)),
    "readSourceBackground": _object({}),
    "querySourceStoryFacts": _object({
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "character_state",
                    "relationship_state",
                    "world_fact",
                    "timeline_event",
                    "plot_thread",
                ],
            },
            "maxItems": 5,
            "uniqueItems": True,
        },
        "limit": _LIMIT,
    }, ("query",)),
    "readSourceOutline": _object({
        "outlineIds": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": (
                    "readSourceOutline 返回的 outlineId；它不是 chapterId。"
                ),
            },
            "maxItems": 12,
            "uniqueItems": True,
        },
        "cursor": _CURSOR,
        "limit": _LIMIT,
    }),
    "readSourceStyle": _object({}),
    "writeScreenplayCandidatePart": _object({
        "candidate": {"type": "object"},
    }, ("candidate",)),
    "inspectScreenplayCandidate": _object({}),
}


SCREENPLAY_TOOL_DESCRIPTIONS = {
    "inspectScreenplayProject": "查看当前剧本项目、阶段和已有交付物的紧凑清单。",
    "readScreenplayDeliverable": "读取当前项目内一个已接受或指定版本的交付物。revisionId 必须属于所选 role。",
    "searchScreenplayDeliverables": "只在当前项目各交付物的已接受版本中检索相关内容，避免旧版本污染上下文。",
    "getScreenplayEpisodeContext": "读取指定集的场景计划、前集连续性与当前草稿。",
    "inspectSourceStructure": "分页查看当前许可改编范围内的原作卷章结构，并返回可读取正文的 chapterId。",
    "readSourceChapters": "按 chapterId 读取当前许可范围内的原文章节；chapterIds 不接受 outlineId。",
    "searchSourceText": "在当前许可改编范围内检索原文并返回短摘录。",
    "listSourceCharacters": "分页查看来源作品的人物目录并返回 characterId。",
    "readSourceCharacters": "按 listSourceCharacters 返回的 characterId 读取人物资料。",
    "listSourceWorldEntities": "分页查看来源作品的地点、势力、物品等设定目录并返回 entityId。",
    "readSourceWorldEntities": "按 listSourceWorldEntities 返回的 entityId 读取世界设定。",
    "readSourceBackground": "读取来源作品的故事背景。",
    "querySourceStoryFacts": "检索带章节证据的当前故事事实、事件和伏笔线索。",
    "readSourceOutline": "分页或按 outlineId 读取原作大纲；有对应正文时另行返回可供 readSourceChapters 使用的 chapterId。",
    "readSourceStyle": "读取来源作品的写作风格约束。",
    "writeScreenplayCandidatePart": "写入本次 Run 唯一且有界的剧本候选部件。",
    "inspectScreenplayCandidate": "检查本次 Run 已写入候选部件的状态和摘要。",
}


__all__ = ["SCREENPLAY_TOOL_DESCRIPTIONS", "SCREENPLAY_TOOL_SCHEMAS"]
