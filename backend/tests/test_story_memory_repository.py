from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from domains.writing.story_memory import (
    SourceReference,
    StoryMemoryChange,
    StoryMemoryConflictError,
    StoryMemoryDeltaDraft,
    StoryMemoryDeltaStatus,
    StoryMemoryKind,
    StoryMemoryLifecycle,
    StoryMemoryOperation,
    StoryMemoryProvenanceStatus,
    StoryMemorySourceStatus,
    StoryMemoryStatus,
    validate_delta_draft,
)
from domains.writing.story_memory_ledger import StoryMemoryLedger
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


def _change(
    *,
    chapter_id: str = "chapter-1",
    revision: str = "rev-1",
    key: str = "character:linmo:location",
    value: str = "旧城区",
    operation: StoryMemoryOperation = StoryMemoryOperation.UPSERT,
) -> StoryMemoryChange:
    return StoryMemoryChange(
        target_key=key,
        kind=StoryMemoryKind.CHARACTER_STATE,
        operation=operation,
        subject_id="character:linmo",
        payload={"attribute": "location", "value": value},
        status=StoryMemoryStatus.CONFIRMED,
        confidence=0.98,
        source=SourceReference(
            chapter_id=chapter_id,
            source_revision=revision,
            excerpt=f"林墨抵达{value}",
            locator={"paragraph": 4},
            narrative_order=1,
            story_time="2041-03-08",
            story_time_precision="day",
        ),
    )


def _draft(
    *changes: StoryMemoryChange,
    chapter_id: str = "chapter-1",
    revision: str = "rev-1",
) -> StoryMemoryDeltaDraft:
    return StoryMemoryDeltaDraft(
        book_id="book-1",
        chapter_id=chapter_id,
        source_revision=revision,
        source_type="manual",
        note="chapter state update",
        changes=tuple(changes or (_change(chapter_id=chapter_id, revision=revision),)),
    )


@pytest.mark.asyncio
async def test_pending_delta_preserves_chapter_evidence_without_changing_state(db):
    repository = SqliteStoryMemoryRepository(db)

    delta = await repository.create_delta(_draft())

    assert delta.status is StoryMemoryDeltaStatus.PENDING
    assert delta.chapter_id == "chapter-1"
    assert delta.source_revision == "rev-1"
    assert delta.changes[0].source.excerpt == "林墨抵达旧城区"
    assert delta.changes[0].source.locator == {"paragraph": 4}
    assert await repository.list_records("book-1") == ()


@pytest.mark.asyncio
async def test_application_ledger_exposes_stage_approve_and_current_state(db):
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))

    delta = await ledger.stage_delta(_draft())
    await ledger.approve_delta(delta.id)
    state = await ledger.current_state(
        "book-1",
        kinds=(StoryMemoryKind.CHARACTER_STATE.value,),
    )

    assert len(state) == 1
    assert state[0].memory_key == "character:linmo:location"


@pytest.mark.asyncio
async def test_apply_delta_projects_current_story_state_and_version(db):
    repository = SqliteStoryMemoryRepository(db)
    delta = await repository.create_delta(_draft())

    receipt = await repository.apply_delta(delta.id)
    record = await repository.get_record("book-1", "character:linmo:location")
    versions = await repository.list_versions(
        "book-1",
        "character:linmo:location",
    )

    assert receipt.status is StoryMemoryDeltaStatus.APPLIED
    assert receipt.changed_keys == ("character:linmo:location",)
    assert record is not None
    assert record.payload == {"attribute": "location", "value": "旧城区"}
    assert record.version == 1
    assert record.last_delta_id == delta.id
    assert record.provenance_status is StoryMemoryProvenanceStatus.VALID
    assert [(item.version, item.action) for item in versions] == [(1, "apply")]


@pytest.mark.asyncio
async def test_rollback_restores_previous_value_without_rewinding_version(db):
    repository = SqliteStoryMemoryRepository(db)
    first = await repository.create_delta(_draft())
    await repository.apply_delta(first.id)
    second = await repository.create_delta(
        _draft(
            _change(value="北城区医院", revision="rev-2"),
            revision="rev-2",
        )
    )
    await repository.apply_delta(second.id)

    receipt = await repository.rollback_delta(second.id)
    record = await repository.get_record("book-1", "character:linmo:location")
    versions = await repository.list_versions(
        "book-1",
        "character:linmo:location",
    )

    assert receipt.status is StoryMemoryDeltaStatus.REVERTED
    assert record is not None
    assert record.payload["value"] == "旧城区"
    assert record.version == 3
    assert record.last_delta_id == first.id
    assert [(item.version, item.action) for item in versions] == [
        (1, "apply"),
        (2, "apply"),
        (3, "rollback"),
    ]


@pytest.mark.asyncio
async def test_rollback_new_record_removes_it_from_current_state(db):
    repository = SqliteStoryMemoryRepository(db)
    delta = await repository.create_delta(_draft())
    await repository.apply_delta(delta.id)

    await repository.rollback_delta(delta.id)

    assert await repository.get_record(
        "book-1",
        "character:linmo:location",
    ) is None
    reverted = await repository.get_record(
        "book-1",
        "character:linmo:location",
        include_reverted=True,
    )
    assert reverted is not None
    assert reverted.lifecycle is StoryMemoryLifecycle.REVERTED
    assert reverted.version == 2


@pytest.mark.asyncio
async def test_rollback_rejects_a_delta_superseded_by_later_state(db):
    repository = SqliteStoryMemoryRepository(db)
    first = await repository.create_delta(_draft())
    await repository.apply_delta(first.id)
    second = await repository.create_delta(
        _draft(
            _change(value="北城区医院", revision="rev-2"),
            revision="rev-2",
        )
    )
    await repository.apply_delta(second.id)

    with pytest.raises(StoryMemoryConflictError):
        await repository.rollback_delta(first.id)

    record = await repository.get_record("book-1", "character:linmo:location")
    assert record is not None and record.payload["value"] == "北城区医院"


@pytest.mark.asyncio
async def test_chapter_revision_invalidates_candidates_and_marks_applied_state_stale(db):
    repository = SqliteStoryMemoryRepository(db)
    applied = await repository.create_delta(_draft())
    await repository.apply_delta(applied.id)
    pending = await repository.create_delta(
        _draft(
            _change(
                key="character:linmo:goal",
                value="寻找妹妹",
            )
        )
    )

    unchanged = await repository.invalidate_chapter(
        "book-1",
        "chapter-1",
        current_revision="rev-1",
    )
    receipt = await repository.invalidate_chapter(
        "book-1",
        "chapter-1",
        current_revision="rev-2",
    )

    assert unchanged.stale_sources == 0
    assert receipt.stale_sources == 2
    assert receipt.invalidated_pending_deltas == 1
    assert receipt.review_required_deltas == 1
    assert receipt.stale_records == 1
    applied_after = await repository.get_delta(applied.id)
    assert applied_after.status is StoryMemoryDeltaStatus.NEEDS_REVIEW
    assert applied_after.changes[0].source.status is StoryMemorySourceStatus.STALE
    assert (await repository.get_delta(pending.id)).status is (
        StoryMemoryDeltaStatus.INVALIDATED
    )
    record = await repository.get_record("book-1", "character:linmo:location")
    assert record is not None
    assert record.provenance_status is StoryMemoryProvenanceStatus.STALE


def test_delta_rejects_evidence_from_another_chapter():
    change = _change(chapter_id="chapter-2")

    with pytest.raises(ValueError, match="delta chapter"):
        validate_delta_draft(_draft(change, chapter_id="chapter-1"))
