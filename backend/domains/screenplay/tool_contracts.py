"""Model-visible contracts and host policies for screenplay tools."""

from __future__ import annotations

from agent_core.contracts import (
    ToolContextContract,
    ToolDataContract,
    ToolExecutionMode,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema as CoreToolSchema,
)
from domains.screenplay.payload_limits import SCENE_DRAFT_PAYLOAD_LIMITS
from domains.screenplay.tool_display_names import (
    SCREENPLAY_TOOL_DISPLAY_NAMES,
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


def _tool_schema(
    *,
    name: str,
    description: str,
    parameters: dict,
) -> CoreToolSchema:
    return CoreToolSchema(
        name=name,
        description=description,
        parameters=parameters,
        display_names=SCREENPLAY_TOOL_DISPLAY_NAMES.get(name, {}),
    )


def _single_enum(value: str) -> dict:
    return {"type": "string", "enum": [value]}


def _scene_execution_schema() -> dict:
    return _object(
        {
            "objectiveResult": {
                "type": "string",
                "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.execution_result_chars,
            },
            "conflictResult": {
                "type": "string",
                "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.execution_result_chars,
            },
            "turnResult": {
                "type": "string",
                "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.execution_result_chars,
            },
            "continuityState": {
                "type": "string",
                "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.execution_result_chars,
            },
            "unresolvedNotes": {
                "type": "array",
                "maxItems": SCENE_DRAFT_PAYLOAD_LIMITS.unresolved_notes,
                "items": {
                    "type": "string",
                    "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.unresolved_note_chars,
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
    )


def _source_analysis_item_schema() -> dict:
    """Return the exact discriminated contract enforced by the artifact host.

    Keeping the variants explicit prevents the model-visible schema from
    advertising fields that the selected ``itemType`` cannot actually use.
    """

    text_item_variants = [
        _object(
            {
                "itemType": _single_enum(value),
                "text": {"type": "string", "maxLength": 4000},
            },
            required=("itemType", "text"),
        )
        for value in (
            "central_conflict",
            "adaptation_asset",
            "continuity_risk",
            "open_question",
        )
    ]
    return {
        "anyOf": [
            _object(
                {
                    "itemType": _single_enum("character"),
                    "name": {"type": "string", "maxLength": 300},
                    "role": {"type": "string", "maxLength": 2000},
                    "goal": {"type": "string", "maxLength": 3000},
                    "conflict": {"type": "string", "maxLength": 3000},
                },
                required=("itemType", "name", "role"),
            ),
            _object(
                {
                    "itemType": _single_enum("plot_event"),
                    "event": {"type": "string", "maxLength": 6000},
                    "consequence": {"type": "string", "maxLength": 6000},
                },
                required=("itemType", "event", "consequence"),
            ),
            *text_item_variants,
            _object(
                {
                    "itemType": _single_enum("evidence"),
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
                    "sourceId": {"type": "string", "maxLength": 500},
                    "claim": {"type": "string", "maxLength": 4000},
                },
                required=(
                    "itemType",
                    "sourceType",
                    "sourceId",
                    "claim",
                ),
            ),
        ],
    }


def _creative_brief_item_schema() -> dict:
    format_common = {
        "scopeStrategy": {"type": "string", "maxLength": 4000},
        "narrativeEndpoint": {"type": "string", "maxLength": 4000},
    }
    format_plan = {
        "anyOf": [
            _object(
                {
                    **format_common,
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
                },
                required=(
                    "scopeStrategy",
                    "narrativeEndpoint",
                    "episodeCount",
                    "episodeDurationMinutes",
                ),
            ),
            _object(
                {
                    **format_common,
                    "targetDurationMinutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 600,
                    },
                },
                required=(
                    "scopeStrategy",
                    "narrativeEndpoint",
                    "targetDurationMinutes",
                ),
            ),
        ],
    }
    source_anchor = _object(
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
            "sourceId": {"type": "string", "maxLength": 300},
        },
        required=("sourceType", "sourceId"),
    )
    return {
        "anyOf": [
            _object(
                {
                    "itemType": _single_enum("brief_content"),
                    "audience": {"type": "string", "maxLength": 2000},
                    "logline": {"type": "string", "maxLength": 4000},
                    "theme": {"type": "string", "maxLength": 4000},
                    "protagonist": {"type": "string", "maxLength": 4000},
                    "coreConflict": {"type": "string", "maxLength": 6000},
                    "formatPlan": format_plan,
                    "adaptationPrinciples": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "maxLength": 2000},
                    },
                    "openQuestions": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "maxLength": 2000},
                    },
                },
                required=("itemType", "logline", "coreConflict"),
            ),
            _object(
                {
                    "itemType": _single_enum("adaptation_decision"),
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "id": {"type": "string", "maxLength": 120},
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
                    "subject": {"type": "string", "maxLength": 4000},
                    "rationale": {"type": "string", "maxLength": 4000},
                    "screenIntent": {"type": "string", "maxLength": 4000},
                    "sourceAnchors": {
                        "type": "array",
                        "maxItems": 30,
                        "items": source_anchor,
                    },
                },
                required=(
                    "itemType",
                    "index",
                    "id",
                    "action",
                    "subject",
                    "rationale",
                    "screenIntent",
                    "sourceAnchors",
                ),
            ),
        ],
    }


