"""Composition-facing builder for the concrete writing tool catalog."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domains.writing.tools import build_writing_tool_catalog as build_domain_catalog
from domains.writing.tools.cache import WRITING_CACHE_PROBES
from domains.writing.policies import WRITING_TOOL_POLICIES
from infrastructure.writing.tools.handlers import WRITING_TOOL_OPERATIONS
from infrastructure.writing.tools.runtime import (
    WritingToolDependencies,
    bind_writing_tool_handlers,
)


def build_writing_tool_catalog(
    *,
    dependencies: WritingToolDependencies,
    skill_items: tuple[Mapping[str, Any], ...],
    handler_overrides: Mapping[str, Any] | None = None,
    cache_probe_overrides: Mapping[str, Any] | None = None,
):
    """Create an immutable Core catalog from explicit capability maps.

    Overrides exist for focused adapter tests. Production callers receive a
    fresh snapshot and cannot observe later mutation of the source maps.
    """

    atomic_operation_names = frozenset(
        name
        for name, policy in WRITING_TOOL_POLICIES.items()
        if policy.requires_user_approval
    )
    bound_handlers = dict(
        bind_writing_tool_handlers(
            WRITING_TOOL_OPERATIONS,
            dependencies,
            atomic_operation_names=atomic_operation_names,
        )
    )
    resolved_handler_overrides = dict(handler_overrides or {})
    bound_handlers.update(resolved_handler_overrides)
    probes = dict(WRITING_CACHE_PROBES)
    probes.update(cache_probe_overrides or {})
    return build_domain_catalog(
        skill_items=skill_items,
        handlers=bound_handlers,
        cache_predictors=probes,
        cancellation_linearizable_handlers=(
            atomic_operation_names - resolved_handler_overrides.keys()
        ),
    )


__all__ = ["WritingToolDependencies", "build_writing_tool_catalog"]
