"""SQLite persistence for Story Memory evolution reviews."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from domains.writing.story_memory import StoryMemoryKind
from domains.writing.story_memory_evolution import (
    EvolutionClassification,
    EvolutionFieldChange,
    EvolutionRecommendation,
    EvolutionResolution,
    EvolutionReviewStatus,
    EvolutionRisk,
    StoryMemoryEvolutionDecision,
    StoryMemoryEvolutionReview,
)

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


class SqliteStoryMemoryEvolutionRepository:
    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def save_review(
        self,
        review: StoryMemoryEvolutionReview,
    ) -> StoryMemoryEvolutionReview:
        async with self._db.transaction():
            for decision in review.decisions:
                await self._db.execute(
                    "INSERT OR IGNORE INTO story_memory_evolution_reviews "
                    "(book_id, chapter_id, delta_id, target_key, kind, "
                    "classification, recommendation, risk, rationale, "
                    "field_changes_json, candidate_payload_json, source_excerpt, "
                    "related_memory_key, existing_record_id, existing_version, "
                    "candidate_confidence, review_status, resolution, "
                    "resolved_delta_id, resolution_actor, resolved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        review.book_id,
                        review.chapter_id,
                        review.delta_id,
                        decision.target_key,
                        decision.kind.value,
                        decision.classification.value,
                        decision.recommendation.value,
                        decision.risk.value,
                        decision.rationale,
                        _json_dump([
                            {
                                "field": item.field,
                                "before": item.before,
                                "after": item.after,
                            }
                            for item in decision.field_changes
                        ]),
                        _json_dump(decision.candidate_payload or {}),
                        decision.source_excerpt,
                        decision.related_memory_key,
                        decision.existing_record_id,
                        decision.existing_version,
                        decision.candidate_confidence,
                        decision.review_status.value,
                        decision.resolution.value,
                        decision.resolved_delta_id,
                        decision.resolution_actor,
                        decision.resolved_at,
                    ],
                )
        stored = await self.get_review(review.delta_id)
        if stored is None:  # pragma: no cover - durable insert invariant
            raise RuntimeError("failed to persist Story Memory evolution review")
        return stored

    async def get_review(
        self,
        delta_id: str,
    ) -> StoryMemoryEvolutionReview | None:
        rows = await self._db.fetch_all(
            "SELECT * FROM story_memory_evolution_reviews WHERE delta_id = ? "
            "ORDER BY id ASC",
            [str(delta_id).strip()],
        )
        if not rows:
            return None
        decisions = tuple(_decision_from_row(item) for item in rows)
        statuses = {item.review_status for item in decisions}
        if EvolutionReviewStatus.STALE in statuses:
            status = EvolutionReviewStatus.STALE
        elif statuses == {EvolutionReviewStatus.RESOLVED}:
            status = EvolutionReviewStatus.RESOLVED
        else:
            status = EvolutionReviewStatus.OPEN
        return StoryMemoryEvolutionReview(
            delta_id=str(rows[0]["delta_id"]),
            book_id=str(rows[0]["book_id"]),
            chapter_id=str(rows[0]["chapter_id"]),
            decisions=decisions,
            status=status,
        )

    async def list_reviews(
        self,
        book_id: str,
        *,
        statuses: Sequence[str] = (),
    ) -> tuple[StoryMemoryEvolutionReview, ...]:
        where = ["book_id = ?"]
        params: list[Any] = [str(book_id).strip()]
        clean_statuses = [
            str(value).strip()
            for value in statuses
            if str(value).strip()
        ]
        if clean_statuses:
            marks = ",".join("?" for _ in clean_statuses)
            where.append(f"review_status IN ({marks})")
            params.extend(clean_statuses)
        rows = await self._db.fetch_all(
            "SELECT delta_id, MAX(create_time) AS latest FROM "
            "story_memory_evolution_reviews WHERE "
            + " AND ".join(where)
            + " GROUP BY delta_id ORDER BY latest DESC",
            params,
        )
        reviews = []
        for row in rows:
            review = await self.get_review(str(row["delta_id"]))
            if review is not None:
                reviews.append(review)
        return tuple(reviews)


def _decision_from_row(row: Mapping[str, Any]) -> StoryMemoryEvolutionDecision:
    return StoryMemoryEvolutionDecision(
        delta_id=str(row["delta_id"]),
        target_key=str(row["target_key"]),
        kind=StoryMemoryKind(str(row["kind"])),
        classification=EvolutionClassification(str(row["classification"])),
        recommendation=EvolutionRecommendation(str(row["recommendation"])),
        risk=EvolutionRisk(str(row["risk"])),
        rationale=str(row.get("rationale") or ""),
        field_changes=tuple(
            EvolutionFieldChange(
                field=str(item.get("field") or ""),
                before=item.get("before"),
                after=item.get("after"),
            )
            for item in _json_list(row.get("field_changes_json"))
            if isinstance(item, Mapping)
        ),
        candidate_payload=_json_object(row.get("candidate_payload_json")),
        source_excerpt=str(row.get("source_excerpt") or ""),
        related_memory_key=_optional(row.get("related_memory_key")),
        existing_record_id=_optional(row.get("existing_record_id")),
        existing_version=(
            int(row["existing_version"])
            if row.get("existing_version") is not None
            else None
        ),
        candidate_confidence=float(row.get("candidate_confidence") or 0.0),
        review_status=EvolutionReviewStatus(str(row["review_status"])),
        resolution=EvolutionResolution(str(row["resolution"])),
        resolved_delta_id=_optional(row.get("resolved_delta_id")),
        resolution_actor=_optional(row.get("resolution_actor")),
        resolved_at=_optional(row.get("resolved_at")),
        create_time=_optional(row.get("create_time")),
        update_time=_optional(row.get("update_time")),
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _optional(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


__all__ = ["SqliteStoryMemoryEvolutionRepository"]
