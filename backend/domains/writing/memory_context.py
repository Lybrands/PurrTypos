"""Budgeted long-term memory context assembled through a repository port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from purra.contracts import ModelRequest
from purra.context_budget import estimate_text_tokens
from purra.ports import CancellationSignal
from domains.writing.repositories import (
    MemoryItem,
    MemoryLink,
    MemoryRecallRepository,
)
from domains.writing.memory_reranking import (
    MemoryCandidateCard,
    MemoryCandidateReranker,
)


GROUP_LABELS = {
    "canon": "必须遵循的设定",
    "plot": "已发生的剧情事实",
    "character": "人物当前状态",
    "foreshadowing": "待铺垫/待回收伏笔",
    "style": "风格约束",
    "world": "必须遵循的设定",
    "summary": "已发生的剧情事实",
}


@dataclass(frozen=True, slots=True)
class MemoryContextRequest:
    book_id: str | None
    user_prompt: str = ""
    token_budget: int = 6_000
    recall_limit: int = 16
    candidate_limit: int = 40
    selected_memory_item_ids: tuple[Any, ...] = ()
    selected_foreshadowing_memory_item_ids: tuple[Any, ...] = ()
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
    """Compact record of one memory fact injected into this run."""

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


@dataclass(slots=True)
class MemoryContextBlock:
    text: str
    included_ids: list[int] = field(default_factory=list)
    deferred_ids: list[int] = field(default_factory=list)
    suppressed_ids: list[int] = field(default_factory=list)
    token_estimate: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    receipts: tuple[MemoryUsageReceipt, ...] = ()
    selected_fact: "SelectedMemoryContextFact | None" = None

    def with_outer_truncation(self) -> "MemoryContextBlock":
        """Revoke selected-memory completeness after a later envelope cut."""

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


@dataclass(frozen=True, slots=True)
class _SelectedMemoryRequest:
    requested_count: int
    search_tools: tuple[str, ...]


def _selected_memory_request(
    request: MemoryContextRequest,
) -> _SelectedMemoryRequest:
    request_keys: set[tuple[str, str]] = set()
    search_tools: list[str] = []
    for kind, values, tool in (
        ("memory", request.selected_memory_item_ids, "searchMemories"),
        (
            "foreshadowing_memory",
            request.selected_foreshadowing_memory_item_ids,
            "searchMemories",
        ),
        ("spark_idea", request.selected_spark_idea_ids, "searchSparkIdeas"),
        (
            "foreshadowing",
            request.selected_foreshadowing_ids,
            "searchSparkIdeas",
        ),
    ):
        normalized = {
            str(value).strip()
            for value in values
            if str(value).strip()
        }
        if normalized:
            search_tools.append(tool)
            request_keys.update((kind, value) for value in normalized)
    return _SelectedMemoryRequest(
        requested_count=len(request_keys),
        search_tools=tuple(dict.fromkeys(search_tools)),
    )


def unavailable_memory_context(
    request: MemoryContextRequest,
) -> MemoryContextBlock:
    """Build a fail-closed manifest when selected retrieval cannot run."""

    selection = _selected_memory_request(request)
    return MemoryContextBlock(
        "",
        diagnostics=_diagnostics(),
        selected_fact=(
            SelectedMemoryContextFact(
                requested_count=selection.requested_count,
                complete_count=0,
                truncated_count=0,
                not_injected_count=selection.requested_count,
                search_tools=selection.search_tools,
                locator_available_to_execution=False,
            )
            if selection.requested_count
            else None
        ),
    )


class WritingMemoryContextBuilder:
    def __init__(
        self,
        repository: MemoryRecallRepository,
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
    ) -> MemoryContextBlock:
        selection = _selected_memory_request(request)
        book_id = str(request.book_id or "").strip()
        if not book_id:
            return unavailable_memory_context(request)
        budget = max(0, int(request.token_budget))
        if budget <= 0:
            return unavailable_memory_context(request)

        forced = await self._resolve_forced_items(request, book_id)
        recalled_candidates = await self._repository.search(
            book_id,
            request.user_prompt,
            limit=max(
                int(request.recall_limit),
                min(160, max(1, int(request.candidate_limit))),
            ),
        )
        recalled = recalled_candidates[:max(1, int(request.recall_limit))]
        rerank_diagnostics: dict[str, Any] = {
            "candidateCount": len(recalled_candidates),
            "rerankerUsed": False,
            "rerankerStatus": "not_configured",
        }
        if (
            self._reranker is not None
            and model_request is not None
            and recalled_candidates
        ):
            try:
                reranked = await self._reranker.rerank(
                    query=request.user_prompt,
                    candidates=tuple(
                        _semantic_candidate_card(item)
                        for item in recalled_candidates
                    ),
                    story_kinds=request.story_kinds,
                    planner_story_kinds=request.planner_story_kinds,
                    entity_refs=request.entity_refs,
                    chapter_ids=request.chapter_ids,
                    max_selected=max(1, int(request.recall_limit)),
                    model_request=model_request,
                    signal=signal,
                )
                item_by_id = {
                    f"semantic:{item.id}": item
                    for item in recalled_candidates
                }
                selected_keys = tuple(
                    decision.record_id
                    for decision in reranked.decisions
                    if decision.record_id in item_by_id
                )
                selected_ids = tuple(
                    item_by_id[key].id for key in selected_keys
                )
                refreshed = await self._repository.get_by_ids(
                    book_id,
                    selected_ids,
                    statuses=("active",),
                )
                refreshed_by_id = {item.id: item for item in refreshed}
                invalidated_ids: list[int] = []
                validated: list[MemoryItem] = []
                for key in selected_keys:
                    candidate = item_by_id[key]
                    current = refreshed_by_id.get(candidate.id)
                    if current is None or current != candidate:
                        invalidated_ids.append(candidate.id)
                        continue
                    validated.append(current)
                recalled = tuple(validated)
                rerank_diagnostics = {
                    "candidateCount": len(recalled_candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "completed",
                    "rerankerModel": reranked.model,
                    "rerankerBatchCount": reranked.batch_count,
                    "rerankerSelectedCount": len(recalled),
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
                        for decision in reranked.decisions
                    ],
                }
            except Exception as error:
                recalled = recalled_candidates[:max(1, int(request.recall_limit))]
                rerank_diagnostics = {
                    "candidateCount": len(recalled_candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "fallback",
                    "rerankerError": type(error).__name__,
                }

        by_id: dict[int, MemoryItem] = {}
        forced_ids = {item.id for item in forced}
        for item in forced:
            by_id[item.id] = item
        for item in recalled:
            by_id.setdefault(item.id, item)

        forced_ordered = sorted(
            (item for item in forced if item.id in by_id),
            key=lambda item: (-item.pinned, -item.importance, item.id),
        )
        recalled_ordered = [
            item for item in recalled
            if item.id in by_id and item.id not in forced_ids
        ]
        ordered = [*forced_ordered, *recalled_ordered]

        authoritative = {
            _fact_fingerprint(value)
            for value in request.authoritative_fingerprints
            if _fact_fingerprint(value)
        }
        authority_suppressed_ids = {
            item.id
            for item in ordered
            if item.id not in forced_ids
            and (
                _fact_fingerprint(item.content or item.summary) in authoritative
                or _matches_authoritative_signature(
                    item.content or item.summary,
                    request.authoritative_signatures,
                )
            )
        }
        if authority_suppressed_ids:
            ordered = [
                item for item in ordered
                if item.id not in authority_suppressed_ids
            ]

        links = await self._repository.get_links(book_id, tuple(by_id))
        related_ids = {
            endpoint
            for link in links
            for endpoint in (link.from_memory_id, link.to_memory_id)
            if endpoint not in by_id
        }
        relation_expanded: list[MemoryItem] = []
        if related_ids:
            related_rows = await self._repository.get_by_ids(
                book_id,
                sorted(related_ids),
                statuses=("active",),
            )
            related_by_id = {item.id: item for item in related_rows}
            for link in links:
                for endpoint in (link.from_memory_id, link.to_memory_id):
                    item = related_by_id.get(endpoint)
                    if item is not None and item.id not in by_id:
                        by_id[item.id] = item
                        relation_expanded.append(item)
        ordered.extend(relation_expanded)
        ordered, suppressed_ids, warnings, conflict_count = _apply_relation_policy(
            ordered,
            links,
            forced_ids,
        )
        suppressed_ids.update(authority_suppressed_ids)

        text, included_ids, deferred_ids, truncated_ids = _format_budgeted(
            ordered,
            forced_ids,
            budget,
            relation_warnings=warnings,
        )
        if included_ids:
            await self._repository.mark_used(book_id, included_ids)

        diagnostics = _diagnostics(
            forced=len(forced_ids),
            recalled=max(0, len([item for item in recalled if item.id not in forced_ids])),
            included=len(included_ids),
            deferred=len(deferred_ids),
            suppressed=len(suppressed_ids),
            conflicts=conflict_count,
            relation_expanded=len(relation_expanded),
            character_count=len(text),
        )
        diagnostics.update(rerank_diagnostics)
        selected_included_ids = forced_ids.intersection(included_ids)
        selected_truncated_ids = selected_included_ids.intersection(truncated_ids)
        selected_truncated_count = min(
            selection.requested_count,
            len(selected_truncated_ids),
        )
        selected_complete_count = min(
            selection.requested_count - selected_truncated_count,
            len(selected_included_ids - selected_truncated_ids),
        )
        selected_not_injected_count = max(
            0,
            selection.requested_count
            - selected_complete_count
            - selected_truncated_count,
        )
        return MemoryContextBlock(
            text=text,
            included_ids=included_ids,
            deferred_ids=deferred_ids,
            suppressed_ids=sorted(suppressed_ids),
            token_estimate=estimate_tokens(text),
            diagnostics=diagnostics,
            receipts=tuple(
                MemoryUsageReceipt(
                    evidence_id=f"semantic:{item_id}",
                    source="semantic",
                    item_id=str(item_id),
                )
                for item_id in included_ids
            ),
            selected_fact=(
                SelectedMemoryContextFact(
                    requested_count=selection.requested_count,
                    complete_count=selected_complete_count,
                    truncated_count=selected_truncated_count,
                    not_injected_count=selected_not_injected_count,
                    search_tools=selection.search_tools,
                    locator_available_to_execution=(
                        selection.requested_count > 0
                        and selected_complete_count == selection.requested_count
                    ),
                )
                if selection.requested_count
                else None
            ),
        )

    async def _resolve_forced_items(
        self,
        request: MemoryContextRequest,
        book_id: str,
    ) -> tuple[MemoryItem, ...]:
        item_ids = (
            *request.selected_memory_item_ids,
            *request.selected_foreshadowing_memory_item_ids,
        )
        rows = list(await self._repository.get_by_ids(
            book_id,
            item_ids,
            statuses=("active", "pending"),
        ))
        rows.extend(await self._repository.get_by_source_ids(
            book_id,
            "spark_idea",
            request.selected_spark_idea_ids,
            statuses=("active", "pending"),
        ))
        rows.extend(await self._repository.get_by_source_ids(
            book_id,
            "foreshadowing",
            request.selected_foreshadowing_ids,
            statuses=("active", "pending"),
        ))
        seen: set[int] = set()
        result: list[MemoryItem] = []
        for item in rows:
            if item.id in seen:
                continue
            seen.add(item.id)
            result.append(item)
        return tuple(result)


def _semantic_candidate_card(item: MemoryItem) -> MemoryCandidateCard:
    return MemoryCandidateCard(
        id=f"semantic:{item.id}",
        source="semantic",
        kind=item.kind,
        fact={
            "content": item.content,
            "summary": item.summary,
            "importance": item.importance,
            "scopeType": item.scope_type,
            "scopeId": item.scope_id,
        },
        subject_id=item.scope_id,
        chapter_id=(
            item.scope_id if item.scope_type == "chapter" else None
        ),
        candidate_channels=("semantic_search",),
    )


def _format_budgeted(
    items: Sequence[MemoryItem],
    forced_ids: set[int],
    budget: int,
    *,
    relation_warnings: Sequence[str] = (),
) -> tuple[str, list[int], list[int], set[int]]:
    if not items:
        return "", [], [], set()

    groups: dict[str, list[str]] = {}
    included_ids: list[int] = []
    included_rows: list[tuple[int, str, str]] = []
    deferred_ids: list[int] = []
    truncated_ids: set[int] = set()
    footer_reserve = min(160, max(48, budget // 5))
    selection_budget = max(0, budget - footer_reserve)
    fitted_warnings = _fit_relation_warnings(relation_warnings, selection_budget)

    for item in items:
        rendered = _render_item(item)
        label = _label_for_item(item)
        if item.id in forced_ids and item.kind != "foreshadowing":
            label = "必须遵循的设定"
        candidate_groups = {key: list(rows) for key, rows in groups.items()}
        candidate_groups.setdefault(label, []).append(rendered)
        candidate_text = _compose_memory_text(candidate_groups, fitted_warnings, 0)
        if len(candidate_text) <= selection_budget:
            groups = candidate_groups
            included_ids.append(item.id)
            included_rows.append((item.id, label, rendered))
            continue

        if not included_ids:
            prefix = f"- [id:{item.id}|{item.kind}]"
            overhead = len(_compose_memory_text({label: [prefix]}, fitted_warnings, 0)) - len(prefix)
            available = selection_budget - overhead - 2
            if available > len(prefix) + 8:
                shortened = rendered[: max(len(prefix) + 4, available - 1)] + "…"
                truncated_groups = {label: [shortened]}
                if len(_compose_memory_text(truncated_groups, fitted_warnings, 0)) <= selection_budget:
                    groups = truncated_groups
                    included_ids.append(item.id)
                    included_rows.append((item.id, label, shortened))
                    truncated_ids.add(item.id)
                    continue
        deferred_ids.append(item.id)

    text = _compose_memory_text(groups, fitted_warnings, len(deferred_ids))
    while len(text) > budget and included_rows:
        item_id, label, rendered = included_rows.pop()
        rows = groups.get(label) or []
        if rendered in rows:
            rows.remove(rendered)
        if not rows:
            groups.pop(label, None)
        included_ids.remove(item_id)
        truncated_ids.discard(item_id)
        deferred_ids.append(item_id)
        text = _compose_memory_text(groups, fitted_warnings, len(deferred_ids))
    if len(text) > budget:
        text = text[:budget]
    return text, included_ids, deferred_ids, truncated_ids


def _compose_memory_text(
    groups: dict[str, list[str]],
    relation_warnings: Sequence[str],
    deferred_count: int,
) -> str:
    lines = ["【长期记忆 — 已由宿主按当前写作意图召回】"]
    if relation_warnings:
        lines.append("\n## 记忆关系警告")
        lines.append("以下关系已由宿主检测；不要把存在冲突的内容同时视为确定事实。")
        lines.extend(relation_warnings)
    for label in (
        "必须遵循的设定",
        "已发生的剧情事实",
        "人物当前状态",
        "待铺垫/待回收伏笔",
        "大纲计划，非既成事实",
        "风格约束",
    ):
        rows = groups.get(label)
        if rows:
            lines.append(f"\n## {label}")
            lines.extend(rows)
    if deferred_count:
        lines.append(
            f"\n另有 {deferred_count} 条相关长期记忆因预算限制未注入，"
            "可用 searchMemories 继续查询。"
        )
    return "\n".join(lines)


def _fit_relation_warnings(warnings: Sequence[str], budget: int) -> list[str]:
    if not warnings or budget <= 0:
        return []
    warning_budget = min(1200, max(80, budget // 4))
    result: list[str] = []
    used = 0
    for warning in warnings[:8]:
        row = str(warning).strip()
        if not row:
            continue
        remaining = warning_budget - used
        if remaining <= 8:
            break
        if len(row) > remaining:
            row = row[: remaining - 1] + "…"
        result.append(row)
        used += len(row) + 1
    return result


def _render_item(item: MemoryItem) -> str:
    body = item.content.strip() or item.summary.strip()
    return f"- [id:{item.id}|{item.kind}] {body}"


def _label_for_item(item: MemoryItem) -> str:
    if item.scope_type == "outline":
        return "大纲计划，非既成事实"
    return GROUP_LABELS.get(item.kind, "已发生的剧情事实")


def _apply_relation_policy(
    ordered: Sequence[MemoryItem],
    links: Sequence[MemoryLink],
    forced_ids: set[int],
) -> tuple[list[MemoryItem], set[int], list[str], int]:
    candidate_ids = {item.id for item in ordered}
    suppressed: set[int] = set()
    warnings: list[str] = []
    conflict_count = 0
    for link in links:
        from_id = link.from_memory_id
        to_id = link.to_memory_id
        if from_id not in candidate_ids or to_id not in candidate_ids:
            continue
        if link.relation == "supersedes":
            if to_id in forced_ids and from_id not in forced_ids:
                suppressed.add(from_id)
                warnings.append(
                    f"- 用户选择的 [id:{to_id}] 已被 [id:{from_id}] 标记为取代；"
                    "本轮按用户选择保留旧版本。"
                )
            elif to_id not in forced_ids:
                suppressed.add(to_id)
            elif from_id in forced_ids:
                warnings.append(
                    f"- [id:{from_id}] 标记为取代 [id:{to_id}]，但两条均由用户选择。"
                )
            continue
        if link.relation == "contradicts":
            conflict_count += 1
            from_forced = from_id in forced_ids
            to_forced = to_id in forced_ids
            if from_forced != to_forced:
                automatic_id = to_id if from_forced else from_id
                selected_id = from_id if from_forced else to_id
                suppressed.add(automatic_id)
                warning = (
                    f"- 自动召回的 [id:{automatic_id}] 与用户选择的 [id:{selected_id}] 冲突，"
                    "已优先保留用户选择。"
                )
            else:
                warning = f"- [id:{from_id}] 与 [id:{to_id}] 存在未解决冲突。"
            if link.note.strip():
                warning += f" 说明：{link.note.strip()}"
            warnings.append(warning)
    return [item for item in ordered if item.id not in suppressed], suppressed, warnings, conflict_count


def estimate_tokens(text: str) -> int:
    return estimate_text_tokens(text)


def _fact_fingerprint(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def _matches_authoritative_signature(
    content: str,
    signatures: Sequence[Sequence[str]],
) -> bool:
    normalized = _fact_fingerprint(content)
    if not normalized:
        return False
    for signature in signatures:
        values = tuple(dict.fromkeys(
            _fact_fingerprint(value)
            for value in signature
            if len(_fact_fingerprint(value)) >= 2
        ))
        matched = [value for value in values if value in normalized]
        if len(matched) >= 2 or any(len(value) >= 8 for value in matched):
            return True
    return False


def _diagnostics(
    *,
    forced: int = 0,
    recalled: int = 0,
    included: int = 0,
    deferred: int = 0,
    suppressed: int = 0,
    conflicts: int = 0,
    relation_expanded: int = 0,
    character_count: int = 0,
) -> dict[str, int]:
    return {
        "forced": forced,
        "recalled": recalled,
        "included": included,
        "deferred": deferred,
        "suppressed": suppressed,
        "conflicts": conflicts,
        "relationExpanded": relation_expanded,
        "characterCount": character_count,
    }