def _structure_item_schema() -> dict:
    return {
        "anyOf": [
            _object(
                {
                    "itemType": _single_enum("structure_unit"),
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                    },
                    "id": {"type": "string", "maxLength": 120},
                    "title": {"type": "string", "maxLength": 300},
                    "summary": {"type": "string", "maxLength": 10000},
                },
                required=("itemType", "index", "id", "title", "summary"),
            ),
            _object(
                {
                    "itemType": _single_enum("decision_coverage"),
                    "decisionId": {"type": "string", "maxLength": 120},
                    "structureUnitIds": {
                        "type": "array",
                        "maxItems": 1000,
                        "items": {"type": "string", "maxLength": 120},
                    },
                    "implementation": {
                        "type": "string",
                        "maxLength": 4000,
                    },
                },
                required=(
                    "itemType",
                    "decisionId",
                    "structureUnitIds",
                    "implementation",
                ),
            ),
        ],
    }


def _review_item_schema() -> dict:
    return {
        "anyOf": [
            _object(
                {
                    "itemType": _single_enum("review_summary"),
                    "summary": {"type": "string", "maxLength": 20000},
                    "strengths": {
                        "type": "array",
                        "maxItems": 30,
                        "items": {"type": "string", "maxLength": 3000},
                    },
                },
                required=("itemType", "summary", "strengths"),
            ),
            _object(
                {
                    "itemType": _single_enum("review_issue"),
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
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
                        "maxItems": 30,
                        "items": {"type": "string", "maxLength": 100},
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
                    "itemType",
                    "index",
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
            _object(
                {
                    "itemType": _single_enum("verification_result"),
                    "index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "status": {
                        "type": "string",
                        "enum": ["verified", "still_open", "regressed"],
                    },
                    "verificationEvidence": {
                        "type": "string",
                        "maxLength": 10000,
                    },
                },
                required=(
                    "itemType",
                    "index",
                    "status",
                    "verificationEvidence",
                ),
            ),
        ],
    }


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
    "beginSourceAnalysisArtifact",
    "appendSourceAnalysisBatch",
    "finalizeSourceAnalysisProposal",
    "beginCreativeBriefArtifact",
    "appendCreativeBriefBatch",
    "finalizeCreativeBriefProposal",
    "beginScreenplayStructureArtifact",
    "appendScreenplayStructureBatch",
    "finalizeScreenplayStructureProposal",
    "beginSceneListArtifact",
    "appendSceneListBatch",
    "finalizeSceneListProposal",
    "proposeSceneDraft",
    "beginScreenplayReviewArtifact",
    "appendScreenplayReviewBatch",
    "finalizeScreenplayReviewProposal",
    "beginScreenplayRevisionArtifact",
    "appendScreenplayRevisionBatch",
    "appendScreenplayRevisionResolutionBatch",
    "finalizeScreenplayRevisionProposal",
)

SCREENPLAY_TOOL_NAMES = (
    *SCREENPLAY_READ_TOOL_NAMES,
    *SCREENPLAY_PROPOSAL_TOOL_NAMES,
)


