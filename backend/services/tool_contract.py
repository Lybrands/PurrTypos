"""Startup-time contract checks for Agent tool declarations and handlers.

The model can only make reliable decisions when its advertised tools and the
backend's executable tools are exactly the same set.  This module deliberately
contains no application imports or side effects so the contract can be tested
independently from FastAPI startup.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class ToolContractReport:
    """Differences discovered between declared and executable capabilities."""

    declared_names: frozenset[str]
    handler_names: frozenset[str]
    missing_handlers: frozenset[str]
    undeclared_handlers: frozenset[str]
    invalid_handlers: tuple[str, ...]
    orphaned_cache_predictors: frozenset[str]
    orphaned_cache_keys: frozenset[str]
    duplicate_skill_names: frozenset[str]

    @property
    def is_valid(self) -> bool:
        return not any((
            self.missing_handlers,
            self.undeclared_handlers,
            self.invalid_handlers,
            self.orphaned_cache_predictors,
            self.orphaned_cache_keys,
            self.duplicate_skill_names,
        ))

    @property
    def tool_count(self) -> int:
        return len(self.declared_names)

    def describe_violations(self) -> str:
        """Return a stable, human-readable error suitable for startup logs."""

        rows: list[str] = []
        if self.missing_handlers:
            rows.append(f"声明了但没有 handler：{_format_names(self.missing_handlers)}")
        if self.undeclared_handlers:
            rows.append(f"注册了但没有 SKILL.md：{_format_names(self.undeclared_handlers)}")
        if self.invalid_handlers:
            rows.append(f"handler 签名无效：{', '.join(self.invalid_handlers)}")
        if self.orphaned_cache_predictors:
            rows.append(f"无 handler 的缓存预测器：{_format_names(self.orphaned_cache_predictors)}")
        if self.orphaned_cache_keys:
            rows.append(f"无 handler 的读缓存键：{_format_names(self.orphaned_cache_keys)}")
        if self.duplicate_skill_names:
            rows.append(f"重复的 skill 名称：{_format_names(self.duplicate_skill_names)}")
        return "; ".join(rows) or "工具契约有效"


def inspect_tool_contract(
    skill_items: Sequence[Mapping[str, Any]],
    handlers: Mapping[str, Callable[..., Any]],
    cache_predictors: Mapping[str, Callable[..., Any]],
    read_cache_keys: Mapping[str, Callable[..., Any]],
) -> ToolContractReport:
    """Compare model-visible skills with backend runtime registrations.

    Skill names are intentionally taken from loaded items rather than from a
    hand-maintained expected list.  That makes a new tool fail at startup if
    its implementation, schema, or cache registration drifts.
    """

    raw_names = [str(item.get("name") or "").strip() for item in skill_items]
    declared_names = frozenset(name for name in raw_names if name)
    duplicate_skill_names = frozenset({
        name for name in declared_names if raw_names.count(name) > 1
    })
    handler_names = frozenset(str(name).strip() for name in handlers if str(name).strip())

    invalid_handlers = tuple(sorted(
        name for name in handler_names
        if not _is_valid_handler(handlers[name])
    ))

    return ToolContractReport(
        declared_names=declared_names,
        handler_names=handler_names,
        missing_handlers=declared_names - handler_names,
        undeclared_handlers=handler_names - declared_names,
        invalid_handlers=invalid_handlers,
        orphaned_cache_predictors=(
            frozenset(str(name) for name in cache_predictors) - handler_names
        ),
        orphaned_cache_keys=(
            frozenset(str(name) for name in read_cache_keys) - handler_names
        ),
        duplicate_skill_names=duplicate_skill_names,
    )


def _is_valid_handler(handler: Callable[..., Any]) -> bool:
    """Handlers are async functions with the stable ``ctx,args,send_chunk`` API."""

    if not inspect.iscoroutinefunction(handler):
        return False
    try:
        return len(inspect.signature(handler).parameters) == 3
    except (TypeError, ValueError):
        return False


def _format_names(names: frozenset[str]) -> str:
    return ", ".join(sorted(names))
