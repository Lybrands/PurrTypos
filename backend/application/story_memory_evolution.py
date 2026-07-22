"""Application orchestration for Story Memory evolution reviews."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from domains.writing.story_memory import (
    StoryMemoryConflictError,
    StoryMemoryDeltaDraft,
    StoryMemoryDeltaStatus,
    StoryMemoryNotFoundError,
    StoryMemoryOperation,
    StoryMemoryStatus,
)
from domains.writing.story_memory_evolution import (
    EvolutionClassification,
    EvolutionRecommendation,
    EvolutionResolution,
    EvolutionReviewStatus,
    EvolutionRisk,
    StoryMemoryEvolutionResolutionReceipt,
    StoryMemoryEvolutionReview,
)
from domains.writing.story_memory_evolution_engine import (
    evaluate_story_memory_delta,
)
from infrastructure.persistence.writing.sqlite_story_memory_evolution_repository import (
    SqliteStoryMemoryEvolutionRepository,
)
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)
from services.model_settings_service import (
    get_setting_value,
    is_setting_enabled,
)

STORY_MEMORY_AUTO_APPLY_ENABLED_KEY = "story_memory_auto_apply_enabled"
STORY_MEMORY_AUTO_APPLY_MIN_CONFIDENCE_KEY = (
    "story_memory_auto_apply_min_confidence"
)
STORY_MEMORY_AUTO_APPLY_KINDS_KEY = "story_memory_auto_apply_kinds"
DEFAULT_AUTO_APPLY_KINDS = (
    "character_state",
    "relationship_state",
    "plot_thread",
)


class StoryMemoryEvolutionService:
    def __init__(self, db: Any):
        self._db = db
        self._memory = SqliteStoryMemoryRepository(db)
        self._reviews = SqliteStoryMemoryEvolutionRepository(db)

    async def review_delta(self, delta_id: str) -> StoryMemoryEvolutionReview:
        existing_review = await self._reviews.get_review(delta_id)
        if existing_review is not None:
            return existing_review
        delta = await self._memory.get_delta(delta_id)
        if delta is None:
            raise StoryMemoryNotFoundError(delta_id)
        if delta.status is not StoryMemoryDeltaStatus.PENDING:
            raise ValueError(
                "only pending Story Memory deltas can receive a new evolution review"
            )
        records = await self._memory.list_records(delta.book_id)
        source_orders = await self._source_narrative_orders(records)
        review = evaluate_story_memory_delta(
            delta,
            records,
            source_narrative_orders=source_orders,
        )
        return await self._reviews.save_review(review)

    async def get_review(
        self,
        delta_id: str,
    ) -> StoryMemoryEvolutionReview | None:
        return await self._reviews.get_review(delta_id)

    async def list_reviews(
        self,
        book_id: str,
        *,
        statuses: Sequence[str] = (),
    ) -> tuple[StoryMemoryEvolutionReview, ...]:
        return await self._reviews.list_reviews(book_id, statuses=statuses)

    async def resolve_review(
        self,
        delta_id: str,
        resolutions: Mapping[str, str],
        *,
        actor: str = "user",
    ) -> StoryMemoryEvolutionResolutionReceipt:
        resolved_review: StoryMemoryEvolutionReview | None = None
        receipt: StoryMemoryEvolutionResolutionReceipt | None = None
        stale_error = ""
        async with self._db.transaction(cancellation_linearizable=True):
            # Reading, snapshot validation and deriving the accepted child Delta
            # share one write transaction. This makes duplicate clicks and
            # concurrent resolve requests converge on the same durable result.
            review = await self._reviews.get_review(delta_id)
            if review is None:
                raise StoryMemoryNotFoundError(delta_id)
            if review.status is EvolutionReviewStatus.RESOLVED:
                resolved_review = review
            elif review.status is EvolutionReviewStatus.STALE:
                stale_error = "evolution review is stale"
            else:
                delta = await self._memory.get_delta(delta_id)
                if delta is None:
                    raise StoryMemoryNotFoundError(delta_id)
                if delta.status is not StoryMemoryDeltaStatus.PENDING:
                    await self._mark_review_stale(delta_id)
                    stale_error = f"delta {delta_id} is no longer pending"

            if not resolved_review and not stale_error:
                expected_keys = {item.target_key for item in review.decisions}
                supplied_keys = {str(key).strip() for key in resolutions}
                if supplied_keys != expected_keys:
                    missing = sorted(expected_keys - supplied_keys)
                    unknown = sorted(supplied_keys - expected_keys)
                    raise ValueError(
                        "resolutions must cover every decision; "
                        f"missing={missing}, unknown={unknown}"
                    )
                normalized = {
                    str(key).strip(): EvolutionResolution(str(value))
                    for key, value in resolutions.items()
                }
                if any(
                    value not in {
                        EvolutionResolution.ACCEPTED,
                        EvolutionResolution.REJECTED,
                    }
                    for value in normalized.values()
                ):
                    raise ValueError("resolution must be accepted or rejected")

                accepted_keys = tuple(
                    item.target_key
                    for item in review.decisions
                    if normalized[item.target_key] is EvolutionResolution.ACCEPTED
                )
                rejected_keys = tuple(
                    item.target_key
                    for item in review.decisions
                    if normalized[item.target_key] is EvolutionResolution.REJECTED
                )
                stale_error = await self._review_snapshot_conflict(
                    review,
                    accepted_keys,
                )
                if stale_error:
                    await self._mark_review_stale(delta_id)

            if not resolved_review and not stale_error:
                change_by_key = {item.target_key: item for item in delta.changes}
                applied_delta_id = None
                if accepted_keys:
                    promoted_changes = tuple(
                        replace(
                            change_by_key[key],
                            status=(
                                StoryMemoryStatus.CONFIRMED
                                if change_by_key[key].operation
                                is StoryMemoryOperation.UPSERT
                                else change_by_key[key].status
                            ),
                        )
                        for key in accepted_keys
                    )
                    applied_delta = await self._memory.create_delta(
                        StoryMemoryDeltaDraft(
                            book_id=delta.book_id,
                            chapter_id=delta.chapter_id,
                            changes=promoted_changes,
                            source_revision=delta.source_revision,
                            source_type="evolution_resolution",
                            note=(
                                f"resolved from candidate delta {delta.id}; "
                                f"actor={actor}"
                            ),
                        )
                    )
                    applied_delta_id = applied_delta.id

                await self._db.execute(
                    "UPDATE story_memory_deltas SET status = 'invalidated', "
                    "invalidated_at = datetime('now'), note = note || ? "
                    "WHERE id = ? AND status = 'pending'",
                    [f"; resolved_by={actor}", delta.id],
                )
                for decision in review.decisions:
                    resolution = normalized[decision.target_key]
                    await self._db.execute(
                        "UPDATE story_memory_evolution_reviews SET "
                        "review_status = 'resolved', resolution = ?, "
                        "resolved_delta_id = ?, resolution_actor = ?, "
                        "resolved_at = datetime('now'), "
                        "update_time = datetime('now') "
                        "WHERE delta_id = ? AND target_key = ?",
                        [
                            resolution.value,
                            (
                                applied_delta_id
                                if resolution is EvolutionResolution.ACCEPTED
                                else None
                            ),
                            actor,
                            delta.id,
                            decision.target_key,
                        ],
                    )
                receipt = StoryMemoryEvolutionResolutionReceipt(
                    original_delta_id=delta.id,
                    applied_delta_id=applied_delta_id,
                    accepted_keys=accepted_keys,
                    rejected_keys=rejected_keys,
                    status="applied" if applied_delta_id else "rejected",
                    actor=actor,
                )

        if stale_error:
            raise StoryMemoryConflictError(stale_error)
        if resolved_review is not None:
            return await self._resume_resolved_review(resolved_review)
        if receipt is None:  # pragma: no cover - exhaustive state invariant
            raise RuntimeError("Story Memory resolution produced no receipt")
        if receipt.applied_delta_id:
            await self._memory.apply_delta(receipt.applied_delta_id)
        return receipt

    async def maybe_auto_resolve(
        self,
        review: StoryMemoryEvolutionReview,
    ) -> StoryMemoryEvolutionResolutionReceipt | None:
        if not await is_setting_enabled(
            self._db,
            STORY_MEMORY_AUTO_APPLY_ENABLED_KEY,
        ):
            return None
        threshold = _confidence_threshold(
            await get_setting_value(
                self._db,
                STORY_MEMORY_AUTO_APPLY_MIN_CONFIDENCE_KEY,
            )
        )
        raw_kinds = await get_setting_value(
            self._db,
            STORY_MEMORY_AUTO_APPLY_KINDS_KEY,
        )
        allowed_kinds = {
            str(value).strip()
            for value in (
                raw_kinds
                if isinstance(raw_kinds, list)
                else DEFAULT_AUTO_APPLY_KINDS
            )
            if str(value).strip()
        }
        resolutions: dict[str, str] = {}
        for decision in review.decisions:
            if (
                decision.classification is EvolutionClassification.DUPLICATE
                and decision.recommendation is EvolutionRecommendation.REJECT
                and decision.risk is EvolutionRisk.LOW
            ):
                resolutions[decision.target_key] = EvolutionResolution.REJECTED.value
                continue
            eligible = (
                decision.classification is EvolutionClassification.ADDITION
                and decision.recommendation is EvolutionRecommendation.APPLY
                and decision.risk is EvolutionRisk.LOW
                and decision.candidate_confidence >= threshold
                and decision.kind.value in allowed_kinds
            )
            if not eligible:
                return None
            resolutions[decision.target_key] = EvolutionResolution.ACCEPTED.value
        return await self.resolve_review(
            review.delta_id,
            resolutions,
            actor="auto_policy",
        )

    async def _review_snapshot_conflict(
        self,
        review: StoryMemoryEvolutionReview,
        accepted_keys: Sequence[str],
    ) -> str:
        accepted = set(accepted_keys)
        for decision in review.decisions:
            if decision.target_key not in accepted:
                continue
            if decision.existing_record_id:
                key = decision.related_memory_key or decision.target_key
                record = await self._memory.get_record(review.book_id, key)
                if (
                    record is None
                    or record.id != decision.existing_record_id
                    or record.version != decision.existing_version
                ):
                    return f"Story Memory changed after review: {key}"
            else:
                record = await self._memory.get_record(
                    review.book_id,
                    decision.target_key,
                )
                if record is not None:
                    return (
                        "Story Memory was created after review: "
                        f"{decision.target_key}"
                    )
        return ""

    async def _mark_review_stale(self, delta_id: str) -> None:
        await self._db.execute(
            "UPDATE story_memory_evolution_reviews SET review_status = 'stale', "
            "update_time = datetime('now') WHERE delta_id = ?",
            [delta_id],
        )

    async def _resume_resolved_review(
        self,
        review: StoryMemoryEvolutionReview,
    ) -> StoryMemoryEvolutionResolutionReceipt:
        accepted = tuple(
            item.target_key
            for item in review.decisions
            if item.resolution is EvolutionResolution.ACCEPTED
        )
        rejected = tuple(
            item.target_key
            for item in review.decisions
            if item.resolution is EvolutionResolution.REJECTED
        )
        applied_delta_id = next(
            (
                item.resolved_delta_id
                for item in review.decisions
                if item.resolved_delta_id
            ),
            None,
        )
        if applied_delta_id:
            applied = await self._memory.get_delta(applied_delta_id)
            if applied and applied.status is StoryMemoryDeltaStatus.PENDING:
                await self._memory.apply_delta(applied_delta_id)
        actor = next(
            (
                item.resolution_actor
                for item in review.decisions
                if item.resolution_actor
            ),
            "unknown",
        )
        return StoryMemoryEvolutionResolutionReceipt(
            original_delta_id=review.delta_id,
            applied_delta_id=applied_delta_id,
            accepted_keys=accepted,
            rejected_keys=rejected,
            status="applied" if applied_delta_id else "rejected",
            actor=actor,
        )

    async def _source_narrative_orders(self, records) -> dict[str, int | None]:
        source_ids = [
            str(item.last_source_id)
            for item in records
            if item.last_source_id
        ]
        if not source_ids:
            return {}
        marks = ",".join("?" for _ in source_ids)
        rows = await self._db.fetch_all(
            "SELECT id, narrative_order FROM story_memory_sources "
            f"WHERE id IN ({marks})",
            source_ids,
        )
        return {
            str(item["id"]): (
                int(item["narrative_order"])
                if item.get("narrative_order") is not None
                else None
            )
            for item in rows
        }


__all__ = ["StoryMemoryEvolutionService"]


def _confidence_threshold(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.95
    return max(0.5, min(1.0, parsed))
