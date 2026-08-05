"""Dependency-inversion ports for Work Item persistence."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from agent_core.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemRecord,
    WorkItemRunLink,
    WorkItemRunLinkCommand,
    WorkItemTransitionCommand,
)


@runtime_checkable
class WorkItemRepository(Protocol):
    """Atomic CAS persistence for durable task identity and Run lineage.

    When ``created_by_run_id`` is present, ``create`` also persists the
    immutable ``CREATED`` Run link in the same transaction. Later Run links
    are append-only audit records and do not advance the Work Item's content
    revision.
    """

    async def create(
        self,
        work_item_id: str,
        command: WorkItemCreateCommand,
    ) -> WorkItemRecord: ...

    async def load(self, work_item_id: str) -> WorkItemRecord | None: ...

    async def link_run(
        self,
        command: WorkItemRunLinkCommand,
    ) -> WorkItemRunLink: ...

    async def list_run_links(
        self,
        work_item_id: str,
    ) -> Sequence[WorkItemRunLink]: ...

    async def complete(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord: ...

    async def cancel(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord: ...


__all__ = ["WorkItemRepository"]
