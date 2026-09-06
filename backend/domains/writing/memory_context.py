"""Writing memory context contracts and fail-closed selection facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryContextRequest:
    book_id: str | None
    user_prompt: str = ""
    token_budget: int = 6_000
    recall_limit: int = 16
    candidate_limit: int = 40
    selected_memory_item_ids: tuple[Any, ...] = ()
    selected_spark_idea_ids: tuple[Any, ...] = ()
    selected_foreshadowing_ids: tuple[Any, ...] = ()
    story_kinds: tuple[str, ...] = ()
    planner_story_kinds: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    chapter_ids: tuple[str, ...] = ()
    include_story_memory: bool = True
    authoritative_fingerprints: tuple[str, ...] = ()
    authoritative_signatures: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryUsageReceipt:
    evidence_id: str
    source: str
    item_id: str
    version: int | None = None
    source_id: str | None = None
    chapter_id: str | None = None
    memory_key: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "evidenceId": self.evidence_id,
                "source": self.source,
                "itemId": self.item_id,
                "version": self.version,
                "sourceId": self.source_id,
                "chapterId": self.chapter_id,
                "memoryKey": self.memory_key,
            }.items()
            if value not in (None, "")
        }


@dataclass(frozen=True, slots=True)
class SelectedMemoryContextFact:
    """ID-free manifest for user-selected memory material only."""

    requested_count: int
    complete_count: int
    truncated_count: int
    not_injected_count: int
    search_tools: tuple[str, ...] = ()
    locator_available_to_execution: bool = False

    def __post_init__(self) -> None:
        requested = max(0, int(self.requested_count))
        complete = max(0, int(self.complete_count))
        truncated = max(0, int(self.truncated_count))
        not_injected = max(0, int(self.not_injected_count))
        if complete + truncated + not_injected != requested:
            raise ValueError("selected memory fact counts must equal requested_count")
        object.__setattr__(self, "requested_count", requested)
        object.__setattr__(self, "complete_count", complete)
        object.__setattr__(self, "truncated_count", truncated)
        object.__setattr__(self, "not_injected_count", not_injected)
        object.__setattr__(self, "search_tools", tuple(dict.fromkeys(
            str(value).strip() for value in self.search_tools if str(value).strip()
        )))
        object.__setattr__(
            self,
            "locator_available_to_execution",
            bool(self.locator_available_to_execution),
        )

    @property
    def status(self) -> str:
        if self.requested_count and self.complete_count == self.requested_count:
            return "complete"
        if self.complete_count == 0 and self.truncated_count == 0:
            return "not_injected"
        return "truncated"

    def with_outer_truncation(self) -> "SelectedMemoryContextFact":
        return SelectedMemoryContextFact(
            requested_count=self.requested_count,
            complete_count=0,
            truncated_count=self.truncated_count + self.complete_count,
            not_injected_count=self.not_injected_count,
            search_tools=self.search_tools,
            locator_available_to_execution=False,
        )

    def to_planning_value(self) -> dict[str, Any]:
        return {
            "requestedCount": self.requested_count,
            "status": self.status,
            "completeCount": self.complete_count,
            "truncatedCount": self.truncated_count,
            "notInjectedCount": self.not_injected_count,
            "searchTools": list(self.search_tools),
            "locatorAvailableToExecution": self.locator_available_to_execution,
        }


@dataclass(slots=True)
class MemoryContextBlock:
    text: str
    included_ids: list[str] = field(default_factory=list)
    deferred_ids: list[str] = field(default_factory=list)
    suppressed_ids: list[str] = field(default_factory=list)
    token_estimate: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    receipts: tuple[MemoryUsageReceipt, ...] = ()
    selected_fact: SelectedMemoryContextFact | None = None

    def with_outer_truncation(self) -> "MemoryContextBlock":
        return MemoryContextBlock(
            text=self.text,
            included_ids=list(self.included_ids),
            deferred_ids=list(self.deferred_ids),
            suppressed_ids=list(self.suppressed_ids),
            token_estimate=self.token_estimate,
            diagnostics=dict(self.diagnostics),
            receipts=(),
            selected_fact=(
                self.selected_fact.with_outer_truncation()
                if self.selected_fact is not None
                else None
            ),
        )


def unavailable_memory_context(
    request: MemoryContextRequest,
) -> MemoryContextBlock:
    requested: set[tuple[str, str]] = set()
    search_tools: list[str] = []
    for kind, values, tool in (
        ("memory", request.selected_memory_item_ids, "searchMemories"),
        ("spark_idea", request.selected_spark_idea_ids, "searchSparkIdeas"),
        ("foreshadowing", request.selected_foreshadowing_ids, "searchSparkIdeas"),
    ):
        normalized = {
            str(value).strip() for value in values if str(value).strip()
        }
        if normalized:
            search_tools.append(tool)
            requested.update((kind, value) for value in normalized)
    count = len(requested)
    return MemoryContextBlock(
        text="",
        diagnostics={"unavailable": True},
        selected_fact=(
            SelectedMemoryContextFact(
                requested_count=count,
                complete_count=0,
                truncated_count=0,
                not_injected_count=count,
                search_tools=tuple(dict.fromkeys(search_tools)),
                locator_available_to_execution=False,
            )
            if count
            else None
        ),
    )


__all__ = [
    "MemoryContextBlock",
    "MemoryContextRequest",
    "MemoryUsageReceipt",
    "SelectedMemoryContextFact",
    "unavailable_memory_context",
]
