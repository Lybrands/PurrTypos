from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from application.story_memory_evolution import (
    STORY_MEMORY_AUTO_APPLY_ENABLED_KEY,
    STORY_MEMORY_AUTO_APPLY_MIN_CONFIDENCE_KEY,
    StoryMemoryEvolutionService,
)
from database.connection import DatabaseConnection
from domains.writing.story_memory import (
    SourceReference,
    StoryMemoryConflictError,
    StoryMemoryDeltaStatus,
    StoryMemoryStatus,
)
from domains.writing.story_memory_evolution import (
    EvolutionClassification,
    EvolutionRecommendation,
    EvolutionResolution,
    EvolutionReviewStatus,
    EvolutionRisk,
)
from domains.writing.story_memory_ledger import StoryMemoryLedger
from domains.writing.story_settings import (
    CharacterState,
    PlotThread,
    StorySettingChange,
    WorldFact,
)
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
    setting,
    *,
    chapter_id: str,
    revision: str,
    narrative_order: int | None = None,
    status: StoryMemoryStatus = StoryMemoryStatus.CONFIRMED,
) -> StorySettingChange:
    return StorySettingChange(
        setting=setting,
        source=SourceReference(
            chapter_id=chapter_id,
            excerpt=f"evidence for {setting.target_key}",
            source_revision=revision,
            narrative_order=narrative_order,
        ),
        confidence=0.9,
        status=status,
    )


async def _stage(
    db: DatabaseConnection,
    *changes: StorySettingChange,
    chapter_id: str,
    revision: str,
):
    return await StoryMemoryLedger(
        SqliteStoryMemoryRepository(db)
    ).stage_settings(
        book_id="book-1",
        chapter_id=chapter_id,
        changes=changes,
        source_revision=revision,
        source_type="test",
    )


async def _apply_existing(
    db: DatabaseConnection,
    setting,
    *,
    narrative_order: int | None = None,
):
    delta = await _stage(
        db,
        _change(
            setting,
            chapter_id="chapter-old",
            revision="revision-old",
            narrative_order=narrative_order,
        ),
        chapter_id="chapter-old",
        revision="revision-old",
    )
    await SqliteStoryMemoryRepository(db).apply_delta(delta.id)
    return delta


@pytest.mark.asyncio
async def test_review_classifies_addition_and_safe_character_update(db):
    await _apply_existing(
        db,
        CharacterState("lin", "location", "旧城区"),
        narrative_order=3,
    )
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "北城"),
            chapter_id="chapter-new",
            revision="revision-new",
            narrative_order=4,
        ),
        _change(
            WorldFact("moon-law", "月光会令魔法失效"),
            chapter_id="chapter-new",
            revision="revision-new",
            narrative_order=4,
        ),
        chapter_id="chapter-new",
        revision="revision-new",
    )

    review = await StoryMemoryEvolutionService(db).review_delta(delta.id)

    update, addition = review.decisions
    assert update.classification is EvolutionClassification.UPDATE
    assert update.recommendation is EvolutionRecommendation.APPLY
    assert update.risk is EvolutionRisk.MEDIUM
    assert [(item.field, item.before, item.after) for item in update.field_changes] == [
        ("value", "旧城区", "北城"),
    ]
    assert addition.classification is EvolutionClassification.ADDITION
    assert addition.risk is EvolutionRisk.LOW
    assert review.summary == {
        "addition": 1,
        "update": 1,
        "duplicate": 0,
        "conflict": 0,
        "supersession": 0,
    }


@pytest.mark.asyncio
async def test_identical_candidate_is_rejected_as_duplicate(db):
    await _apply_existing(db, CharacterState("lin", "goal", "寻找妹妹"))
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "goal", "寻找妹妹"),
            chapter_id="chapter-new",
            revision="revision-new",
        ),
        chapter_id="chapter-new",
        revision="revision-new",
    )

    decision = (
        await StoryMemoryEvolutionService(db).review_delta(delta.id)
    ).decisions[0]

    assert decision.classification is EvolutionClassification.DUPLICATE
    assert decision.recommendation is EvolutionRecommendation.REJECT
    assert decision.related_memory_key == "character:lin:state:goal"


@pytest.mark.asyncio
async def test_world_fact_rewrite_and_narrative_regression_require_review(db):
    await _apply_existing(
        db,
        WorldFact("moon-law", "月光会令魔法失效"),
        narrative_order=8,
    )
    delta = await _stage(
        db,
        _change(
            WorldFact("moon-law", "月光只会削弱魔法"),
            chapter_id="chapter-earlier",
            revision="revision-earlier",
            narrative_order=2,
        ),
        chapter_id="chapter-earlier",
        revision="revision-earlier",
    )

    decision = (
        await StoryMemoryEvolutionService(db).review_delta(delta.id)
    ).decisions[0]

    assert decision.classification is EvolutionClassification.CONFLICT
    assert decision.recommendation is EvolutionRecommendation.REVIEW
    assert decision.risk is EvolutionRisk.HIGH
    assert "叙事顺序" in decision.rationale


