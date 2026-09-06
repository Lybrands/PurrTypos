"""Book-bound model-input evidence validation for PurrA Core."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from purra.contracts import ContextEvidenceReceipt
from purra.ports import CancellationSignal

from application.memory_operations import MemoryApplicationService


@dataclass(frozen=True, slots=True)
class BookMemoryEvidenceValidator:
    memory: MemoryApplicationService
    book_id: str

    async def validate_evidence(
        self,
        receipts: Sequence[ContextEvidenceReceipt],
        *,
        signal: CancellationSignal | None = None,
    ) -> None:
        await self.memory.validate_evidence(
            book_id=self.book_id,
            receipts=receipts,
            signal=signal,
        )


__all__ = ["BookMemoryEvidenceValidator"]
