"""Model-visible contracts and host policies for screenplay tools."""

from __future__ import annotations

from agent_core.contracts import (
    ToolContextContract,
    ToolExecutionMode,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)


def _object(
    properties: dict,
    *,
    required: tuple[str, ...] = (),
) -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


SCREENPLAY_READ_TOOL_NAMES = (
    "getScreenplayProject",
    "getScreenplayDocument",
    "getSourceBookOverview",
    "getSourceCoveragePlan",
    "readSourceCoverageBatch",
    "searchSourceMaterial",
    "readSourcePassages",
    "getSourceCharacters",
    "getSourceWorldSettings",
)

SCREENPLAY_PROPOSAL_TOOL_NAMES = (
    "proposeSourceAnalysis",
    "proposeCreativeBrief",
    "proposeBeatSheet",
    "proposeEpisodeOutline",
    "proposeSceneList",
    "proposeSceneDraft",
    "proposeScreenplayReview",
    "proposeScreenplayRevision",
)

SCREENPLAY_TOOL_NAMES = (
    *SCREENPLAY_READ_TOOL_NAMES,
    *SCREENPLAY_PROPOSAL_TOOL_NAMES,
)


SCREENPLAY_TOOL_SCHEMAS = (
    ToolSchema(
        name="getScreenplayProject",
        description=(
            "读取当前绑定的剧本项目、阶段和文档版本摘要。"
            "项目由宿主绑定，不接受 projectId 参数。"
        ),
        parameters=_object({}),
    ),
    ToolSchema(
        name="getScreenplayDocument",
        description="读取当前剧本项目中的一个指定版本文档。",
        parameters=_object(
            {
                "documentId": {
                    "type": "string",
                    "description": "来自项目文档摘要的 document id",
                },
            },
            required=("documentId",),
        ),
    ),
    ToolSchema(
        name="getSourceBookOverview",
        description=(
            "读取当前项目绑定原作的书名、目录、大纲摘要和素材数量。"
            "不返回整本正文。"
        ),
        parameters=_object({}),
    ),
    ToolSchema(
        name="getSourceCoveragePlan",
        description=(
            "为当前锁定的原作章节生成确定性的长篇覆盖计划。"
            "返回最多 6 个连续批次及 planId；应先调用本工具，再在一个"
            "只读工具批次中并列调用每个 readSourceCoverageBatch。"
        ),
        parameters=_object({}),
    ),
    ToolSchema(
        name="readSourceCoverageBatch",
        description=(
            "读取长篇覆盖计划中的一个批次。短章节返回全文，长章节返回"
            "开头与结尾抽样，并明确标记 full 或 sampled。可以在同一轮"
            "并列读取计划中的全部批次；随后再用 readSourcePassages "
            "精读决定创作结论的关键章节。"
        ),
        parameters=_object(
            {
                "planId": {
                    "type": "string",
                    "description": "getSourceCoveragePlan 返回的 planId",
                },
                "batchNumber": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 6,
                },
            },
            required=("planId", "batchNumber"),
        ),
    ),
    ToolSchema(
        name="searchSourceMaterial",
        description=(
            "在当前绑定原作的人物、世界设定、大纲和章节正文中检索关键词，"
            "返回可继续精读的来源 id 与短摘录。"
        ),
        parameters=_object(
            {
                "query": {
                    "type": "string",
                    "description": "要检索的人物、事件、地点、主题或关键词",
                },
                "sourceTypes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "chapter",
                            "outline",
                            "character",
                            "setting",
                            "background",
                        ],
                    },
                    "description": "可选来源类型过滤",
                    "maxItems": 5,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "最多返回数量，默认 8",
                },
            },
            required=("query",),
        ),
    ),
    ToolSchema(
        name="readSourcePassages",
        description=(
            "按 searchSourceMaterial 或原作概览返回的 id 精读章节或大纲。"
            "一次最多读取 10 个来源。"
        ),
        parameters=_object(
            {
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": _object(
                        {
                            "sourceType": {
                                "type": "string",
                                "enum": ["chapter", "outline"],
                            },
                            "sourceId": {"type": "string"},
                        },
                        required=("sourceType", "sourceId"),
                    ),
                },
                "maxCharactersPerSource": {
                    "type": "integer",
                    "minimum": 500,
                    "maximum": 16000,
                    "description": "每个来源最大字符数，默认 8000",
                },
            },
            required=("sources",),
        ),
    ),
    ToolSchema(
        name="getSourceCharacters",
        description=(
            "读取当前绑定原作的人物卡。可按人物 id 或姓名过滤；"
            "不传过滤条件时返回前 20 个人物。"
        ),
        parameters=_object(
            {
                "characterIds": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 20,
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                },
            },
        ),
    ),
    ToolSchema(
        name="getSourceWorldSettings",
        description=(
            "读取当前绑定原作的故事背景和世界设定条目。"
            "可按设定 id、名称或类型过滤。"
        ),
        parameters=_object(
            {
                "entityIds": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 20,
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
                "entityType": {
                    "type": "string",
                    "enum": ["location", "faction", "item", "other"],
                },
                "includeBackground": {
                    "type": "boolean",
                    "description": "是否同时返回故事背景，默认 true",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                },
            },
        ),
    ),
    ToolSchema(
        name="proposeSourceAnalysis",
        description=(
            "基于当前项目锁定的原作范围，提交可追溯的素材分析提案。"
            "必须先读取原作概览和正文证据；该工具只生成待审阅提案，"
            "不会保存或接受文档。"
        ),
        parameters=_object(
            {
                "title": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "提案标题",
                },
                "analysis": _object(
                    {
                        "rangeSummary": {
                            "type": "string",
                            "maxLength": 4000,
                        },
                        "narrativeSummary": {
                            "type": "string",
                            "maxLength": 20000,
                        },
                        "coverage": _object(
                            {
                                "selectedChapterCount": {
                                    "type": "integer",
                                    "minimum": 0,
                                },
                                "readChapterIds": {
                                    "type": "array",
                                    "maxItems": 1000,
                                    "items": {"type": "string"},
                                },
                                "sampledChapterIds": {
                                    "type": "array",
                                    "maxItems": 1000,
                                    "items": {"type": "string"},
                                },
                                "limitations": {
                                    "type": "array",
                                    "maxItems": 100,
                                    "items": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                },
                            },
                            required=(
                                "selectedChapterCount",
                                "readChapterIds",
                                "sampledChapterIds",
                                "limitations",
                            ),
                        ),
                        "characters": {
                            "type": "array",
                            "maxItems": 100,
                            "items": _object(
                                {
                                    "name": {
                                        "type": "string",
                                        "maxLength": 300,
                                    },
                                    "role": {
                                        "type": "string",
                                        "maxLength": 2000,
                                    },
                                    "goal": {
                                        "type": "string",
                                        "maxLength": 3000,
                                    },
                                    "conflict": {
                                        "type": "string",
                                        "maxLength": 3000,
                                    },
                                },
                                required=("name", "role"),
                            ),
                        },
                        "plotEvents": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 300,
                            "items": _object(
                                {
                                    "order": {
                                        "type": "integer",
                                        "minimum": 1,
                                    },
                                    "event": {
                                        "type": "string",
                                        "maxLength": 6000,
                                    },
                                    "consequence": {
                                        "type": "string",
                                        "maxLength": 6000,
                                    },
                                },
                                required=("order", "event", "consequence"),
                            ),
                        },
                        "centralConflicts": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        "adaptationAssets": {
                            "type": "array",
                            "maxItems": 80,
                            "items": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        "continuityRisks": {
                            "type": "array",
                            "maxItems": 80,
                            "items": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        "openQuestions": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 300,
                            "items": _object(
                                {
                                    "sourceType": {
                                        "type": "string",
                                        "enum": [
                                            "book",
                                            "chapter",
                                            "outline",
                                            "character",
                                            "setting",
                                            "background",
                                        ],
                                    },
                                    "sourceId": {"type": "string"},
                                    "claim": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                },
                                required=("sourceType", "sourceId", "claim"),
                            ),
                        },
                    },
                    required=(
                        "rangeSummary",
                        "narrativeSummary",
                        "coverage",
                        "plotEvents",
                        "evidence",
                    ),
                ),
                "contentText": {
                    "type": "string",
                    "maxLength": 300000,
                    "description": "供用户阅读的完整 Markdown 范围分析",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=("analysis", "contentText"),
        ),
    ),
    ToolSchema(
        name="proposeCreativeBrief",
        description=(
            "向用户提交结构化创作简报提案。该工具只生成待审阅提案，"
            "不会保存或接受文档；必须由用户在界面中明确操作。书架改编"
            "必须给出成片规模、可追溯的逐条改编决策，并承接原作分析局限。"
        ),
        parameters=_object(
            {
                "title": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "提案标题",
                },
                "brief": _object(
                    {
                        "audience": {"type": "string", "maxLength": 2000},
                        "logline": {"type": "string", "maxLength": 4000},
                        "theme": {"type": "string", "maxLength": 4000},
                        "protagonist": {"type": "string", "maxLength": 4000},
                        "coreConflict": {"type": "string", "maxLength": 6000},
                        "formatPlan": _object(
                            {
                                "targetFormat": {
                                    "type": "string",
                                    "enum": [
                                        "短片",
                                        "电影",
                                        "单集剧",
                                        "连续剧",
                                        "竖屏短剧",
                                    ],
                                },
                                "targetDurationMinutes": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 600,
                                },
                                "episodeCount": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 1000,
                                },
                                "episodeDurationMinutes": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 300,
                                },
                                "scopeStrategy": {
                                    "type": "string",
                                    "maxLength": 4000,
                                },
                                "narrativeEndpoint": {
                                    "type": "string",
                                    "maxLength": 4000,
                                },
                            },
                            required=(
                                "targetFormat",
                                "scopeStrategy",
                                "narrativeEndpoint",
                            ),
                        ),
                        "adaptationDecisions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 100,
                            "items": _object(
                                {
                                    "id": {
                                        "type": "string",
                                        "maxLength": 120,
                                    },
                                    "action": {
                                        "type": "string",
                                        "enum": [
                                            "preserve",
                                            "compress",
                                            "merge",
                                            "omit",
                                            "reorder",
                                            "transform",
                                            "invent",
                                        ],
                                    },
                                    "subject": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                    "rationale": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                    "screenIntent": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                    "sourceAnchors": {
                                        "type": "array",
                                        "maxItems": 30,
                                        "items": _object(
                                            {
                                                "sourceType": {
                                                    "type": "string",
                                                    "enum": [
                                                        "book",
                                                        "chapter",
                                                        "outline",
                                                        "character",
                                                        "setting",
                                                        "background",
                                                    ],
                                                },
                                                "sourceId": {
                                                    "type": "string",
                                                    "maxLength": 300,
                                                },
                                            },
                                            required=(
                                                "sourceType",
                                                "sourceId",
                                            ),
                                        ),
                                    },
                                },
                                required=(
                                    "id",
                                    "action",
                                    "subject",
                                    "rationale",
                                    "screenIntent",
                                    "sourceAnchors",
                                ),
                            ),
                        },
                        "acknowledgedSourceLimitations": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                            "maxItems": 100,
                        },
                        "adaptationPrinciples": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 2000},
                            "maxItems": 20,
                        },
                        "openQuestions": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 2000},
                            "maxItems": 20,
                        },
                    },
                    required=("logline", "coreConflict"),
                ),
                "contentText": {
                    "type": "string",
                    "maxLength": 100000,
                    "description": "供用户阅读的完整 Markdown 提案",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "本项目内的上游文档 id",
                },
            },
            required=("brief", "contentText"),
        ),
    ),
    ToolSchema(
        name="proposeBeatSheet",
        description=(
            "向用户提交电影、短片或单集剧的结构化节拍表提案。"
            "每个节拍必须有稳定 id 和连续 order；decisionCoverage 必须"
            "完整说明创作简报中的每条改编决策落在哪些节拍。"
            "只生成待审阅提案，不会保存或接受。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "beats": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 80,
                    "items": _object(
                        {
                            "id": {"type": "string", "maxLength": 120},
                            "order": {"type": "integer", "minimum": 1},
                            "label": {"type": "string", "maxLength": 300},
                            "summary": {"type": "string", "maxLength": 6000},
                        },
                        required=("id", "order", "label", "summary"),
                    ),
                },
                "decisionCoverage": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _object(
                        {
                            "decisionId": {
                                "type": "string",
                                "maxLength": 120,
                            },
                            "structureUnitIds": {
                                "type": "array",
                                "maxItems": 80,
                                "items": {
                                    "type": "string",
                                    "maxLength": 120,
                                },
                            },
                            "implementation": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        required=(
                            "decisionId",
                            "structureUnitIds",
                            "implementation",
                        ),
                    ),
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 200000,
                    "description": "供用户阅读的完整 Markdown 提案",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=("beats", "decisionCoverage", "contentText"),
        ),
    ),
    ToolSchema(
        name="proposeEpisodeOutline",
        description=(
            "向用户提交连续剧或竖屏短剧的结构化分集提案。"
            "每集必须有稳定 id 和连续 number，并与简报集数一致；"
            "decisionCoverage 必须完整说明每条改编决策落在哪些集。"
            "只生成待审阅提案，不会保存或接受。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "episodes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": _object(
                        {
                            "id": {"type": "string", "maxLength": 120},
                            "number": {"type": "integer", "minimum": 1},
                            "title": {"type": "string", "maxLength": 300},
                            "summary": {"type": "string", "maxLength": 10000},
                        },
                        required=("id", "number", "title", "summary"),
                    ),
                },
                "decisionCoverage": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _object(
                        {
                            "decisionId": {
                                "type": "string",
                                "maxLength": 120,
                            },
                            "structureUnitIds": {
                                "type": "array",
                                "maxItems": 100,
                                "items": {
                                    "type": "string",
                                    "maxLength": 120,
                                },
                            },
                            "implementation": {
                                "type": "string",
                                "maxLength": 4000,
                            },
                        },
                        required=(
                            "decisionId",
                            "structureUnitIds",
                            "implementation",
                        ),
                    ),
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 300000,
                    "description": "供用户阅读的完整 Markdown 提案",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=("episodes", "decisionCoverage", "contentText"),
        ),
    ),
    ToolSchema(
        name="proposeSceneList",
        description=(
            "基于已接受的节拍表或分集结构，向用户提交结构化场景表。"
            "每个场景必须有稳定 id，并通过 structureUnitIds 映射结构单元；"
            "所有结构单元必须至少被一个场景承接。连续剧场景必须且只能"
            "归属一个分集，episodeNumber 必须与映射一致。后续逐场正文"
            "会引用场景 id。"
            "该工具只生成待审阅提案，不会保存或接受。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "scenes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 300,
                    "items": _object(
                        {
                            "id": {"type": "string", "maxLength": 100},
                            "order": {"type": "integer", "minimum": 1},
                            "heading": {"type": "string", "maxLength": 500},
                            "episodeNumber": {
                                "type": "integer",
                                "minimum": 1,
                            },
                            "structureUnitIds": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": {
                                    "type": "string",
                                    "maxLength": 120,
                                },
                            },
                            "location": {"type": "string", "maxLength": 500},
                            "timeOfDay": {"type": "string", "maxLength": 200},
                            "characters": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "maxLength": 200,
                                },
                                "maxItems": 30,
                            },
                            "objective": {"type": "string", "maxLength": 3000},
                            "conflict": {"type": "string", "maxLength": 4000},
                            "turn": {"type": "string", "maxLength": 4000},
                            "synopsis": {"type": "string", "maxLength": 10000},
                        },
                        required=(
                            "id",
                            "order",
                            "heading",
                            "structureUnitIds",
                            "objective",
                            "conflict",
                            "turn",
                            "synopsis",
                        ),
                    ),
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 500000,
                    "description": "供用户阅读的完整 Markdown 场景表",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=("scenes", "contentText"),
        ),
    ),
    ToolSchema(
        name="proposeSceneDraft",
        description=(
            "基于已接受的场景表逐场创作剧本正文。contentText 必须是"
            "截至本场的完整滚动整稿，而不是只有当前场；新版本会继承先前"
            "已接受正文。execution 必须说明本场如何完成目标、推进冲突、"
            "实现转折以及留下何种连续性状态；历史 sceneExecutions 不可改写。"
            "使用 Fountain 场景标题，并以 @人物名 标记角色提示。"
            "该工具只生成待审阅提案，不会保存或接受。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "sceneId": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "已接受场景表中的场景 id",
                },
                "sceneHeading": {"type": "string", "maxLength": 500},
                "execution": _object(
                    {
                        "objectiveResult": {
                            "type": "string",
                            "maxLength": 6000,
                        },
                        "conflictResult": {
                            "type": "string",
                            "maxLength": 6000,
                        },
                        "turnResult": {
                            "type": "string",
                            "maxLength": 6000,
                        },
                        "continuityState": {
                            "type": "string",
                            "maxLength": 6000,
                        },
                        "unresolvedNotes": {
                            "type": "array",
                            "maxItems": 30,
                            "items": {
                                "type": "string",
                                "maxLength": 3000,
                            },
                        },
                    },
                    required=(
                        "objectiveResult",
                        "conflictResult",
                        "turnResult",
                        "continuityState",
                        "unresolvedNotes",
                    ),
                ),
                "completedSceneIds": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 300,
                    "items": {"type": "string", "maxLength": 100},
                },
                "isComplete": {
                    "type": "boolean",
                    "description": "仅当场景表中所有场景均已写入整稿时为 true",
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 2000000,
                    "description": "截至当前场的完整 Fountain 兼容剧本文本",
                },
                "notes": {"type": "string", "maxLength": 20000},
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=(
                "sceneId",
                "sceneHeading",
                "execution",
                "completedSceneIds",
                "isComplete",
                "contentText",
            ),
        ),
    ),
    ToolSchema(
        name="proposeScreenplayReview",
        description=(
            "审阅当前已接受的完整剧本，提交结构化审阅报告。"
            "每个问题必须定位到场景 id 和具体 sceneExecutions 字段，"
            "并给出可供复审的验收标准。如果当前稿是修订稿，还必须用 "
            "verificationResults 逐项核验上一轮问题。只生成待审阅提案。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "summary": {"type": "string", "maxLength": 20000},
                "strengths": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 3000},
                    "maxItems": 30,
                },
                "issues": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _object(
                        {
                            "id": {"type": "string", "maxLength": 100},
                            "severity": {
                                "type": "string",
                                "enum": ["critical", "major", "minor"],
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "continuity",
                                    "character",
                                    "structure",
                                    "pacing",
                                    "dialogue",
                                    "format",
                                ],
                            },
                            "sceneIds": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "maxLength": 100},
                                "maxItems": 30,
                            },
                            "executionFields": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 5,
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "objectiveResult",
                                        "conflictResult",
                                        "turnResult",
                                        "continuityState",
                                        "unresolvedNotes",
                                    ],
                                },
                            },
                            "problem": {"type": "string", "maxLength": 10000},
                            "recommendation": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                            "acceptanceCriteria": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                        },
                        required=(
                            "id",
                            "severity",
                            "category",
                            "sceneIds",
                            "executionFields",
                            "problem",
                            "recommendation",
                            "acceptanceCriteria",
                        ),
                    ),
                },
                "verificationResults": {
                    "type": "array",
                    "maxItems": 100,
                    "description": (
                        "仅复审修订稿时使用；必须逐项核验上一轮全部问题"
                    ),
                    "items": _object(
                        {
                            "issueId": {
                                "type": "string",
                                "maxLength": 100,
                            },
                            "status": {
                                "type": "string",
                                "enum": [
                                    "verified",
                                    "still_open",
                                    "regressed",
                                ],
                            },
                            "verificationEvidence": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                        },
                        required=(
                            "issueId",
                            "status",
                            "verificationEvidence",
                        ),
                    ),
                },
                "verdict": {
                    "type": "string",
                    "enum": ["ready", "revise", "major_rework"],
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 300000,
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=(
                "summary",
                "strengths",
                "issues",
                "verdict",
                "contentText",
            ),
        ),
    ),
    ToolSchema(
        name="proposeScreenplayRevision",
        description=(
            "根据用户已接受的审阅报告修订完整剧本。contentText 必须是"
            "修订后的完整 Fountain 兼容剧本，不是局部补丁。必须逐项回应"
            "全部审阅问题，并重评每个受影响场景的 sceneExecutions；"
            "以 @人物名 标记角色提示。只生成待审阅提案。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "issueResolutions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": _object(
                        {
                            "issueId": {
                                "type": "string",
                                "maxLength": 100,
                            },
                            "status": {
                                "type": "string",
                                "enum": [
                                    "resolved",
                                    "partially_resolved",
                                ],
                            },
                            "resolutionEvidence": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                        },
                        required=(
                            "issueId",
                            "status",
                            "resolutionEvidence",
                        ),
                    ),
                },
                "executionUpdates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "items": _object(
                        {
                            "sceneId": {
                                "type": "string",
                                "maxLength": 100,
                            },
                            "objectiveResult": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                            "conflictResult": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                            "turnResult": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                            "continuityState": {
                                "type": "string",
                                "maxLength": 10000,
                            },
                            "unresolvedNotes": {
                                "type": "array",
                                "maxItems": 100,
                                "items": {
                                    "type": "string",
                                    "maxLength": 3000,
                                },
                            },
                        },
                        required=(
                            "sceneId",
                            "objectiveResult",
                            "conflictResult",
                            "turnResult",
                            "continuityState",
                            "unresolvedNotes",
                        ),
                    ),
                },
                "revisionSummary": {
                    "type": "string",
                    "maxLength": 30000,
                },
                "contentText": {
                    "type": "string",
                    "maxLength": 2000000,
                    "description": "修订后的完整 Fountain 兼容剧本",
                },
                "derivedFromIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            required=(
                "issueResolutions",
                "executionUpdates",
                "revisionSummary",
                "contentText",
            ),
        ),
    ),
)


