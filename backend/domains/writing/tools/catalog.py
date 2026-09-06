"""Build an instance-scoped Core catalog from injected writing capabilities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from purra.contracts import (
    AgentRunRequest,
    DomainEffect,
    ExecutionState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolSchema,
)
from purra.ports import (
    CancellationSignal,
    ToolHandler as CoreToolHandler,
    ToolRegistration,
)
from purra.tools import InMemoryToolCatalog
from domains.writing.contracts import WRITING_DOMAIN_NAMESPACE, WritingDomainContext
from domains.writing.policies import WRITING_TOOL_POLICIES, policy_coverage
from domains.writing.tools.contracts import CachePredictor, ToolHandler
from domains.writing.tools.host_arguments import (
    bind_host_writing_arguments,
    model_visible_writing_parameters,
)
from domains.writing.tools.context_contracts import (
    WRITING_TOOL_CONTEXT_CONTRACTS,
)
from domains.writing.tools.display_names import (
    WRITING_TOOL_DISPLAY_NAMES,
    writing_tool_display_names,
)


WRITING_REPLANNING_EVIDENCE_TOOLS = frozenset({
    "batchGetChapterContents",
    "getBookCharacters",
    "getChapterContent",
    "getGlobalOutline",
    "getSettingEntities",
    "getStoryBackground",
    "getStoryHealthDashboard",
    "getWritingStatsDashboard",
    "queryOutline",
    "searchMemories",
    "searchSparkIdeas",
})


@dataclass(frozen=True, slots=True)
class _WritingCacheProbe:
    tool_name: str
    predictor: CachePredictor

    def will_hit(
        self,
        state: ExecutionState,
        arguments: Mapping[str, Any],
    ) -> bool:
        return bool(self.predictor(
            state.domain,
            bind_host_writing_arguments(
                state.domain,
                self.tool_name,
                arguments,
            ),
        ))


def build_writing_tool_catalog(
    *,
    skill_items: tuple[Mapping[str, Any], ...],
    handlers: Mapping[str, ToolHandler],
    cache_predictors: Mapping[str, CachePredictor],
    native_registrations: Mapping[str, ToolRegistration],
    cancellation_linearizable_handlers: frozenset[str] = frozenset(),
) -> InMemoryToolCatalog:
    """Validate and snapshot injected schemas, handlers and cache probes."""

    skills = {
        str(item.get("name") or "").strip(): item
        for item in skill_items
        if str(item.get("name") or "").strip()
    }
    registrations = dict(native_registrations)
    handler_names = set(handlers) | set(registrations)
    if set(handlers) & set(registrations):
        raise RuntimeError("Writing tool handlers must have a single owner")
    unclassified, orphaned = policy_coverage(handler_names)
    schema_names = set(skills)
    missing_handlers = schema_names - handler_names
    hidden_handlers = handler_names - schema_names
    if unclassified or orphaned or missing_handlers or hidden_handlers:
        problems = []
        if unclassified:
            problems.append("unclassified=" + ",".join(sorted(unclassified)))
        if orphaned:
            problems.append("orphanedPolicies=" + ",".join(sorted(orphaned)))
        if missing_handlers:
            problems.append("missingHandlers=" + ",".join(sorted(missing_handlers)))
        if hidden_handlers:
            problems.append("hiddenHandlers=" + ",".join(sorted(hidden_handlers)))
        raise RuntimeError(
            "Writing tool catalog contract is incomplete: " + "; ".join(problems)
        )

    registrations.update({
        name: ToolRegistration(
            schema=_schema_from_skill(skills[name]),
            handler=_adapt_handler(name, handlers[name]),
            call_handler=_adapt_call_handler(name, handlers[name]),
            policy=WRITING_TOOL_POLICIES[name],
            scope_validator=_validate_writing_scope,
            cache_probe=(
                _WritingCacheProbe(name, cache_predictors[name])
                if name in cache_predictors
                else None
            ),
            cancellation_linearizable=(
                name in cancellation_linearizable_handlers
            ),
        )
        for name in handlers
    })
    return InMemoryToolCatalog(tuple(
        _decorate_registration(name, registrations[name])
        for name in skills
    ), enabled_writing_tools)


def _decorate_registration(
    tool_name: str,
    registration: ToolRegistration,
) -> ToolRegistration:
    return replace(
        registration,
        handler=_with_replanning(tool_name, registration.handler),
        call_handler=(
            _with_call_replanning(tool_name, registration.call_handler)
            if registration.call_handler is not None
            else None
        ),
        context_contract=WRITING_TOOL_CONTEXT_CONTRACTS[tool_name],
        operation_display_params=_writing_operation_display_params,
    )


def _writing_operation_display_params(state, arguments, tool_call):
    return {
        "displayNames": writing_tool_display_names(
            tool_call.name,
            state.domain,
            arguments,
        )
    }


def _with_replanning(tool_name: str, handler: CoreToolHandler) -> CoreToolHandler:
    async def _run(state, arguments, signal=None):
        result = await handler(state, arguments, signal)
        return replace(result, planning_disposition=(
            ToolPlanningDisposition.REPLAN
            if result.error_code is None and tool_name in WRITING_REPLANNING_EVIDENCE_TOOLS
            else ToolPlanningDisposition.KEEP_PLAN
        ))

    return _run


def _with_call_replanning(tool_name: str, handler):
    async def _run(state, arguments, tool_call, signal=None):
        result = await handler(state, arguments, tool_call, signal)
        return replace(result, planning_disposition=(
            ToolPlanningDisposition.REPLAN
            if result.error_code is None and tool_name in WRITING_REPLANNING_EVIDENCE_TOOLS
            else ToolPlanningDisposition.KEEP_PLAN
        ))

    return _run


def _schema_from_skill(item: Mapping[str, Any]) -> ToolSchema:
    name = str(item.get("name") or "").strip()
    return ToolSchema(
        name=name,
        description=str(item.get("description") or ""),
        parameters=model_visible_writing_parameters(item.get("parameters")),
        display_names=WRITING_TOOL_DISPLAY_NAMES.get(name, {}),
    )


def _adapt_handler(tool_name: str, handler: ToolHandler) -> CoreToolHandler:
    async def _run(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        del signal
        return await _invoke_handler(
            tool_name,
            handler,
            state,
            arguments,
        )

    return _run


def _adapt_call_handler(tool_name: str, handler: ToolHandler):
    async def _run(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        tool_call,
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        del signal
        trusted_arguments = dict(arguments)
        trusted_arguments["__toolCallId"] = tool_call.id
        return await _invoke_handler(
            tool_name,
            handler,
            state,
            trusted_arguments,
        )

    return _run


async def _invoke_handler(
    tool_name: str,
    handler: ToolHandler,
    state: ExecutionState,
    arguments: Mapping[str, Any],
) -> ToolHandlerResult:
    effects: list[DomainEffect] = []

    def _capture(raw: dict) -> None:
        effects.extend(_domain_effects(raw))

    result = await handler(
        state.domain,
        bind_host_writing_arguments(
            state.domain,
            tool_name,
            arguments,
        ),
        _capture,
    )
    return ToolHandlerResult(
        content=result.content,
        from_cache=result.from_cache,
        effects=tuple(effects),
        error_code=_tool_error_code(result.content),
    )


async def _validate_writing_scope(
    state: ExecutionState,
    arguments: Mapping[str, Any],
    signal: CancellationSignal | None = None,
) -> str | None:
    del signal
    host_book = str(state.domain.get("bookId") or "").strip()
    supplied_book = str(arguments.get("bookId") or "").strip()
    if host_book and supplied_book and host_book != supplied_book:
        return "The requested bookId is outside the current Agent Run scope."
    return None


def enabled_writing_tools(request: AgentRunRequest) -> frozenset[str]:
    if request.domain_context.namespace != WRITING_DOMAIN_NAMESPACE:
        return frozenset()
    context = WritingDomainContext.from_core_context(request.domain_context)
    if not context.book_id:
        return frozenset()
    names = set(WRITING_TOOL_POLICIES)
    if not context.knowledge_scope:
        names.difference_update({"searchNovelKnowledge", "readNovelKnowledge"})
    elif context.knowledge_scope.get("purpose") != "discussion":
        # These current-state sources do not have a chapter/POV projection.
        names.difference_update({
            "getBookCharacters", "listBookCharacters", "getSettingEntities", "listSettingEntities",
            "getStoryBackground", "searchMemories", "searchSparkIdeas", "getGlobalOutline",
            "queryOutline", "listOutlines", "getStoryHealthDashboard", "updateCharacter",
            "updateSettingEntity", "editStoryBackground", "updateOutline", "editGlobalOutline",
        })
    if not context.writing_method_recommendation_requested:
        names.discard("searchWritingMethods")
    if context.creation_mode != "continuation" or not context.continuation_binding:
        names.discard("readContinuationSourceSection")
    return frozenset(names)


def _domain_effects(raw: Mapping[str, Any]) -> tuple[DomainEffect, ...]:
    mapping = {
        "proposedChapterDiff": "writing.proposed_chapter_diff",
        "proposedSettingDiff": "writing.proposed_setting_diff",
        "settingUpdated": "writing.setting_updated",
        "chapterCreated": "writing.chapter_created",
    }
    effects: list[DomainEffect] = []
    remaining: dict[str, Any] = {}
    for key, value in raw.items():
        effect_type = mapping.get(str(key))
        if effect_type is None:
            remaining[str(key)] = value
            continue
        effects.append(DomainEffect(
            type=effect_type,
            payload=dict(value) if isinstance(value, Mapping) else {"value": value},
        ))
    if remaining:
        effects.append(DomainEffect(type="writing.progress", payload=remaining))
    return tuple(effects)


def _tool_error_code(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return "tool_execution_failed" if str(payload.get("error") or "").strip() else None