@pytest.mark.asyncio
async def test_inferred_candidate_cannot_silently_downgrade_confirmed_state(db):
    await _apply_existing(db, CharacterState("lin", "location", "旧城区"))
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "北城"),
            chapter_id="chapter-new",
            revision="revision-new",
            status=StoryMemoryStatus.INFERRED,
        ),
        chapter_id="chapter-new",
        revision="revision-new",
    )

    decision = (
        await StoryMemoryEvolutionService(db).review_delta(delta.id)
    ).decisions[0]

    assert decision.classification is EvolutionClassification.UPDATE
    assert decision.recommendation is EvolutionRecommendation.REVIEW
    assert decision.risk is EvolutionRisk.HIGH
    assert any(item.field == "status" for item in decision.field_changes)


@pytest.mark.asyncio
async def test_cross_key_plot_thread_is_flagged_as_supersession(db):
    await _apply_existing(
        db,
        PlotThread("missing-sister", "失踪的妹妹", "线索中断"),
    )
    delta = await _stage(
        db,
        _change(
            PlotThread("sister-clue", "失踪的妹妹", "线索指向北城"),
            chapter_id="chapter-new",
            revision="revision-new",
        ),
        chapter_id="chapter-new",
        revision="revision-new",
    )

    decision = (
        await StoryMemoryEvolutionService(db).review_delta(delta.id)
    ).decisions[0]

    assert decision.classification is EvolutionClassification.SUPERSESSION
    assert decision.related_memory_key == "plot_thread:missing-sister"
    assert decision.recommendation is EvolutionRecommendation.REVIEW


@pytest.mark.asyncio
async def test_review_is_idempotent_and_tracks_apply_rollback_lifecycle(db):
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "injury", "left_arm"),
            chapter_id="chapter-new",
            revision="revision-new",
        ),
        chapter_id="chapter-new",
        revision="revision-new",
    )
    service = StoryMemoryEvolutionService(db)
    first = await service.review_delta(delta.id)
    second = await service.review_delta(delta.id)
    assert first == second
    assert len(await db.fetch_all("SELECT * FROM story_memory_evolution_reviews")) == 1

    repository = SqliteStoryMemoryRepository(db)
    await repository.apply_delta(delta.id)
    applied = await service.get_review(delta.id)
    assert applied is not None
    assert applied.status is EvolutionReviewStatus.RESOLVED
    assert applied.decisions[0].resolution is EvolutionResolution.ACCEPTED

    await repository.rollback_delta(delta.id)
    reverted = await service.get_review(delta.id)
    assert reverted is not None
    assert reverted.decisions[0].resolution is EvolutionResolution.REVERTED


@pytest.mark.asyncio
async def test_chapter_revision_marks_open_review_stale(db):
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    service = StoryMemoryEvolutionService(db)
    await service.review_delta(delta.id)

    await SqliteStoryMemoryRepository(db).invalidate_chapter(
        "book-1",
        "chapter-1",
        current_revision="revision-2",
    )
    stale = await service.get_review(delta.id)

    assert stale is not None
    assert stale.status is EvolutionReviewStatus.STALE
    assert stale.decisions[0].review_status is EvolutionReviewStatus.STALE


@pytest.mark.asyncio
async def test_book_review_listing_can_filter_open_reviews(db):
    open_delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    resolved_delta = await _stage(
        db,
        _change(
            CharacterState("su", "location", "北城"),
            chapter_id="chapter-2",
            revision="revision-2",
        ),
        chapter_id="chapter-2",
        revision="revision-2",
    )
    service = StoryMemoryEvolutionService(db)
    await service.review_delta(open_delta.id)
    await service.review_delta(resolved_delta.id)
    await SqliteStoryMemoryRepository(db).apply_delta(resolved_delta.id)

    open_reviews = await service.list_reviews("book-1", statuses=("open",))

    assert [item.delta_id for item in open_reviews] == [open_delta.id]


