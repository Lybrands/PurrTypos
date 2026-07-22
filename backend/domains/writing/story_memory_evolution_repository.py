"""Persistence port for explainable Story Memory evolution reviews."""

from __future__ import annotations

from typing import Protocol, Sequence

from domains.writing.story_memory_evolution import StoryMemoryEvolutionReview


class StoryMemoryEvolutionRepository(Protocol):
    async def save_review(
        self,
        review: StoryMemoryEvolutionReview,
    ) -> StoryMemoryEvolutionReview: ...

    async def get_review(
        self,
        delta_id: str,
    ) -> StoryMemoryEvolutionReview | None: ...

    async def list_reviews(
        self,
        book_id: str,
        *,
        statuses: Sequence[str] = (),
    ) -> tuple[StoryMemoryEvolutionReview, ...]: ...


__all__ = ["StoryMemoryEvolutionRepository"]
