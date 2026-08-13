"""Writing-specific context assembly behind PurrA's ContextProvider port."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol

from purra.context_budget import estimate_json_tokens
from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudget,
    ContextBudgetClaim,
    ContextBundle,
    MessageRole,
    TaskContextRequest,
)
from purra.json_values import thaw_json_mapping
from purra.ports import CancellationSignal
from domains.writing.associated_context import (
    AssociatedContextResult,
    ChapterContextFact,
    OutlineContextFact,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    unavailable_memory_context,
)
from domains.writing.unified_memory_context import MemoryContextPack
from domains.writing.prompts import (
    build_writing_agent_policy,
    build_writing_evidence_policy,
    build_writing_session_binding,
    frame_untrusted_writing_context,
)
from domains.writing.response import writing_response_contract_for_request


WRITING_RETRIEVAL_CONTEXT = "writing_retrieval"
WRITING_BINDING_CONTEXT = "writing_session_binding"
WRITING_EVIDENCE_POLICY_CONTEXT = "writing_evidence_policy"
WRITING_AGENT_POLICY_CONTEXT = "writing_agent_policy"


@dataclass(frozen=True, slots=True)
class _DesiredBudgets:
    memory: int
    associated: int

    @property
    def total(self) -> int:
        return self.memory + self.associated


class WritingContextSource(Protocol):
    """Infrastructure-facing source for writing retrieval content."""

    async def build_memory(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> str | MemoryContextBlock | MemoryContextPack: ...

    async def build_associated(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> str | AssociatedContextResult: ...


class EmptyWritingContextSource:
    async def build_memory(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> str:
        del context, request, token_budget
        return ""

    async def build_associated(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> str:
        del context, request, token_budget
        return ""


class WritingContextProvider:
    """Assemble chapters, outlines, memories and the session binding prompt."""

    def __init__(self, source: WritingContextSource | None = None):
        self._source = source or EmptyWritingContextSource()

    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del signal
        return await self._build_context(request, budget)

    async def build_planning_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        """Build only the host facts needed before the LLM Planner runs.

        Evidence bodies and semantic memory search are deferred until a
        normalized TaskSpec exists. Explicit selections are represented by a
        fail-closed manifest rather than being loaded and then discarded.
        """

        del signal
        context = WritingDomainContext.from_core_context(request.domain_context)
        empty_memory = unavailable_memory_context(MemoryContextRequest(
            book_id=context.book_id,
            user_prompt="",
            token_budget=0,
            selected_spark_idea_ids=context.selected_memory_ids,
            selected_foreshadowing_ids=context.selected_foreshadowing_ids,
        ))
        associated = _planning_associated_manifest(context)
        policy = build_writing_agent_policy()
        host_facts = build_host_planning_facts(
            current_chapter_bound=bool(
                str(context.chapter_id or "").strip()
            ),
            memory=empty_memory,
            associated=associated,
            include_evidence_read_rules=False,
        )
        existing_rules = host_facts.get("planningRules")
        host_facts["planningRules"] = [
            policy,
            *(existing_rules if isinstance(existing_rules, list) else []),
        ]
        return ContextBundle(
            blocks=(ContextBlock(
                name=WRITING_AGENT_POLICY_CONTEXT,
                content=policy,
                token_count=estimate_json_tokens(policy),
                untrusted=False,
            ),),
            diagnostics={
                "memoryTokens": 0,
                "associatedTokens": 0,
                "retrievalTokens": 0,
                "retrievalAllocation": budget.allocation_for(
                    WRITING_RETRIEVAL_CONTEXT
                ),
                "planningContextMode": (
                    "explicit_evidence_manifest"
                    if _has_explicit_evidence(context)
                    else "lightweight_manifest"
                ),
                "hostPlanningFacts": host_facts,
            },
        )

    async def build_task_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        """Perform formal recall after planning using semantic task intent."""

        context = WritingDomainContext.from_core_context(request.domain_context)
        retrieval_required = bool(
            WRITING_RETRIEVAL_CONTEXT in task.required_context_blocks
            or task.include_response_context
            or _has_explicit_evidence(context)
        )
        query = build_task_recall_query(task)
        return await self._build_context(
            request,
            budget,
            recall_query=query,
            retrieval_required=retrieval_required,
            task=task,
            signal=signal,
        )

    async def _build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        *,
        recall_query: str | None = None,
        retrieval_required: bool = True,
        task: TaskContextRequest | None = None,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        context = WritingDomainContext.from_core_context(request.domain_context)
        allocated = budget.allocation_for(WRITING_RETRIEVAL_CONTEXT)
        desired = (
            _desired_budgets(request, context)
            if retrieval_required
            else _DesiredBudgets(memory=0, associated=0)
        )
        memory_budget, associated_budget = _split_allocation(allocated, desired)

        memory_request = MemoryContextRequest(
            book_id=context.book_id,
            user_prompt=request.latest_user_text(),
            token_budget=memory_budget,
            selected_spark_idea_ids=context.selected_memory_ids,
            selected_foreshadowing_ids=context.selected_foreshadowing_ids,
        )
        memory_value: str | MemoryContextBlock | MemoryContextPack = ""
        if context.book_id and memory_budget > 0:
            task_builder = getattr(self._source, "build_memory_for_task", None)
            query_builder = getattr(self._source, "build_memory_for_query", None)
            if (
                recall_query is not None
                and task is not None
                and callable(task_builder)
            ):
                memory_value = await task_builder(
                    context,
                    request,
                    memory_budget,
                    recall_query,
                    task,
                    signal=signal,
                )
            elif recall_query is not None and callable(query_builder):
                memory_value = await query_builder(
                    context,
                    request,
                    memory_budget,
                    recall_query,
                )
            else:
                memory_request_value = (
                    _request_with_latest_user_text(request, recall_query)
                    if recall_query is not None
                    else request
                )
                memory_value = await self._source.build_memory(
                    context,
                    memory_request_value,
                    memory_budget,
                )
        if isinstance(memory_value, (MemoryContextBlock, MemoryContextPack)):
            memory_result = memory_value
        else:
            memory_result = unavailable_memory_context(memory_request)
            memory_result.text = str(memory_value or "")
            memory_result.token_estimate = estimate_json_tokens(memory_result.text)
        memory_block = memory_result.text

        memory_tokens = estimate_json_tokens(memory_block) if memory_block else 0
        unused_memory = max(0, memory_budget - memory_tokens)
        associated_value = (
            await self._source.build_associated(
                context,
                request,
                associated_budget + unused_memory,
            )
            if (
                context.associated_chapter_ids
                or context.associated_outline_ids
            )
            else ""
        )
        associated_result = (
            associated_value
            if isinstance(associated_value, AssociatedContextResult)
            else AssociatedContextResult(text=str(associated_value or ""))
        )
        associated_block = associated_result.text
        retrieval = frame_untrusted_writing_context({
            "memory_context_pack": memory_block,
            "associated_chapters_and_outlines": associated_block,
        })
        fitted_retrieval = _fit_json_budget(retrieval, allocated)
        if fitted_retrieval != retrieval:
            memory_result = memory_result.with_outer_truncation()
            associated_result = associated_result.with_outer_truncation()
        retrieval = fitted_retrieval

        blocks: list[ContextBlock] = []
        if retrieval:
            blocks.append(ContextBlock(
                name=WRITING_RETRIEVAL_CONTEXT,
                content=retrieval,
                token_count=estimate_json_tokens(retrieval),
                untrusted=True,
                host_metadata={
                    "writing_outline_sources": [
                        {"outlineId": outline_id, "text": text}
                        for outline_id, text in (
                            associated_result.outline_source_records
                        )
                    ],
                    "memory_context_receipts": [
                        receipt.to_mapping()
                        for receipt in memory_result.receipts
                    ],
                },
            ))

        binding = build_writing_session_binding(
            context,
            tools_enabled=bool(request.tools_enabled and context.book_id),
        )
        if binding:
            blocks.append(ContextBlock(
                name=WRITING_BINDING_CONTEXT,
                content=binding,
                token_count=estimate_json_tokens(binding),
                untrusted=False,
            ))

        response_contract = writing_response_contract_for_request(request)
        evidence_policy = build_writing_evidence_policy(
            exact_review_item_count=response_contract.exact_review_item_count,
            atomic_continuity_items=response_contract.atomic_continuity_items,
            summary_max_characters=response_contract.summary_max_characters,
        )
        blocks.append(ContextBlock(
            name=WRITING_EVIDENCE_POLICY_CONTEXT,
            content=evidence_policy,
            token_count=estimate_json_tokens(evidence_policy),
            untrusted=False,
        ))

        return ContextBundle(
            blocks=tuple(blocks),
            diagnostics={
                "memoryTokens": memory_tokens,
                "associatedTokens": (
                    estimate_json_tokens(associated_block) if associated_block else 0
                ),
                "retrievalTokens": estimate_json_tokens(retrieval) if retrieval else 0,
                "retrievalAllocation": allocated,
                "recallQuerySource": (
                    "taskSpec" if recall_query is not None else "latestUserText"
                ),
                "recallQueryCharacters": len(recall_query or ""),
                "requiredEvidenceKinds": (
                    list(task.evidence_kinds) if task is not None else []
                ),
                "memoryContextReceipt": {
                    "receipts": [
                        receipt.to_mapping()
                        for receipt in memory_result.receipts
                    ],
                    "diagnostics": dict(memory_result.diagnostics),
                },
                "hostPlanningFacts": build_host_planning_facts(
                    current_chapter_bound=bool(
                        str(context.chapter_id or "").strip()
                    ),
                    memory=memory_result,
                    associated=associated_result,
                ),
            },
        )


def build_task_recall_query(task: TaskContextRequest) -> str:
    """Compile a bounded semantic query from TaskSpec and contract evidence."""

    brief = task.task_spec
    rows = [f"任务目标: {brief.goal}"]
    if brief.operation:
        rows.append(f"操作: {brief.operation}")
    if task.required_context_blocks:
        rows.append(
            "所需上下文块: " + ", ".join(task.required_context_blocks)
        )
    if task.evidence_kinds:
        rows.append("所需证据类型: " + ", ".join(task.evidence_kinds))
    if brief.target:
        rows.append(
            "目标: "
            + json.dumps(
                thaw_json_mapping(brief.target),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if brief.instruction:
        rows.append(f"具体要求: {brief.instruction}")
    if brief.constraints:
        rows.append("约束: " + "；".join(brief.constraints))
    if brief.preserve:
        rows.append("必须保留: " + "；".join(brief.preserve))
    if brief.deliverable:
        rows.append(f"交付物: {brief.deliverable}")
    return "\n".join(rows)[:6_000]


def _planning_associated_manifest(
    context: WritingDomainContext,
) -> AssociatedContextResult:
    return AssociatedContextResult(
        chapter_facts=tuple(
            ChapterContextFact(chapter_id, "not_injected")
            for chapter_id in _unique_identifiers(context.associated_chapter_ids)
        ),
        outline_facts=tuple(
            OutlineContextFact(outline_id, "not_injected")
            for outline_id in _unique_identifiers(context.associated_outline_ids)
        ),
    )


def _unique_identifiers(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _has_explicit_evidence(context: WritingDomainContext) -> bool:
    return bool(
        context.selected_memory_ids
        or context.selected_foreshadowing_ids
        or context.associated_chapter_ids
        or context.associated_outline_ids
    )


def _request_with_latest_user_text(
    request: AgentRunRequest,
    text: str,
) -> AgentRunRequest:
    """Compatibility adapter for sources without query-aware retrieval."""

    messages = list(request.messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is MessageRole.USER:
            messages[index] = replace(messages[index], content=str(text or ""))
            return replace(request, messages=tuple(messages))
    return request


def writing_context_claims(request: AgentRunRequest) -> tuple[ContextBudgetClaim, ...]:
    """Describe writing retrieval demand without exposing it to PurrA."""

    context = WritingDomainContext.from_core_context(request.domain_context)
    desired = _desired_budgets(request, context)
    if desired.total <= 0:
        return ()
    return (ContextBudgetClaim(WRITING_RETRIEVAL_CONTEXT, desired.total),)


def _desired_budgets(
    request: AgentRunRequest,
    context: WritingDomainContext,
) -> _DesiredBudgets:
    window = int(request.context_window or 200_000)
    memory = (
        min(40_000, max(6_000, window // 25))
        if context.book_id
        else 0
    )
    associated = (
        min(120_000, max(24_000, window // 5))
        if context.associated_chapter_ids or context.associated_outline_ids
        else 0
    )
    return _DesiredBudgets(memory=memory, associated=associated)


def _split_allocation(
    allocated: int,
    desired: _DesiredBudgets,
) -> tuple[int, int]:
    available = max(0, int(allocated))
    if desired.total <= 0 or available <= 0:
        return 0, 0
    if desired.total <= available:
        return desired.memory, desired.associated
    memory = desired.memory * available // desired.total
    return memory, max(0, available - memory)


def _fit_json_budget(text: str, token_budget: int) -> str:
    budget = max(0, int(token_budget))
    if not text or budget <= 0:
        return ""
    if estimate_json_tokens(text) <= budget:
        return text
    marker = "\n…（写作上下文已按统一 token 预算截断）"
    if estimate_json_tokens(marker) >= budget:
        marker = ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_json_tokens(text[:middle] + marker) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + marker


def build_host_planning_facts(
    *,
    current_chapter_bound: bool,
    memory: MemoryContextBlock | MemoryContextPack | None,
    associated: AssociatedContextResult,
    include_evidence_read_rules: bool = True,
) -> dict[str, object]:
    """Return the ID-free planning manifest consumed by PurrA."""

    facts: dict[str, object] = {}
    if current_chapter_bound:
        facts["currentChapter"] = {
            "bound": True,
            "singleChapterToolsMayOmitChapterId": True,
        }
    selected_memory_value: dict[str, object] | None = None
    if memory is not None and memory.selected_fact is not None:
        selected_memory_value = memory.selected_fact.to_planning_value()
        facts["selectedMemories"] = selected_memory_value
    chapter_values: list[dict[str, str | int | bool]] = []
    if associated.chapter_facts:
        chapter_values = [
            fact.to_planning_value(ordinal=index)
            for index, fact in enumerate(associated.chapter_facts, start=1)
        ]
        facts["associatedChapters"] = {
            "selectedCount": len(chapter_values),
            "completeCount": sum(
                value["status"] == "complete" for value in chapter_values
            ),
            "items": chapter_values,
        }
    outline_values: list[dict[str, str | int | bool]] = []
    if associated.outline_facts:
        outline_values = [
            fact.to_planning_value(ordinal=index)
            for index, fact in enumerate(associated.outline_facts, start=1)
        ]
        facts["associatedOutlines"] = {
            "selectedCount": len(outline_values),
            "completeCount": sum(
                value["status"] == "complete" for value in outline_values
            ),
            "items": outline_values,
        }
    selected_evidence_statuses = [
        *(
            [str(selected_memory_value["status"])]
            if selected_memory_value is not None
            else []
        ),
        *(str(value["status"]) for value in chapter_values),
        *(str(value["status"]) for value in outline_values),
    ]
    selected_evidence_complete = bool(selected_evidence_statuses) and all(
        status == "complete" for status in selected_evidence_statuses
    )
    rules: list[str] = []
    if "currentChapter" in facts:
        rules.append(
            "For the bound current chapter, getChapterContent may omit chapterId; "
            "do not add listWritingChapters solely to locate that chapter."
        )
    if selected_memory_value is not None and include_evidence_read_rules:
        rules.append(
            "User-selected memory material with status complete is already "
            "fully injected; do not plan searchMemories or searchSparkIdeas "
            "solely to reread it."
        )
        if selected_memory_value["status"] != "complete":
            search_tools = ", ".join(
                str(value)
                for value in selected_memory_value["searchTools"]
            )
            rules.append(
                "Only truncated or not_injected user-selected memory material "
                "may require another search"
                + (f" via {search_tools}." if search_tools else ".")
            )
    if "associatedChapters" in facts and include_evidence_read_rules:
        rules.append(
            "A complete associated chapter is already fully injected; do not "
            "plan getChapterContent solely to reread it."
        )
        if any(
            value["status"] != "complete"
            and value["locatorAvailableToExecution"]
            for value in chapter_values
        ):
            rules.append(
                "For a truncated or not_injected associated chapter whose "
                "locatorAvailableToExecution is true, plan getChapterContent "
                "directly; do not add listWritingChapters."
            )
        if any(
            value["status"] != "complete"
            and not value["locatorAvailableToExecution"]
            for value in chapter_values
        ):
            rules.append(
                "For a truncated or not_injected associated chapter whose "
                "locatorAvailableToExecution is false, listWritingChapters may "
                "be used only to discover its locator before getChapterContent."
            )
    if "associatedOutlines" in facts and include_evidence_read_rules:
        rules.append(
            "A complete associated outline is already fully injected; do not "
            "plan any tool step to reread it."
        )
        if any(
            value["status"] != "complete"
            and value["locatorAvailableToExecution"]
            for value in outline_values
        ):
            rules.append(
                "For a truncated or not_injected associated outline whose "
                "locatorAvailableToExecution is true, plan queryOutline directly; "
                "do not add listOutlines."
            )
        if any(
            value["status"] != "complete"
            and not value["locatorAvailableToExecution"]
            for value in outline_values
        ):
            rules.append(
                "For a truncated or not_injected associated outline whose "
                "locatorAvailableToExecution is false, listOutlines may be used "
                "only to discover its locator before queryOutline."
            )
        rules.append(
            "getGlobalOutline is book-wide and never substitutes for an "
            "associated outline."
        )
    if selected_evidence_complete and include_evidence_read_rules:
        rules.append(
            "The complete user-selected evidence set is already injected. "
            "When the request is explicitly limited to analyzing or citing "
            "that selected evidence, do not broaden scope with dashboards, "
            "character or setting lists, searches, or unrelated discovery "
            "tools unless the user explicitly asks to broaden the evidence set."
        )
    if selected_evidence_statuses and not include_evidence_read_rules:
        rules.append(
            "Host-bound explicit evidence bodies are loaded after TaskSpec "
            "planning; do not plan tool steps solely to read those exact "
            "selections."
        )
    if rules:
        facts["planningRules"] = rules
    return facts
