"""Task-scoped Story Memory + semantic memory retrieval and assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from purra.context_budget import estimate_json_tokens
from purra.contracts import ModelRequest, TaskContextRequest
from purra.json_values import thaw_json_mapping
from purra.ports import CancellationSignal
from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    MemoryUsageReceipt,
    SelectedMemoryContextFact,
    WritingMemoryContextBuilder,
)
from domains.writing.repositories import (
    StoryMemoryRecallItem,
    StoryMemoryRecallRepository,
)
from domains.writing.memory_reranking import (
    MemoryCandidateCard,
    MemoryCandidateReranker,
    MemoryRerankDecision,
)


_STORY_KIND_LABELS = {
    "character_state": "人物当前状态",
    "relationship_state": "人物关系",
    "world_fact": "世界设定",
    "timeline_event": "时间线事实",
    "plot_thread": "剧情线状态",
}

_STORY_KIND_ALIASES = {
    "character": ("character_state",),
    "characters": ("character_state",),
    "人物": ("character_state",),
    "人物状态": ("character_state",),
    "relationship": ("relationship_state",),
    "relationships": ("relationship_state",),
    "关系": ("relationship_state",),
    "人物关系": ("relationship_state",),
    "world": ("world_fact",),
    "world_facts": ("world_fact",),
    "世界": ("world_fact",),
    "世界设定": ("world_fact",),
    "timeline": ("timeline_event",),
    "时间线": ("timeline_event",),
    "plot": ("plot_thread", "timeline_event"),
    "plot_threads": ("plot_thread",),
    "剧情": ("plot_thread", "timeline_event"),
    "剧情线": ("plot_thread",),
    "canon": (
        "character_state",
        "relationship_state",
        "world_fact",
        "timeline_event",
        "plot_thread",
    ),
}


@dataclass(slots=True)
class StoryMemoryContextBlock:
    text: str = ""
    included_items: tuple[StoryMemoryRecallItem, ...] = ()
    deferred_keys: tuple[str, ...] = ()
    token_estimate: int = 0
    authority_fingerprints: tuple[str, ...] = ()
    authority_signatures: tuple[tuple[str, ...], ...] = ()
    receipts: tuple[MemoryUsageReceipt, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryContextPack:
    """Reusable, receipt-bearing memory context for the complete Agent Run."""

    text: str
    semantic: MemoryContextBlock
    story: StoryMemoryContextBlock
    token_estimate: int
    receipts: tuple[MemoryUsageReceipt, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    outer_truncated: bool = False

    @property
    def included_ids(self) -> list[int]:
        return list(self.semantic.included_ids)

    @property
    def deferred_ids(self) -> list[int]:
        return list(self.semantic.deferred_ids)

    @property
    def suppressed_ids(self) -> list[int]:
        return list(self.semantic.suppressed_ids)

    @property
    def selected_fact(self) -> SelectedMemoryContextFact | None:
        return self.semantic.selected_fact

    def with_outer_truncation(self) -> "MemoryContextPack":
        return MemoryContextPack(
            text=self.text,
            semantic=self.semantic.with_outer_truncation(),
            story=self.story,
            token_estimate=self.token_estimate,
            receipts=(),
            diagnostics={
                **self.diagnostics,
                "receiptCount": 0,
                "outerTruncated": True,
            },
            outer_truncated=True,
        )


class StoryMemoryContextProvider:
    """Select and format authoritative current story state."""

    def __init__(
        self,
        repository: StoryMemoryRecallRepository,
        reranker: MemoryCandidateReranker | None = None,
    ):
        self._repository = repository
        self._reranker = reranker

    async def build(
        self,
        request: MemoryContextRequest,
        *,
        model_request: ModelRequest | None = None,
        signal: CancellationSignal | None = None,
    ) -> StoryMemoryContextBlock:
        book_id = str(request.book_id or "").strip()
        budget = max(0, int(request.token_budget))
        if not book_id or budget <= 0 or not request.include_story_memory:
            return StoryMemoryContextBlock(diagnostics=_story_diagnostics())
        maximum = max(1, min(64, int(request.recall_limit)))
        candidates = await self._repository.search_current(
            book_id,
            request.user_prompt,
            kinds=request.story_kinds,
            planner_kinds=request.planner_story_kinds,
            entity_refs=request.entity_refs,
            chapter_ids=request.chapter_ids,
            limit=max(
                maximum,
                min(160, max(1, int(request.candidate_limit))),
            ),
        )
        items = candidates[:maximum]
        rerank_diagnostics: dict[str, Any] = {
            "candidateCount": len(candidates),
            "rerankerUsed": False,
            "rerankerStatus": "not_configured",
        }
        decisions: tuple[MemoryRerankDecision, ...] = ()
        if self._reranker is not None and model_request is not None and candidates:
            try:
                reranked = await self._reranker.rerank(
                    query=request.user_prompt,
                    candidates=tuple(
                        _story_candidate_card(item) for item in candidates
                    ),
                    story_kinds=request.story_kinds,
                    planner_story_kinds=request.planner_story_kinds,
                    entity_refs=request.entity_refs,
                    chapter_ids=request.chapter_ids,
                    max_selected=maximum,
                    model_request=model_request,
                    signal=signal,
                )
                decisions = reranked.decisions
                candidate_by_id = {
                    item.record_id: item for item in candidates
                }
                selected_ids = tuple(
                    decision.record_id
                    for decision in decisions
                    if decision.record_id in candidate_by_id
                )
                refreshed = await self._repository.get_current_by_ids(
                    book_id,
                    selected_ids,
                )
                refreshed_by_id = {
                    item.record_id: item for item in refreshed
                }
                invalidated_ids: list[str] = []
                validated_items: list[StoryMemoryRecallItem] = []
                for record_id in selected_ids:
                    candidate = candidate_by_id[record_id]
                    current = refreshed_by_id.get(record_id)
                    if current is None or current.version != candidate.version:
                        invalidated_ids.append(record_id)
                        continue
                    validated_items.append(replace(
                        current,
                        candidate_channels=candidate.candidate_channels,
                        retrieval_score=candidate.retrieval_score,
                    ))
                items = tuple(validated_items[:maximum])
                rerank_diagnostics = {
                    "candidateCount": len(candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "completed",
                    "rerankerModel": reranked.model,
                    "rerankerBatchCount": reranked.batch_count,
                    "rerankerSelectedCount": len(items),
                    "rerankerInvalidatedCount": len(invalidated_ids),
                    "rerankerInvalidatedIds": invalidated_ids,
                    "rerankerUnresolvedNeeds": list(reranked.unresolved_needs),
                    "rerankerDecisions": [
                        {
                            "recordId": decision.record_id,
                            "priority": decision.priority,
                            "supports": list(decision.supports),
                            "reason": decision.reason,
                        }
                        for decision in decisions
                    ],
                }
            except Exception as error:
                # Recall is optional context. A malformed or unavailable judge
                # must not erase deterministic candidates or fail the Agent Run.
                items = candidates[:maximum]
                rerank_diagnostics = {
                    "candidateCount": len(candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "fallback",
                    "rerankerError": type(error).__name__,
                }
        included: list[StoryMemoryRecallItem] = []
        deferred: list[str] = []
        rows: list[str] = []
        for item in items:
            row = _render_story_item(item)
            candidate = _compose_story_text([*rows, row], 0)
            if estimate_json_tokens(candidate) <= budget:
                included.append(item)
                rows.append(row)
            else:
                deferred.append(item.memory_key)
        text = _compose_story_text(rows, len(deferred)) if rows else ""
        fingerprints = tuple(dict.fromkeys(
            value
            for item in included
            for value in _story_fingerprints(item)
            if value
        ))
        signatures = tuple(_story_signature(item) for item in included)
        receipts = tuple(
            MemoryUsageReceipt(
                evidence_id=f"story:{item.record_id}:v{item.version}",
                source="story_state",
                item_id=item.record_id,
                version=item.version,
                source_id=item.source_id,
                chapter_id=item.chapter_id,
                memory_key=item.memory_key,
            )
            for item in included
        )
        return StoryMemoryContextBlock(
            text=text,
            included_items=tuple(included),
            deferred_keys=tuple(deferred),
            token_estimate=estimate_json_tokens(text),
            authority_fingerprints=fingerprints,
            authority_signatures=signatures,
            receipts=receipts,
            diagnostics=_story_diagnostics(
                recalled=len(candidates),
                included=len(included),
                deferred=len(deferred),
                selected=len(items),
                **rerank_diagnostics,
            ),
        )


class MemoryContextAssembler:
    """Combine authorities, enforce priority and return a bounded pack."""

    def assemble(
        self,
        story: StoryMemoryContextBlock,
        semantic: MemoryContextBlock,
        *,
        token_budget: int,
    ) -> MemoryContextPack:
        budget = max(0, int(token_budget))
        sections = ["【本轮记忆上下文 — 由宿主按 TaskSpec 召回】"]
        if story.text:
            sections.extend(("\n## 权威 Story Memory", story.text))
        if semantic.text:
            sections.extend(("\n## 语义长期记忆", semantic.text))
        text = "\n".join(sections) if len(sections) > 1 else ""
        outer_truncated = False
        if estimate_json_tokens(text) > budget:
            text = _fit_json_tokens(text, budget)
            outer_truncated = True
        receipts = (
            ()
            if outer_truncated
            else (*story.receipts, *semantic.receipts)
        )
        diagnostics = {
            "story": dict(story.diagnostics),
            "semantic": dict(semantic.diagnostics),
            "receiptCount": len(receipts),
            "authoritySuppressedCount": len(semantic.suppressed_ids),
            "outerTruncated": outer_truncated,
        }
        return MemoryContextPack(
            text=text,
            semantic=(
                semantic.with_outer_truncation()
                if outer_truncated else semantic
            ),
            story=story,
            token_estimate=estimate_json_tokens(text),
            receipts=receipts,
            diagnostics=diagnostics,
            outer_truncated=outer_truncated,
        )


class UnifiedMemoryRetriever:
    """Recall authoritative story state first, then semantic memory."""

    def __init__(
        self,
        semantic_builder: WritingMemoryContextBuilder,
        story_provider: StoryMemoryContextProvider | None = None,
        assembler: MemoryContextAssembler | None = None,
    ):
        self._semantic = semantic_builder
        self._story = story_provider
        self._assembler = assembler or MemoryContextAssembler()

    async def build(
        self,
        request: MemoryContextRequest,
        *,
        model_request: ModelRequest | None = None,
        signal: CancellationSignal | None = None,
    ) -> MemoryContextPack:
        total_budget = max(0, int(request.token_budget))
        overhead_reserve = min(120, total_budget)
        content_budget = max(0, total_budget - overhead_reserve)
        story_budget = (
            min(content_budget, max(240, content_budget * 11 // 20))
            if self._story is not None and request.include_story_memory
            else 0
        )
        story = (
            await self._story.build(
                replace(request, token_budget=story_budget),
                model_request=model_request,
                signal=signal,
            )
            if self._story is not None
            else StoryMemoryContextBlock(diagnostics=_story_diagnostics())
        )
        semantic_budget = max(0, content_budget - story.token_estimate)
        semantic = await self._semantic.build(
            replace(
                request,
                token_budget=semantic_budget,
                authoritative_fingerprints=story.authority_fingerprints,
                authoritative_signatures=story.authority_signatures,
            ),
            model_request=model_request,
            signal=signal,
        )
        return self._assembler.assemble(
            story,
            semantic,
            token_budget=total_budget,
        )


def memory_context_request_from_task(
    request: MemoryContextRequest,
    task: TaskContextRequest | None,
    *,
    current_chapter_id: str | None = None,
) -> MemoryContextRequest:
    """Compile planner semantic hints plus host-owned evidence declarations."""

    if task is None:
        return replace(
            request,
            chapter_ids=_clean_values((current_chapter_id,)),
        )
    target = thaw_json_mapping(task.task_spec.target)
    requested_context = _target_values(
        target,
        "storyContext",
        "story_context",
        "contextNeeds",
        "context_needs",
    )
    planner_kinds: list[str] = []
    for value in requested_context:
        normalized = str(value).strip()
        if normalized in _STORY_KIND_LABELS:
            planner_kinds.append(normalized)
        planner_kinds.extend(_STORY_KIND_ALIASES.get(normalized.casefold(), ()))
    contract_kinds: list[str] = []
    for value in task.evidence_kinds:
        normalized = str(value).strip()
        if normalized in _STORY_KIND_LABELS:
            contract_kinds.append(normalized)
        contract_kinds.extend(_STORY_KIND_ALIASES.get(normalized.casefold(), ()))
    entities = _target_values(
        target,
        "entities",
        "entityRefs",
        "entity_refs",
        "characters",
        "characterIds",
        "character_ids",
        "relationships",
    )
    chapters = _target_values(
        target,
        "chapterIds",
        "chapter_ids",
        "chapters",
        "chapter",
    )
    return replace(
        request,
        story_kinds=_clean_values((*planner_kinds, *contract_kinds)),
        planner_story_kinds=_clean_values(planner_kinds),
        entity_refs=_clean_values(entities),
        chapter_ids=_clean_values((*chapters, current_chapter_id)),
    )


def _target_values(target: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        value = target.get(key)
        if isinstance(value, Mapping):
            values.extend(str(item) for item in value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(str(item) for item in value)
        elif value not in (None, ""):
            values.append(str(value))
    return _clean_values(values)


def _clean_values(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(value).strip()
        for value in values
        if value is not None and str(value).strip()
    ))


def _render_story_item(item: StoryMemoryRecallItem) -> str:
    label = _STORY_KIND_LABELS.get(item.kind, item.kind)
    payload = json.dumps(
        dict(item.payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    excerpt = " ".join(item.source_excerpt.split())[:320]
    source = f"chapter:{item.chapter_id}|source:{item.source_id}"
    return (
        f"- [{item.memory_key}|{label}|v{item.version}|{source}] {payload}"
        + (f"\n  证据：{excerpt}" if excerpt else "")
    )


def _story_candidate_card(item: StoryMemoryRecallItem) -> MemoryCandidateCard:
    return MemoryCandidateCard(
        id=item.record_id,
        source="story_state",
        kind=item.kind,
        fact=dict(item.payload),
        subject_id=item.subject_id,
        chapter_id=item.chapter_id,
        source_excerpt=item.source_excerpt,
        version=item.version,
        candidate_channels=item.candidate_channels,
    )


def _compose_story_text(rows: Sequence[str], deferred_count: int) -> str:
    lines = ["【Story Memory — 已确认、当前有效且证据有效】", *rows]
    if deferred_count:
        lines.append(f"另有 {deferred_count} 条相关故事状态因预算限制未注入。")
    return "\n".join(lines)


def _story_fingerprints(item: StoryMemoryRecallItem) -> tuple[str, ...]:
    values = [
        item.memory_key,
        json.dumps(
            dict(item.payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        *_leaf_strings(item.payload),
    ]
    return tuple(dict.fromkeys(_fingerprint(value) for value in values if value))


def _story_signature(item: StoryMemoryRecallItem) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value
        for key, raw in item.payload.items()
        if not str(key).casefold().endswith(("id", "ids"))
        for value in _leaf_strings(raw)
        if len(_fingerprint(value)) >= 2
    ))


def _leaf_strings(value: object) -> list[str]:
    if isinstance(value, Mapping):
        return [row for item in value.values() for row in _leaf_strings(item)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [row for item in value for row in _leaf_strings(item)]
    return [str(value)] if value not in (None, "") else []


def _fingerprint(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def _fit_json_tokens(text: str, budget: int) -> str:
    if not text or budget <= 0:
        return ""
    if estimate_json_tokens(text) <= budget:
        return text
    marker = "\n…（记忆上下文已按预算截断）"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle] + marker
        if estimate_json_tokens(candidate) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + (marker if low else "")


def _story_diagnostics(
    *,
    recalled: int = 0,
    included: int = 0,
    deferred: int = 0,
    selected: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "recalled": recalled,
        "selected": selected,
        "included": included,
        "deferred": deferred,
        **extra,
    }


__all__ = [
    "MemoryContextAssembler",
    "MemoryContextPack",
    "StoryMemoryContextBlock",
    "StoryMemoryContextProvider",
    "UnifiedMemoryRetriever",
    "memory_context_request_from_task",
]
