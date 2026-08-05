"""Typed failures for the Work Item lifecycle boundary."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from agent_core.errors import AgentCoreError


class WorkItemError(AgentCoreError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "work_item_error")
        self.details = MappingProxyType(dict(details or {}))


class WorkItemNotFoundError(WorkItemError):
    pass


class WorkItemConflictError(WorkItemError):
    pass


class WorkItemStateError(WorkItemError):
    pass


__all__ = [
    "WorkItemConflictError",
    "WorkItemError",
    "WorkItemNotFoundError",
    "WorkItemStateError",
]
