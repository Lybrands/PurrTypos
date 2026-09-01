"""Writing-domain semantic context backed by the PurrA memory component."""

from __future__ import annotations

import json

from purra.context_budget import estimate_json_tokens
from purra.retrieval import RetrievalRequest

from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    MemoryUsageReceipt,
    SelectedMemoryContextFact,
)
from domains.writing.memory_reranking import MemoryCandidateCard


class ComponentWritingMemoryContextBuilder:
    def __init__(
        self,
        memory,
        sources,
        *,
        run_id: str,
        reranker=None,
    ) -> None:
        self._memory = memory
        self._sources = sources
        self._run_id = str(run_id or "").strip()
        self._reranker = reranker

    async def build(self, request, *, model_request=None, signal=None):
        book_id = str(request.book_id or "").strip()
        if not book_id or request.token_budget <= 0:
            return MemoryContextBlock(text="", diagnostics={"unavailable": not book_id})
        if not self._run_id:
            raise ValueError("memory Run id is required")
        recall_limit = min(32, max(1, int(request.recall_limit)))
        candidate_limit = max(
            recall_limit,
            min(32, max(1, int(request.candidate_limit))),
        )
        hits = await self._memory.retrieve(
            book_id=book_id,
            request=RetrievalRequest(
                query=request.user_prompt,
                limit=candidate_limit,
                run_id=self._run_id,
            ),
            signal=signal,
        ) if request.user_prompt.strip() else ()
        suppressed: list[str] = []
        authority = set(request.authoritative_fingerprints)
        candidates = []
        for hit in hits:
            if _normalize(hit.content) in authority:
                suppressed.append(hit.id)
                continue
            candidates.append(hit)

        reranker_diagnostics = {
            "candidateCount": len(candidates),
            "rerankerUsed": False,
            "rerankerStatus": "not_configured",
        }
        selected_hits = tuple(candidates[:recall_limit])
        if self._reranker is not None and model_request is not None and candidates:
            try:
                reranked = await self._reranker.rerank(
                    query=request.user_prompt,
                    candidates=tuple(_candidate_card(hit) for hit in candidates),
                    story_kinds=request.story_kinds,
                    planner_story_kinds=request.planner_story_kinds,
                    entity_refs=request.entity_refs,
                    chapter_ids=request.chapter_ids,
                    max_selected=recall_limit,
                    model_request=model_request,
                    signal=signal,
                )
                by_id = {hit.id: hit for hit in candidates}
                selected_hits = tuple(
                    by_id[decision.record_id]
                    for decision in reranked.decisions
                    if decision.record_id in by_id
                )
                reranker_diagnostics = {
                    "candidateCount": len(candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "completed",
                    "rerankerModel": reranked.model,
                    "rerankerBatchCount": reranked.batch_count,
                }
            except Exception as error:
                selected_hits = ()
                reranker_diagnostics = {
                    "candidateCount": len(candidates),
                    "rerankerUsed": True,
                    "rerankerStatus": "failed",
                    "rerankerError": type(error).__name__,
                }

        selected_ids = tuple(
            str(value) for value in (
                *request.selected_memory_item_ids,
            )
        )
        ordered_ids = tuple(dict.fromkeys((
            *selected_ids,
            *(hit.id for hit in selected_hits),
        )))
        semantic = await self._memory.build_context(
            book_id=book_id,
            operation_key=f"run:{self._run_id}:semantic-context",
            query="",
            selected_ids=ordered_ids,
            token_budget=request.token_budget,
            recall_limit=recall_limit,
            signal=signal,
        )
        text, included, deferred = await assemble_selected_business_sources(
            self._sources,
            book_id=book_id,
            selected_spark_ids=request.selected_spark_idea_ids,
            selected_foreshadowing_ids=request.selected_foreshadowing_ids,
            sections=(semantic.block.content,) if semantic.block is not None else (),
            included=semantic.included,
            deferred=semantic.deferred,
            token_budget=request.token_budget,
        )
        requested_sources = {
            *(f"spark:{str(value)}" for value in request.selected_spark_idea_ids),
            *(f"foreshadowing:{str(value)}" for value in request.selected_foreshadowing_ids),
        }
        requested_keys = {*selected_ids, *requested_sources}
        requested_count = len(requested_keys)
        selected_included = len(set(included).intersection(requested_keys))
        return MemoryContextBlock(
            text=text,
            included_ids=list(included),
            deferred_ids=list(deferred),
            suppressed_ids=suppressed,
            token_estimate=estimate_json_tokens(text),
            diagnostics={
                "recalled": len(hits),
                "included": len(included),
                "deferred": len(deferred),
                "suppressed": len(suppressed),
                **reranker_diagnostics,
            },
            receipts=tuple(
                MemoryUsageReceipt(
                    evidence_id=receipt.evidence_id,
                    source=receipt.source,
                    item_id=receipt.item_id,
                    version=receipt.version,
                    source_id=str(receipt.metadata.get("sourceId") or "") or None,
                )
                for receipt in semantic.receipts
            ),
            selected_fact=(
                SelectedMemoryContextFact(
                    requested_count=requested_count,
                    complete_count=selected_included,
                    truncated_count=0,
                    not_injected_count=max(0, requested_count - selected_included),
                    search_tools=("searchMemories",),
                    locator_available_to_execution=(
                        requested_count > 0 and selected_included == requested_count
                    ),
                )
                if requested_count else None
            ),
        )


async def assemble_selected_business_sources(
    sources,
    *,
    book_id: str,
    selected_spark_ids,
    selected_foreshadowing_ids,
    sections,
    included,
    deferred,
    token_budget: int,
):
    """Append trusted, explicitly selected business sources under one budget."""

    assembled = list(sections)
    included_ids = list(included)
    deferred_ids = list(deferred)
    source_rows: list[tuple[str, dict]] = []
    if selected_spark_ids:
        source_rows.extend(
            ("spark", row)
            for row in await sources.get_spark_ideas_by_ids(
                book_id,
                [str(value) for value in selected_spark_ids],
            )
        )
    if selected_foreshadowing_ids:
        source_rows.extend(
            ("foreshadowing", row)
            for row in await sources.get_foreshadowing_by_ids(
                book_id,
                [str(value) for value in selected_foreshadowing_ids],
            )
        )
    for source_kind, row in source_rows:
        source_id = f"{source_kind}:{row.get('id')}"
        rendered = json.dumps({
            "id": source_id,
            "text": str(row.get("content") or ""),
            "type": row.get("layer") or row.get("type"),
            "status": row.get("status"),
        }, ensure_ascii=False, separators=(",", ":"))
        block = "【显式选择的业务来源】\n" + rendered
        candidate = "\n\n".join((*assembled, block))
        if estimate_json_tokens(candidate) <= token_budget:
            assembled.append(block)
            included_ids.append(source_id)
        else:
            deferred_ids.append(source_id)
    return "\n\n".join(assembled), tuple(included_ids), tuple(deferred_ids)


def _candidate_card(hit) -> MemoryCandidateCard:
    metadata = dict(hit.metadata.get("metadata") or {})
    scope_type = str(metadata.get("scopeType") or "book")
    scope_id = metadata.get("scopeId")
    return MemoryCandidateCard(
        id=hit.id,
        source="semantic",
        kind=str(metadata.get("kind") or "summary"),
        fact={
            "content": hit.content,
            "summary": metadata.get("summary"),
            "importance": metadata.get("importance"),
            "scopeType": scope_type,
            "scopeId": scope_id,
        },
        subject_id=str(scope_id) if scope_id is not None else None,
        chapter_id=(str(scope_id) if scope_type == "chapter" and scope_id is not None else None),
        version=hit.version,
        candidate_channels=("semantic_search",),
    )


def _normalize(value: str) -> str:
    return "".join(str(value or "").casefold().split())


__all__ = [
    "ComponentWritingMemoryContextBuilder",
    "assemble_selected_business_sources",
]