SCREENPLAY_TOOL_SCHEMAS = (
    _tool_schema(
        name="getScreenplayProject",
        description=(
            "读取当前绑定的剧本项目、阶段和文档版本摘要。"
            "项目由宿主绑定，不接受 projectId 参数。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
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
    _tool_schema(
        name="getSourceBookOverview",
        description=(
            "读取当前项目绑定原作的书名、目录、大纲摘要和素材数量。"
            "不返回整本正文。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="getSourceCoveragePlan",
        description=(
            "为当前锁定的原作章节生成确定性的长篇覆盖计划。"
            "返回最多 6 个连续批次及 planId；应先调用本工具，再在一个"
            "只读工具批次中并列调用每个 readSourceCoverageBatch。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
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
    _tool_schema(
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
    _tool_schema(
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
    _tool_schema(
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
    _tool_schema(
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
    _tool_schema(
        name="beginSourceAnalysisArtifact",
        description=(
            "开始可恢复的原作范围分析 Artifact。只提交两段总述、可选的语义"
            "局限和各类分析条目数量；章节范围、实际读取章节和来源凭证均由宿主"
            "根据当前 Run 的真实读取记录生成，不得在本工具中重发。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "rangeSummary": {"type": "string", "maxLength": 4000},
                "narrativeSummary": {
                    "type": "string",
                    "maxLength": 20000,
                },
                "coverageLimitations": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "maxLength": 2000},
                },
                "expectedItemCounts": _object(
                    {
                        "character": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                        },
                        "plot_event": {
                            "type": "integer", "minimum": 1, "maximum": 300,
                        },
                        "central_conflict": {
                            "type": "integer", "minimum": 0, "maximum": 50,
                        },
                        "adaptation_asset": {
                            "type": "integer", "minimum": 0, "maximum": 80,
                        },
                        "continuity_risk": {
                            "type": "integer", "minimum": 0, "maximum": 80,
                        },
                        "open_question": {
                            "type": "integer", "minimum": 0, "maximum": 50,
                        },
                        "evidence": {
                            "type": "integer", "minimum": 1, "maximum": 300,
                        },
                    },
                    required=(
                        "character",
                        "plot_event",
                        "central_conflict",
                        "adaptation_asset",
                        "continuity_risk",
                        "open_question",
                        "evidence",
                    ),
                ),
            },
            required=(
                "rangeSummary",
                "narrativeSummary",
                "expectedItemCounts",
            ),
        ),
    ),
    _tool_schema(
        name="appendSourceAnalysisBatch",
        description=(
            "向当前原作分析 Artifact 追加最多 20 个条目。同一模型轮次可以返回"
            "最多 8 个同名调用。条目索引和已提交游标由宿主按 itemType 自动维护，"
            "不要自行计算或传入 index；超过该类型声明容量的尾部条目会被宿主明确"
            "丢弃并在回执中报告。evidence 只能引用当前 Run 已实际读取并由工具回执"
            "返回的 sourceType/sourceId。"
        ),
        parameters=_object(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _source_analysis_item_schema(),
                },
            },
            required=("items",),
        ),
    ),
    _tool_schema(
        name="finalizeSourceAnalysisProposal",
        description=(
            "全部分析条目提交完成后最终确认 Artifact。宿主校验数量、宿主管理的连续索引、"
            "来源凭证和章节覆盖，随后组装可审阅的原作范围分析；不接受内容参数。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="beginCreativeBriefArtifact",
        description=(
            "开始可恢复的创作简报 Artifact。只声明改编决策数量；宿主绑定"
            "项目形态、当前已接受原作分析、来源证据清单、阅读局限与文档谱系。"
            "书架改编必须至少一条决策，原创项目必须声明 0。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "expectedDecisionCount": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            required=("expectedDecisionCount",),
        ),
    ),
    _tool_schema(
        name="appendCreativeBriefBatch",
        description=(
            "向创作简报 Artifact 追加最多 8 个条目。必须且只能提交一个 "
            "brief_content；其 formatPlan 不传 targetFormat，项目形态由宿主"
            "注入。书架改编再按连续 index 分批提交 adaptation_decision；"
            "阅读局限由宿主继承，不作为模型参数。"
        ),
        parameters=_object(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": _creative_brief_item_schema(),
                },
            },
            required=("items",),
        ),
    ),
    _tool_schema(
        name="finalizeCreativeBriefProposal",
        description=(
            "全部简报内容与改编决策条目提交后最终确认 Artifact。宿主校验"
            "连续索引、来源锚点、成片规模和原作分析版本，自动承接阅读局限，"
            "并生成可审阅创作简报；不接受内容参数。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="beginScreenplayStructureArtifact",
        description=(
            "开始当前项目形态对应的结构 Artifact。只声明完整结构单元总数；"
            "宿主从项目形态选择节拍表或分集结构，并绑定当前已接受创作简报、"
            "改编决策清单和版本谱系。连续剧集数必须与创作简报一致。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "expectedUnitCount": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            required=("expectedUnitCount",),
        ),
    ),
    _tool_schema(
        name="appendScreenplayStructureBatch",
        description=(
            "向当前结构 Artifact 追加最多 20 个条目。structure_unit 使用连续"
            " index、稳定 id、title 和 summary；decision_coverage 只提交已"
            "接受创作简报中决策的结构落点，不重传决策正文。可混合两种条目，"
            "也可在同一模型轮次多次调用。"
        ),
        parameters=_object(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": _structure_item_schema(),
                },
            },
            required=("items",),
        ),
    ),
    _tool_schema(
        name="finalizeScreenplayStructureProposal",
        description=(
            "在全部结构单元和宿主声明的改编决策覆盖条目提交后，最终确认结构 "
            "Artifact。宿主校验连续序号、唯一 ID、成片规模、全部决策落点和"
            "创作简报版本，并组装为可审阅的节拍表或分集结构；不接受正文参数。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="beginSceneListArtifact",
        description=(
            "开始一个可恢复的场景表 Artifact。先声明完整场景总数；宿主绑定"
            "当前已接受结构和运行身份。成功后按回执调用 appendSceneListBatch，"
            "不要在本工具中发送任何场景。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "expectedSceneCount": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            required=("expectedSceneCount",),
        ),
    ),
    _tool_schema(
        name="appendSceneListBatch",
        description=(
            "向当前运行已开始的场景表 Artifact 追加一个批次。每批最多 10 场；"
            "每个场景必须有稳定 id、连续 order，并通过 structureUnitIds 映射"
            "已接受结构单元。连续剧的 episodeNumber 可省略并由宿主根据唯一的"
            "structureUnitId 推导；若提供则必须一致。根据回执的 "
            "remainingItemCount 继续追加；不要重发"
            "已经提交的场景。同一模型轮次可以返回最多 8 个同名批次调用；"
            "宿主会先完整预检，再按序持久化并返回每批回执。"
        ),
        parameters=_object(
            {
                "scenes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
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
            },
            required=("scenes",),
        ),
    ),
    _tool_schema(
        name="finalizeSceneListProposal",
        description=(
            "在场景批次数量完整后最终确认 Artifact，并向用户提交一份可审阅"
            "场景表。宿主会检查顺序、重复 id、分集归属和结构单元覆盖；"
            "本工具不接受场景内容。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="proposeSceneDraft",
        description=(
            "基于已接受的场景表创作宿主确定的连续场景批次。sceneText 与 "
            "execution 表示批次第一场；其余场景按顺序放入 additionalScenes。"
            "数量必须与宿主的 requestedSceneCount 完全一致，模型不能指定或跳过"
            "场景 id。宿主会从已接受版本追加历史正文、绑定场景 id 与标题，并"
            "计算累计进度。每场 execution 必须说明如何完成目标、推进冲突、"
            "实现转折以及留下何种连续性状态。"
            "使用 Fountain 场景标题，并以 @人物名 标记角色提示。"
            "该工具只生成待审阅提案，不会保存或接受。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "execution": _scene_execution_schema(),
                "sceneText": {
                    "type": "string",
                    "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars,
                    "description": "只包含当前场的 Fountain 兼容剧本文本",
                },
                "additionalScenes": {
                    "type": "array",
                    "maxItems": 19,
                    "items": _object(
                        {
                            "execution": _scene_execution_schema(),
                            "sceneText": {
                                "type": "string",
                                "maxLength": (
                                    SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars
                                ),
                            },
                        },
                        required=("execution", "sceneText"),
                    ),
                },
                "notes": {
                    "type": "string",
                    "maxLength": SCENE_DRAFT_PAYLOAD_LIMITS.draft_notes_chars,
                },
            },
            required=(
                "execution",
                "sceneText",
            ),
        ),
    ),
    _tool_schema(
        name="beginScreenplayReviewArtifact",
        description=(
            "开始当前完整剧本的可恢复审阅 Artifact。只声明结论和当前问题"
            "数量；宿主绑定完整稿、可引用场景、执行记录及上一轮审阅核验清单。"
            "ready 必须声明 0 个问题，revise 或 major_rework 至少 1 个。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "verdict": {
                    "type": "string",
                    "enum": ["ready", "revise", "major_rework"],
                },
                "expectedIssueCount": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            required=("verdict", "expectedIssueCount"),
        ),
    ),
    _tool_schema(
        name="appendScreenplayReviewBatch",
        description=(
            "向审阅 Artifact 追加最多 8 个条目。必须且只能提交一个 "
            "review_summary；问题按连续 index 提交 review_issue。复审时按宿主"
            "返回的 verificationIssueIds 顺序提交 verification_result，只传 "
            "index、状态与核验证据，不重传上一轮问题 ID 或验收标准。"
        ),
        parameters=_object(
            {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": _review_item_schema(),
                },
            },
            required=("items",),
        ),
    ),
    _tool_schema(
        name="finalizeScreenplayReviewProposal",
        description=(
            "全部审阅摘要、问题和必需复审核验提交后最终确认 Artifact。宿主"
            "校验场景引用、执行字段、上一轮问题映射及 ready/revise 结论，"
            "并生成可审阅报告；不接受内容参数。"
        ),
        parameters=_object({}),
    ),
    _tool_schema(
        name="beginScreenplayRevisionArtifact",
        description=(
            "根据用户已接受的审阅报告开始可恢复的修订 Artifact。宿主从审阅"
            "报告推导必须修订的场景和必须回应的问题；只有确因连续性联动需要"
            "额外改动其他场景时，才在 additionalSceneIds 中声明。本工具不接收"
            "问题回写、场景执行记录或剧本正文。"
        ),
        parameters=_object(
            {
                "title": {"type": "string", "maxLength": 160},
                "additionalSceneIds": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "maxLength": 100},
                    "description": (
                        "审阅问题未直接绑定、但因连续性联动也必须修改的场景 id"
                    ),
                },
                "revisionSummary": {
                    "type": "string",
                    "maxLength": 30000,
                },
            },
            required=("revisionSummary",),
        ),
    ),
    _tool_schema(
        name="appendScreenplayRevisionBatch",
        description=(
            "向当前修订 Artifact 追加受影响场景的新正文。每次调用只发送 1 场，"
            "同一模型轮次可以返回最多 8 个同名批次调用。只发送"
            "beginScreenplayRevisionArtifact 回执要求重评的场景；未受影响场景由"
            "宿主从已接受版本复用，不得重发整部剧本。"
        ),
        parameters=_object(
            {
                "revisedScenes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": _object(
                        {
                            "sceneId": {
                                "type": "string",
                                "maxLength": 100,
                            },
                            "sceneText": {
                                "type": "string",
                                "maxLength": 14000,
                                "description": "该场完整的 Fountain 兼容修订正文",
                            },
                            "execution": _object(
                                {
                                    "objectiveResult": {
                                        "type": "string",
                                        "maxLength": 2000,
                                    },
                                    "conflictResult": {
                                        "type": "string",
                                        "maxLength": 2000,
                                    },
                                    "turnResult": {
                                        "type": "string",
                                        "maxLength": 2000,
                                    },
                                    "continuityState": {
                                        "type": "string",
                                        "maxLength": 2000,
                                    },
                                    "unresolvedNotes": {
                                        "type": "array",
                                        "maxItems": 10,
                                        "items": {
                                            "type": "string",
                                            "maxLength": 500,
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
                        },
                        required=("sceneId", "sceneText", "execution"),
                    ),
                },
            },
            required=("revisedScenes",),
        ),
    ),
    _tool_schema(
        name="appendScreenplayRevisionResolutionBatch",
        description=(
            "向当前修订 Artifact 追加审阅问题回写。每次最多回应 8 个问题；"
            "必须覆盖 beginScreenplayRevisionArtifact 回执要求的全部问题 id，"
            "不得传入剧本正文或宿主已经保存的审阅问题原文。"
        ),
        parameters=_object(
            {
                "issueResolutions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
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
                                "maxLength": 2500,
                            },
                        },
                        required=(
                            "issueId",
                            "status",
                            "resolutionEvidence",
                        ),
                    ),
                },
            },
            required=("issueResolutions",),
        ),
    ),
    _tool_schema(
        name="finalizeScreenplayRevisionProposal",
        description=(
            "在全部受影响场景正文、执行记录和审阅问题回写提交后最终确认修订 "
            "Artifact。宿主按场景表顺序复用未修改正文、替换已修改场景，并生成"
            "完整可审阅修订稿；本工具不接受正文参数。"
        ),
        parameters=_object({}),
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
        ("beginSourceAnalysisArtifact", "开始原作分析分批提案"),
        ("appendSourceAnalysisBatch", "追加原作分析条目批次"),
        ("finalizeSourceAnalysisProposal", "提交原作范围分析"),
        ("beginCreativeBriefArtifact", "开始创作简报分批提案"),
        ("appendCreativeBriefBatch", "追加创作简报条目批次"),
        ("finalizeCreativeBriefProposal", "提交创作简报提案"),
        ("beginScreenplayStructureArtifact", "开始结构分批提案"),
        ("appendScreenplayStructureBatch", "追加结构条目批次"),
        ("finalizeScreenplayStructureProposal", "提交剧本结构提案"),
        ("beginSceneListArtifact", "开始场景表分批提案"),
        ("appendSceneListBatch", "追加场景表批次"),
        ("finalizeSceneListProposal", "提交场景表提案"),
        ("proposeSceneDraft", "提交正文批次提案"),
        ("beginScreenplayReviewArtifact", "开始剧本审阅分批提案"),
        ("appendScreenplayReviewBatch", "追加剧本审阅条目批次"),
        ("finalizeScreenplayReviewProposal", "提交剧本审阅报告"),
        ("beginScreenplayRevisionArtifact", "开始剧本修订分批提案"),
        ("appendScreenplayRevisionBatch", "追加修订场景批次"),
        (
            "appendScreenplayRevisionResolutionBatch",
            "追加审阅问题回写批次",
        ),
        ("finalizeScreenplayRevisionProposal", "提交完整剧本修订"),
    )
})


