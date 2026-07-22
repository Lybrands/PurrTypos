"""Contracts for chapter-to-Story-Memory analysis runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StoryMemoryAnalysisStatus(StrEnum):
    SKIPPED = "skipped"
    RUNNING = "running"
    COMPLETED = "completed"
    REUSED = "reused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StoryMemoryAnalysisReceipt:
    book_id: str
    chapter_id: str
    source_revision: str
    status: StoryMemoryAnalysisStatus
    candidate_count: int = 0
    delta_id: str | None = None
    reason: str = ""
    model: str = ""


__all__ = ["StoryMemoryAnalysisReceipt", "StoryMemoryAnalysisStatus"]
