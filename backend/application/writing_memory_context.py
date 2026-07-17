"""Application use case for the Writing long-term-memory context endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    WritingMemoryContextBuilder,
)
from infrastructure.persistence.writing import SqliteMemoryRecallRepository
from schemas.memories import BuildMemoryContextRequest

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


DEFAULT_MEMORY_BUDGET = 6_000
_CONTEXT_WINDOWS = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "256k": 256_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


@dataclass(frozen=True, slots=True)
class WritingMemoryContextResult:
    """Application result with an explicit stable HTTP representation."""

    block: MemoryContextBlock

    def to_response_data(self) -> dict[str, Any]:
        return {
            "text": self.block.text,
            "includedIds": list(self.block.included_ids),
            "deferredIds": list(self.block.deferred_ids),
            "suppressedIds": list(self.block.suppressed_ids),
            "tokenEstimate": self.block.token_estimate,
            "diagnostics": dict(self.block.diagnostics),
        }


async def build_writing_memory_context(
    body: BuildMemoryContextRequest,
    *,
    db: "DatabaseConnection",
) -> WritingMemoryContextResult:
    """Map the transport request and execute the Writing memory use case."""

    request = _to_domain_request(body)
    builder = WritingMemoryContextBuilder(SqliteMemoryRecallRepository(db))
    return WritingMemoryContextResult(await builder.build(request))


def _to_domain_request(body: BuildMemoryContextRequest) -> MemoryContextRequest:
    return MemoryContextRequest(
        book_id=body.bookId,
        user_prompt=body.userPrompt,
        token_budget=_memory_budget(body),
        recall_limit=_memory_recall_limit(body),
        selected_memory_item_ids=tuple(body.selectedLongTermMemoryIds or ()),
        selected_spark_idea_ids=tuple(body.selectedMemoryIds or ()),
        selected_foreshadowing_ids=tuple(body.selectedForeshadowingIds or ()),
    )


def _context_window(value: Any) -> int:
    key = str(value or "").strip().lower()
    return _CONTEXT_WINDOWS.get(key, _CONTEXT_WINDOWS["200k"])


def _memory_budget(body: BuildMemoryContextRequest) -> int:
    if body.memoryBudget is not None:
        return int(body.memoryBudget)
    window = _context_window(body.contextWindow)
    return min(40_000, max(DEFAULT_MEMORY_BUDGET, window // 25))


def _memory_recall_limit(body: BuildMemoryContextRequest) -> int:
    if body.memoryRecallLimit:
        return int(body.memoryRecallLimit)
    window = _context_window(body.contextWindow)
    if window >= 1_000_000:
        return 64
    if window >= 300_000:
        return 32
    return 16


__all__ = [
    "WritingMemoryContextResult",
    "build_writing_memory_context",
]