SCREENPLAY_TOOL_CONTEXT_CONTRACTS = {
    name: ToolContextContract(
        prerequisite_tools=(
            ("getSourceCoveragePlan",)
            if name == "readSourceCoverageBatch"
            else ("readSourceCoverageBatch",)
            if name == "beginSourceAnalysisArtifact"
            else ("beginSourceAnalysisArtifact",)
            if name == "appendSourceAnalysisBatch"
            else ("appendSourceAnalysisBatch",)
            if name == "finalizeSourceAnalysisProposal"
            else ("beginCreativeBriefArtifact",)
            if name == "appendCreativeBriefBatch"
            else ("appendCreativeBriefBatch",)
            if name == "finalizeCreativeBriefProposal"
            else ("beginScreenplayStructureArtifact",)
            if name == "appendScreenplayStructureBatch"
            else ("appendScreenplayStructureBatch",)
            if name == "finalizeScreenplayStructureProposal"
            else ("beginSceneListArtifact",)
            if name == "appendSceneListBatch"
            else ("appendSceneListBatch",)
            if name == "finalizeSceneListProposal"
            else ("beginScreenplayReviewArtifact",)
            if name == "appendScreenplayReviewBatch"
            else ("appendScreenplayReviewBatch",)
            if name == "finalizeScreenplayReviewProposal"
            else ("beginScreenplayRevisionArtifact",)
            if name in {
                "appendScreenplayRevisionBatch",
                "appendScreenplayRevisionResolutionBatch",
            }
            else (
                "appendScreenplayRevisionBatch",
                "appendScreenplayRevisionResolutionBatch",
            )
            if name == "finalizeScreenplayRevisionProposal"
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


_HOST_BOUND_SCOPE_PATHS = ("projectId", "bookId", "sourceBookId")
_HOST_DERIVED_PROPOSAL_PATHS = (
    "contentText",
    "schemaVersion",
    "generatedBy",
    "documentKind",
    "derivedFromIds",
)
SCREENPLAY_TOOL_DATA_CONTRACTS = {
    name: ToolDataContract()
    for name in SCREENPLAY_TOOL_NAMES
}
SCREENPLAY_TOOL_DATA_CONTRACTS.update({
    "beginSourceAnalysisArtifact": ToolDataContract(
        model_owned_paths=(
            "title",
            "rangeSummary",
            "narrativeSummary",
            "coverageLimitations",
            "expectedItemCounts",
        ),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "artifactId",
            "coverage",
            "allowedSourceKeys",
            "previousAnalysisId",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendSourceAnalysisBatch": ToolDataContract(
        model_owned_paths=("items",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeSourceAnalysisProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "coverage",
            "artifactRef",
            "sourceAnalysisArtifactId",
        ),
        payload_mode="resource_reference",
    ),
    "beginCreativeBriefArtifact": ToolDataContract(
        model_owned_paths=("title", "expectedDecisionCount"),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "sourceAnalysisId",
            "targetFormat",
            "sourceLimitations",
            "allowedEvidenceKeys",
            "parentDocumentIds",
            "artifactId",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendCreativeBriefBatch": ToolDataContract(
        model_owned_paths=("items",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "targetFormat",
            "sourceLimitations",
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeCreativeBriefProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "sourceAnalysisId",
            "creativeBriefArtifactId",
            "artifactRef",
        ),
        payload_mode="resource_reference",
    ),
    "beginScreenplayStructureArtifact": ToolDataContract(
        model_owned_paths=("title", "expectedUnitCount"),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "creativeBriefId",
            "structureKind",
            "expectedDecisionIds",
            "artifactId",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendScreenplayStructureBatch": ToolDataContract(
        model_owned_paths=("items",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeScreenplayStructureProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "creativeBriefId",
            "structureArtifactId",
            "artifactRef",
        ),
        payload_mode="resource_reference",
    ),
    "beginSceneListArtifact": ToolDataContract(
        model_owned_paths=("title", "expectedSceneCount"),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "structureId",
            "artifactId",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendSceneListBatch": ToolDataContract(
        model_owned_paths=("scenes",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeSceneListProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "structureId",
            "artifactRef",
        ),
        payload_mode="resource_reference",
    ),
    "proposeSceneDraft": ToolDataContract(
        model_owned_paths=(
            "title",
            "execution",
            "sceneText",
            "additionalScenes",
            "notes",
        ),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "sceneListId",
            "sceneId",
            "sceneHeading",
            "newSceneIds",
            "newSceneHeadings",
            "completedSceneIds",
            "sceneExecutions",
            "isComplete",
        ),
        payload_mode="delta",
    ),
    "beginScreenplayReviewArtifact": ToolDataContract(
        model_owned_paths=("title", "verdict", "expectedIssueCount"),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "reviewedDraftId",
            "verificationOfReviewId",
            "verificationIssueIds",
            "allowedSceneIds",
            "artifactId",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendScreenplayReviewBatch": ToolDataContract(
        model_owned_paths=("items",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "verificationIssueIds",
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeScreenplayReviewProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "reviewedDraftId",
            "verificationOfReviewId",
            "verifiedIssueIds",
            "failedVerificationIssueIds",
            "reviewArtifactId",
            "artifactRef",
        ),
        payload_mode="resource_reference",
    ),
    "beginScreenplayRevisionArtifact": ToolDataContract(
        model_owned_paths=(
            "title",
            "additionalSceneIds",
            "revisionSummary",
        ),
        host_bound_paths=_HOST_BOUND_SCOPE_PATHS,
        host_derived_paths=(
            "artifactId",
            "baseDraftId",
            "reviewId",
            "sceneListId",
            "expectedSceneIds",
            "reviewIssueIds",
            "revision",
            "nextSequence",
        ),
        payload_mode="delta",
    ),
    "appendScreenplayRevisionBatch": ToolDataContract(
        model_owned_paths=("revisedScenes",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "appendScreenplayRevisionResolutionBatch": ToolDataContract(
        model_owned_paths=("issueResolutions",),
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            "revision",
            "sequence",
            "committedItemCount",
            "remainingItemCount",
        ),
        payload_mode="batch",
    ),
    "finalizeScreenplayRevisionProposal": ToolDataContract(
        host_bound_paths=(*_HOST_BOUND_SCOPE_PATHS, "artifactId"),
        host_derived_paths=(
            *_HOST_DERIVED_PROPOSAL_PATHS,
            "revisionOf",
            "reviewId",
            "sceneListId",
            "artifactRef",
            "revisionArtifactId",
            "completedSceneIds",
            "sceneExecutions",
            "reassessedSceneIds",
            "isComplete",
        ),
        payload_mode="resource_reference",
    ),
})