SCREENPLAY_TOOL_POLICIES = {
    name: ToolPolicy(ToolExecutionMode.READ, title)
    for name, title in (
        ("getScreenplayProject", "读取剧本项目"),
        ("getScreenplayDocument", "读取剧本文档"),
        ("getSourceBookOverview", "读取原作概览"),
        ("getSourceCoveragePlan", "规划长篇阅读批次"),
        ("readSourceCoverageBatch", "读取长篇覆盖批次"),
        ("searchSourceMaterial", "检索原作素材"),
        ("readSourcePassages", "精读原作片段"),
        ("getSourceCharacters", "读取原作人物"),
        ("getSourceWorldSettings", "读取原作世界设定"),
    )
}
SCREENPLAY_TOOL_POLICIES.update({
    name: ToolPolicy(
        ToolExecutionMode.PROPOSE,
        title,
        ToolRiskLevel.WRITE,
    )
    for name, title in (
        ("proposeSourceAnalysis", "提交原作范围分析"),
        ("proposeCreativeBrief", "提交创作简报提案"),
        ("proposeBeatSheet", "提交节拍表提案"),
        ("proposeEpisodeOutline", "提交分集结构提案"),
        ("proposeSceneList", "提交场景表提案"),
        ("proposeSceneDraft", "提交逐场正文提案"),
        ("proposeScreenplayReview", "提交剧本审阅报告"),
        ("proposeScreenplayRevision", "提交完整剧本修订"),
    )
})


SCREENPLAY_TOOL_CONTEXT_CONTRACTS = {
    name: ToolContextContract(
        prerequisite_tools=(
            ("getSourceCoveragePlan",)
            if name == "readSourceCoverageBatch"
            else ("readSourceCoverageBatch",)
            if name == "proposeSourceAnalysis"
            else ()
        ),
        required_context_blocks=("screenplay_project",),
        evidence_kinds=(
            ("screenplay_project",)
            if (
                name.startswith("getScreenplay")
                or name in SCREENPLAY_PROPOSAL_TOOL_NAMES
            )
            else ("source_material",)
        ),
        produces=(name,),
    )
    for name in SCREENPLAY_TOOL_NAMES
}
