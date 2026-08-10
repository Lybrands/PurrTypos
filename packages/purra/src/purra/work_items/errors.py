"""Typed failures for the Work Item lifecycle boundary."""

from __future__ import annotations

from purra.errors import CodedAgentCoreError


class WorkItemError(CodedAgentCoreError):
    default_code = "work_item_error"


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
