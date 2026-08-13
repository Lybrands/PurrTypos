"""Build an instance-scoped Core catalog from injected writing capabilities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from purra.contracts import (
    AgentRunRequest,
    DomainEffect,
    ExecutionState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolSchema,
)
from purra.ports import CancellationSignal, ToolRegistration
from purra.tools import InMemoryToolCatalog
from domains.writing.contracts import WRITING_DOMAIN_NAMESPACE, WritingDomainContext
from domains.writing.policies import WRITING_TOOL_POLICIES, policy_coverage
from domains.writing.planning import WRITING_TOOL_PLANNING_DEPENDENCIES
from domains.writing.tools.host_arguments import (
    bind_host_writing_arguments,
    model_visible_writing_parameters,
)
from domains.writing.tools.context_contracts import (
    WRITING_TOOL_CONTEXT_CONTRACTS,
)
from domains.writing.tools.display_names import WRITING_TOOL_DISPLAY_NAMES


WritingToolHandler = Callable[[dict, dict, Callable[[dict], None] | None], Any]
WritingCachePredictor = Callable[[dict, dict], bool]


WRITING_REPLANNING_EVIDENCE_TOOLS = frozenset({
    "batchGetChapterContents",
    "getBookCharacters",
    "getBookStyle",
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
    predictor: WritingCachePredictor

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
    handlers: Mapping[str, WritingToolHandler],
    cache_predictors: Mapping[str, WritingCachePredictor],
    cancellation_linearizable_handlers: frozenset[str] = frozenset(),
) -> InMemoryToolCatalog:
    """Validate and snapshot injected schemas, handlers and cache probes."""

    schemas = {
        str(item.get("name") or "").strip(): _schema_from_skill(item)
        for item in skill_items
        if str(item.get("name") or "").strip()
    }
    handler_names = set(handlers)
    unclassified, orphaned = policy_coverage(handler_names)
    schema_names = set(schemas)
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

    ordered_names = tuple(
        str(item.get("name") or "").strip()
        for item in skill_items
        if str(item.get("name") or "").strip()
    )
    registrations = tuple(
        ToolRegistration(
            schema=schemas[name],
            handler=_adapt_handler(name, handlers[name]),
            policy=WRITING_TOOL_POLICIES[name],
            scope_validator=_writing_scope_validator(name),
            cache_probe=(
                _WritingCacheProbe(name, cache_predictors[name])
                if name in cache_predictors
                else None
            ),
            cancellation_linearizable=(
                name in cancellation_linearizable_handlers
            ),
            context_contract=WRITING_TOOL_CONTEXT_CONTRACTS[name],
        )
        for name in ordered_names
    )
    return InMemoryToolCatalog(registrations, _enabled_writing_tools)


def _schema_from_skill(item: Mapping[str, Any]) -> ToolSchema:
    name = str(item.get("name") or "").strip()
    return ToolSchema(
        name=name,
        description=str(item.get("description") or ""),
        parameters=model_visible_writing_parameters(item.get("parameters")),
        display_names=WRITING_TOOL_DISPLAY_NAMES.get(name, {}),
    )


def _adapt_handler(tool_name: str, handler: WritingToolHandler):
    async def _run(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        del signal
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
        content = str(getattr(result, "content", "") or "")
        error_code = _tool_error_code(content)
        return ToolHandlerResult(
            content=content,
            from_cache=bool(getattr(result, "from_cache", False)),
            effects=tuple(effects),
            error_code=error_code,
            planning_disposition=(
                ToolPlanningDisposition.REPLAN
                if (
                    error_code is None
                    and tool_name in WRITING_REPLANNING_EVIDENCE_TOOLS
                )
                else ToolPlanningDisposition.KEEP_PLAN
            ),
        )

    return _run


def _writing_scope_validator(tool_name: str):
    async def _validate(
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

    return _validate


def _enabled_writing_tools(request: AgentRunRequest) -> frozenset[str]:
    if request.domain_context.namespace != WRITING_DOMAIN_NAMESPACE:
        return frozenset()
    context = WritingDomainContext.from_core_context(request.domain_context)
    if not context.book_id:
        return frozenset()
    return frozenset(WRITING_TOOL_POLICIES)


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
