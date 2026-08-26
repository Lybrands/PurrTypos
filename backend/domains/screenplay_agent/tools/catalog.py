"""Build the instance-scoped screenplay Tool Catalog."""

from __future__ import annotations

from collections.abc import Mapping
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
from purra.tools import InMemoryToolCatalog

from domains.screenplay.source_scope import is_restricted_source_scope
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
    "inspectSourceStructure",
    "readSourceChapters",
    "searchSourceText",
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
_HOST_PREPARED_TOOL_WRITE_PARTS = frozenset({"episode_metadata"})
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
    "draft_scene": _DRAFT_SOURCE_TOOLS | _DEPENDENCY_READ_TOOL | {
        "getScreenplayEpisodeContext",
    },
    "episode_metadata": _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL,
    "review_dimension": frozenset({
        "getScreenplayEpisodeContext",
        "writeScreenplayCandidatePart",
    }),
    "source_chapter_digest": frozenset({
        "readSourceChapters",
        "writeScreenplayCandidatePart",
    }),
    "source_digest_reduction": (
        _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "source_analysis_section": (
        _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "creative_brief_section": frozenset({
        "readScreenplayDeliverable",
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
        {"readSourceChapters"} | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "character_arcs_index": (
        _PROJECT_TOOLS | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "character_arc_fragment": (
        _PROJECT_TOOLS | _DEPENDENCY_READ_TOOL | _CANDIDATE_WRITE_TOOL
    ),
    "scene_list_episode": frozenset({
        "readScreenplayDeliverable",
        "writeScreenplayCandidatePart",
    }),
    "final_response": frozenset(),
}
_DISPLAY_NAMES = {
    "readScreenplayTaskDependencies": "读取任务依赖",
    "inspectScreenplayProject": "查看剧本项目",
    "readScreenplayDeliverable": "读取剧本交付物",
    "searchScreenplayDeliverables": "检索剧本交付物",
    "getScreenplayEpisodeContext": "读取分集上下文",
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
) -> dict[str, str]:
    normalized_name = str(tool_name or "")
    label = _DISPLAY_NAMES.get(normalized_name)
    if episode_number is not None and episode_number > 0:
        template = _EPISODE_DISPLAY_NAMES.get(normalized_name)
        if template is not None:
            label = template.format(episode=episode_number)
    return {"zh-CN": label} if label else {}


def _operation_display_params(state, arguments, tool_call) -> dict[str, int]:
    del tool_call
    value = arguments.get("episodeNumber")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        value = state.domain.get("boundEpisodeNumber")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return {}
    return {"episodeNumber": value}


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
    if context.expected_part_type in _HOST_PREPARED_TOOL_WRITE_PARTS:
        # The application has already assembled the exact metadata context.
        # This phase is a bounded commit, not another research loop.
        return frozenset({"writeScreenplayCandidatePart"})
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
    "screenplay_tool_display_names",
]