@pytest.mark.asyncio
async def test_partial_resolution_applies_confirmed_child_delta_and_keeps_audit(db):
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
            status=StoryMemoryStatus.INFERRED,
        ),
        _change(
            CharacterState("lin", "goal", "寻找妹妹"),
            chapter_id="chapter-1",
            revision="revision-1",
            status=StoryMemoryStatus.INFERRED,
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    service = StoryMemoryEvolutionService(db)
    await service.review_delta(delta.id)

    receipt = await service.resolve_review(
        delta.id,
        {
            "character:lin:state:location": "accepted",
            "character:lin:state:goal": "rejected",
        },
    )

    assert receipt.status == "applied"
    assert receipt.applied_delta_id
    repository = SqliteStoryMemoryRepository(db)
    original = await repository.get_delta(delta.id)
    applied = await repository.get_delta(str(receipt.applied_delta_id))
    assert original is not None
    assert original.status is StoryMemoryDeltaStatus.INVALIDATED
    assert applied is not None
    assert applied.status is StoryMemoryDeltaStatus.APPLIED
    assert applied.source_type == "evolution_resolution"
    assert applied.changes[0].status is StoryMemoryStatus.CONFIRMED
    records = await repository.list_records("book-1")
    assert [item.memory_key for item in records] == [
        "character:lin:state:location"
    ]

    resolved = await service.get_review(delta.id)
    assert resolved is not None
    assert resolved.status is EvolutionReviewStatus.RESOLVED
    assert [item.resolution for item in resolved.decisions] == [
        EvolutionResolution.ACCEPTED,
        EvolutionResolution.REJECTED,
    ]
    assert resolved.decisions[0].resolved_delta_id == receipt.applied_delta_id
    assert resolved.decisions[0].resolution_actor == "user"

    repeated = await service.resolve_review(
        delta.id,
        {
            "character:lin:state:location": "accepted",
            "character:lin:state:goal": "rejected",
        },
    )
    assert repeated == receipt


@pytest.mark.asyncio
async def test_concurrent_resolution_requests_share_one_child_delta(db):
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
            status=StoryMemoryStatus.INFERRED,
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    service = StoryMemoryEvolutionService(db)
    await service.review_delta(delta.id)

    first, second = await asyncio.gather(
        service.resolve_review(
            delta.id,
            {"character:lin:state:location": "accepted"},
        ),
        service.resolve_review(
            delta.id,
            {"character:lin:state:location": "accepted"},
        ),
    )

    assert first.applied_delta_id == second.applied_delta_id
    delta_rows = await db.fetch_all(
        "SELECT id, status FROM story_memory_deltas ORDER BY create_time ASC"
    )
    assert len(delta_rows) == 2
    assert {item["status"] for item in delta_rows} == {"applied", "invalidated"}
    versions = await SqliteStoryMemoryRepository(db).list_versions(
        "book-1",
        "character:lin:state:location",
    )
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_resolution_detects_state_changed_after_review(db):
    await _apply_existing(db, CharacterState("lin", "location", "旧城区"))
    candidate = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "北城"),
            chapter_id="chapter-candidate",
            revision="revision-candidate",
        ),
        chapter_id="chapter-candidate",
        revision="revision-candidate",
    )
    service = StoryMemoryEvolutionService(db)
    await service.review_delta(candidate.id)

    concurrent = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "南城"),
            chapter_id="chapter-concurrent",
            revision="revision-concurrent",
        ),
        chapter_id="chapter-concurrent",
        revision="revision-concurrent",
    )
    await SqliteStoryMemoryRepository(db).apply_delta(concurrent.id)

    with pytest.raises(StoryMemoryConflictError, match="changed after review"):
        await service.resolve_review(
            candidate.id,
            {"character:lin:state:location": "accepted"},
        )
    stale = await service.get_review(candidate.id)
    assert stale is not None and stale.status is EvolutionReviewStatus.STALE


@pytest.mark.asyncio
async def test_auto_policy_only_applies_whitelisted_low_risk_additions(db):
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        [STORY_MEMORY_AUTO_APPLY_ENABLED_KEY, json.dumps(True)],
    )
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        [STORY_MEMORY_AUTO_APPLY_MIN_CONFIDENCE_KEY, json.dumps(0.9)],
    )
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
            status=StoryMemoryStatus.INFERRED,
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    service = StoryMemoryEvolutionService(db)
    review = await service.review_delta(delta.id)

    receipt = await service.maybe_auto_resolve(review)

    assert receipt is not None
    assert receipt.actor == "auto_policy"
    record = await SqliteStoryMemoryRepository(db).get_record(
        "book-1",
        "character:lin:state:location",
    )
    assert record is not None
    assert record.status is StoryMemoryStatus.CONFIRMED

    world_delta = await _stage(
        db,
        _change(
            WorldFact("moon-law", "月光会令魔法失效"),
            chapter_id="chapter-2",
            revision="revision-2",
            status=StoryMemoryStatus.INFERRED,
        ),
        chapter_id="chapter-2",
        revision="revision-2",
    )
    world_review = await service.review_delta(world_delta.id)
    assert await service.maybe_auto_resolve(world_review) is None
    untouched = await SqliteStoryMemoryRepository(db).get_delta(world_delta.id)
    assert untouched is not None
    assert untouched.status is StoryMemoryDeltaStatus.PENDING


@pytest.mark.asyncio
async def test_auto_policy_is_off_by_default(db):
    delta = await _stage(
        db,
        _change(
            CharacterState("lin", "location", "旧城区"),
            chapter_id="chapter-1",
            revision="revision-1",
        ),
        chapter_id="chapter-1",
        revision="revision-1",
    )
    service = StoryMemoryEvolutionService(db)
    review = await service.review_delta(delta.id)

    assert await service.maybe_auto_resolve(review) is None
