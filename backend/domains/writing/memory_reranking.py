"""Model-neutral contracts for unified memory candidate reranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agent_core.contracts import ModelRequest
from agent_core.ports import CancellationSignal


@dataclass(frozen=True, slots=True)
class MemoryCandidateCard:
    """Compact projection used for relevance judgment before full hydration."""

    id: str
    source: str
    kind: str
    fact: object
    subject_id: str | None = None
    chapter_id: str | None = None
    source_excerpt: str = ""
    version: int | None = None
    candidate_channels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        candidate_id = str(self.id or "").strip()
        if not candidate_id:
            raise ValueError("memory candidate requires an id")
        object.__setattr__(self, "id", candidate_id)
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(self, "kind", str(self.kind or "").strip())
        object.__setattr__(
            self,
            "subject_id",
            str(self.subject_id).strip() if self.subject_id is not None else None,
        )
        object.__setattr__(
            self,
            "chapter_id",
            str(self.chapter_id).strip() if self.chapter_id is not None else None,
        )
        object.__setattr__(
            self,
            "source_excerpt",
            str(self.source_excerpt or "").strip(),
        )
        object.__setattr__(
            self,
            "candidate_channels",
            tuple(dict.fromkeys(
                str(value).strip()
                for value in self.candidate_channels
                if str(value).strip()
            )),
        )


@dataclass(frozen=True, slots=True)
class MemoryRerankDecision:
    """One model-selected candidate; the host still validates the identifier."""

    record_id: str
    priority: str
    supports: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        record_id = str(self.record_id or "").strip()
        priority = str(self.priority or "").strip().casefold()
        if not record_id:
            raise ValueError("rerank decision requires a record id")
        if priority not in {"must_use", "helpful"}:
            raise ValueError("rerank priority must be must_use or helpful")
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(
            self,
            "supports",
            tuple(dict.fromkeys(
                str(value).strip()
                for value in self.supports
                if str(value).strip()
            )),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())


@dataclass(frozen=True, slots=True)
class MemoryRerankResult:
    """Validated model output before the host hydrates selected records."""

    decisions: tuple[MemoryRerankDecision, ...] = ()
    unresolved_needs: tuple[str, ...] = ()
    model: str | None = None
    batch_count: int = 0


class MemoryCandidateReranker(Protocol):
    """Expensive precision stage over a bounded, high-recall candidate set."""

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[MemoryCandidateCard],
        story_kinds: Sequence[str],
        planner_story_kinds: Sequence[str],
        entity_refs: Sequence[str],
        chapter_ids: Sequence[str],
        max_selected: int,
        model_request: ModelRequest,
        signal: CancellationSignal | None = None,
    ) -> MemoryRerankResult: ...


__all__ = [
    "MemoryCandidateCard",
    "MemoryCandidateReranker",
    "MemoryRerankDecision",
    "MemoryRerankResult",
]
