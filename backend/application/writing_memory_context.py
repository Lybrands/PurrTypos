"""Authorized non-Run memory context for inline writing operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from purra.context_budget import estimate_json_tokens

from application.memory_operations import MemoryApplicationService
from application.component_memory_context import (
    assemble_selected_business_sources,
)
from schemas.memories import BuildMemoryContextRequest

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


DEFAULT_MEMORY_BUDGET = 6_000
_CONTEXT_WINDOWS = {
    "32k": 32_000, "64k": 64_000, "128k": 128_000, "200k": 200_000,
    "256k": 256_000, "300k": 300_000, "1m": 1_000_000,
}


@dataclass(frozen=True, slots=True)
class WritingMemoryContextResult:
    text: str
    included_ids: tuple[str, ...]
    deferred_ids: tuple[str, ...]
    missing_ids: tuple[str, ...]
    token_estimate: int
    receipt_count: int

    def to_response_data(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "includedIds": list(self.included_ids),
            "deferredIds": list(self.deferred_ids),
            "missingIds": list(self.missing_ids),
            "tokenEstimate": self.token_estimate,
            "diagnostics": {"receiptCount": self.receipt_count},
        }


async def build_writing_memory_context(
    body: BuildMemoryContextRequest,
    *,
    db: "DatabaseConnection",
) -> WritingMemoryContextResult:
    from application.agent_composition import get_agent_composition

    budget = _memory_budget(body)
    component = MemoryApplicationService(
        db, get_agent_composition().memory_resource
    )
    from infrastructure.persistence.writing import SqliteWritingSourceRepository

    sources = SqliteWritingSourceRepository(db)
    semantic = await component.build_context(
        book_id=body.bookId,
        operation_key=body.operationKey,
        query=body.userPrompt,
        selected_ids=tuple(
            str(value) for value in (body.selectedLongTermMemoryIds or ())
        ),
        token_budget=budget,
        recall_limit=_memory_recall_limit(body),
    )
    text, included, deferred = await assemble_selected_business_sources(
        sources,
        book_id=body.bookId,
        selected_spark_ids=body.selectedMemoryIds or (),
        selected_foreshadowing_ids=body.selectedForeshadowingIds or (),
        sections=(
            ("【语义长期记忆】\n" + semantic.block.content,)
            if semantic.block is not None else ()
        ),
        included=semantic.included,
        deferred=semantic.deferred,
        token_budget=budget,
    )
    return WritingMemoryContextResult(
        text=text,
        included_ids=tuple(included),
        deferred_ids=tuple(deferred),
        missing_ids=semantic.missing,
        token_estimate=estimate_json_tokens(text),
        receipt_count=len(semantic.receipts),
    )


def _context_window(value: Any) -> int:
    return _CONTEXT_WINDOWS.get(
        str(value or "").strip().lower(), _CONTEXT_WINDOWS["200k"]
    )


def _memory_budget(body: BuildMemoryContextRequest) -> int:
    if body.memoryBudget is not None:
        return int(body.memoryBudget)
    return min(40_000, max(DEFAULT_MEMORY_BUDGET, _context_window(body.contextWindow) // 25))


def _memory_recall_limit(body: BuildMemoryContextRequest) -> int:
    if body.memoryRecallLimit:
        return int(body.memoryRecallLimit)
    window = _context_window(body.contextWindow)
    return 64 if window >= 1_000_000 else 32 if window >= 300_000 else 16


__all__ = ["WritingMemoryContextResult", "build_writing_memory_context"]
