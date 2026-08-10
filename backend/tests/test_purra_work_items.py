from __future__ import annotations

from dataclasses import replace

import pytest

from purra.work_items import (
    WorkItemCreateCommand,
    WorkItemLifecycle,
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


class _MemoryWorkItemRepository:
    def __init__(self) -> None:
        self.items: dict[str, WorkItemRecord] = {}
        self.links: list[WorkItemRunLink] = []

    async def create(
        self,
        work_item_id: str,
        command: WorkItemCreateCommand,
    ) -> WorkItemRecord:
        item = WorkItemRecord(
            id=work_item_id,
            namespace=command.namespace,
            kind=command.kind,
            owner_id=command.owner_id,
            created_by_run_id=command.created_by_run_id,
            metadata=command.metadata,
        )
        self.items[item.id] = item
        if command.created_by_run_id is not None:
            self.links.append(WorkItemRunLink(
                work_item_id=item.id,
                run_id=command.created_by_run_id,
                relation=WorkItemRunRelation.CREATED,
                work_item_revision=item.revision,
            ))
        return item

    async def load(self, work_item_id: str) -> WorkItemRecord | None:
        return self.items.get(work_item_id)

    async def link_run(
        self,
        command: WorkItemRunLinkCommand,
    ) -> WorkItemRunLink:
        item = self.items[command.work_item_id]
        link = WorkItemRunLink(
            work_item_id=item.id,
            run_id=command.run_id,
            relation=command.relation,
            work_item_revision=item.revision,
        )
        self.links.append(link)
        return link

    async def list_run_links(
        self,
        work_item_id: str,
    ) -> tuple[WorkItemRunLink, ...]:
        return tuple(
            link for link in self.links if link.work_item_id == work_item_id
        )

    async def complete(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        return self._transition(command, WorkItemStatus.COMPLETED)

    async def cancel(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        return self._transition(command, WorkItemStatus.CANCELED)

    def _transition(
        self,
        command: WorkItemTransitionCommand,
        status: WorkItemStatus,
    ) -> WorkItemRecord:
        item = self.items[command.work_item_id]
        updated = replace(item, status=status, revision=item.revision + 1)
        self.items[item.id] = updated
        return updated


@pytest.mark.asyncio
async def test_work_item_spans_creator_and_continuation_runs() -> None:
    repository = _MemoryWorkItemRepository()
    lifecycle = WorkItemLifecycle(
        repository,
        id_factory=lambda: "work-item-1",
    )

    work_item = await lifecycle.begin(WorkItemCreateCommand(
        namespace="screenplay",
        kind="source_analysis",
        owner_id="project-1",
        created_by_run_id="run-a",
        metadata={"range": "chapters-1-100"},
    ))

    assert isinstance(repository, WorkItemRepository)
    assert work_item.status is WorkItemStatus.OPEN
    assert work_item.revision == 1
    assert work_item.created_by_run_id == "run-a"

    continuation = await lifecycle.link_run(WorkItemRunLinkCommand(
        work_item_id=work_item.id,
        run_id="run-b",
        relation=WorkItemRunRelation.CONTINUATION,
        expected_revision=1,
    ))

    assert continuation.work_item_revision == 1
    assert continuation.relation.writes_work_item is True
    links = await repository.list_run_links(work_item.id)
    assert [link.relation for link in links] == [
        WorkItemRunRelation.CREATED,
        WorkItemRunRelation.CONTINUATION,
    ]

    completed = await lifecycle.complete(WorkItemTransitionCommand(
        work_item_id=work_item.id,
        expected_revision=1,
    ))
    assert completed.status is WorkItemStatus.COMPLETED
    assert completed.revision == 2

    with pytest.raises(WorkItemStateError) as error:
        await lifecycle.link_run(WorkItemRunLinkCommand(
            work_item_id=work_item.id,
            run_id="run-c",
            relation=WorkItemRunRelation.CONTINUATION,
            expected_revision=2,
        ))
    assert error.value.code == "work_item_not_open"

    reference = await lifecycle.link_run(WorkItemRunLinkCommand(
        work_item_id=work_item.id,
        run_id="run-c",
        relation=WorkItemRunRelation.REFERENCE,
        expected_revision=2,
    ))
    assert reference.relation.writes_work_item is False


@pytest.mark.asyncio
async def test_work_item_lifecycle_rejects_stale_revision_and_missing_item() -> None:
    repository = _MemoryWorkItemRepository()
    lifecycle = WorkItemLifecycle(
        repository,
        id_factory=lambda: "work-item-stale",
    )
    item = await lifecycle.begin(WorkItemCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        created_by_run_id="run-a",
    ))
    await lifecycle.complete(WorkItemTransitionCommand(
        work_item_id=item.id,
        expected_revision=1,
    ))

    with pytest.raises(WorkItemConflictError) as conflict:
        await lifecycle.cancel(WorkItemTransitionCommand(
            work_item_id=item.id,
            expected_revision=1,
        ))
    assert conflict.value.code == "work_item_revision_conflict"
    assert conflict.value.details["actualRevision"] == 2

    with pytest.raises(WorkItemStateError) as creator:
        await lifecycle.link_run(WorkItemRunLinkCommand(
            work_item_id=item.id,
            run_id="run-b",
            relation=WorkItemRunRelation.CREATED,
            expected_revision=2,
        ))
    assert creator.value.code == "work_item_creator_link_immutable"

    with pytest.raises(WorkItemNotFoundError) as missing:
        await lifecycle.get("missing")
    assert missing.value.code == "work_item_not_found"


def test_work_item_run_relations_encode_write_authority() -> None:
    assert WorkItemRunRelation.CREATED.writes_work_item is True
    assert WorkItemRunRelation.CONTINUATION.writes_work_item is True
    assert WorkItemRunRelation.REFERENCE.writes_work_item is False
