"""Core-owned lifecycle for task identity that may span multiple Runs."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from purra.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemRecord,
    WorkItemRunLink,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemStatus,
    WorkItemTransitionCommand,
)
from purra.work_items.errors import (
    WorkItemConflictError,
    WorkItemNotFoundError,
    WorkItemStateError,
)
from purra.work_items.ports import WorkItemRepository


class WorkItemLifecycle:
    """Validate generic state transitions while storage owns atomic CAS."""

    def __init__(
        self,
        repository: WorkItemRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    async def begin(self, command: WorkItemCreateCommand) -> WorkItemRecord:
        work_item_id = str(self._id_factory() or "").strip()
        if not work_item_id:
            raise RuntimeError("work item id factory returned an empty id")
        return await self._repository.create(work_item_id, command)

    async def get(self, work_item_id: str) -> WorkItemRecord:
        return await self._require(work_item_id)

    async def link_run(
        self,
        command: WorkItemRunLinkCommand,
    ) -> WorkItemRunLink:
        work_item = await self._require(command.work_item_id)
        self._require_revision(work_item, command.expected_revision)
        if command.relation is WorkItemRunRelation.CREATED:
            raise WorkItemStateError(
                "creator Run link is immutable after Work Item creation",
                code="work_item_creator_link_immutable",
                details={"workItemId": work_item.id},
            )
        if command.relation.writes_work_item:
            self._require_open(work_item)
        return await self._repository.link_run(command)

    async def complete(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        work_item = await self._require(command.work_item_id)
        self._require_revision(work_item, command.expected_revision)
        self._require_open(work_item)
        return await self._repository.complete(command)

    async def cancel(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        work_item = await self._require(command.work_item_id)
        self._require_revision(work_item, command.expected_revision)
        self._require_open(work_item)
        return await self._repository.cancel(command)

    async def _require(self, work_item_id: str) -> WorkItemRecord:
        normalized = str(work_item_id or "").strip()
        work_item = await self._repository.load(normalized)
        if work_item is None:
            raise WorkItemNotFoundError(
                "work item does not exist",
                code="work_item_not_found",
                details={"workItemId": normalized},
            )
        return work_item

    @staticmethod
    def _require_revision(work_item: WorkItemRecord, expected: int) -> None:
        if work_item.revision != int(expected):
            raise WorkItemConflictError(
                "work item revision does not match",
                code="work_item_revision_conflict",
                details={
                    "expectedRevision": int(expected),
                    "actualRevision": work_item.revision,
                },
            )

    @staticmethod
    def _require_open(work_item: WorkItemRecord) -> None:
        if work_item.status is not WorkItemStatus.OPEN:
            raise WorkItemStateError(
                "work item is not open",
                code="work_item_not_open",
                details={"status": work_item.status.value},
            )


__all__ = ["WorkItemLifecycle"]
