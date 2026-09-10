"""Writing-owned declaration of generic final-response constraints."""

from __future__ import annotations

import re
from dataclasses import dataclass

from purra.contracts import AgentRunRequest, ResponseConstraints
from purra.ports import ResponseValidator
from domains.writing.continuity_validation import (
    AtomicContinuityGroundingValidator,
)
from domains.writing.continuity_judging import AtomicContinuityJudgePolicy
from domains.writing.contracts import WritingDomainContext
from domains.writing.prompts import (
    derive_exact_review_item_count,
    derive_summary_max_characters,
)
from domains.writing.summary_validation import SummaryResponseValidator
from domains.writing.paragraph_validation import requests_single_prose_paragraph, SingleProseParagraphValidator


_CONTINUITY_DIFFERENCE_CUE = re.compile(
    r"(?:不一致(?:之处)?|差异|矛盾|连续性(?:问题|风险))"
)
_MINIMAL_CHANGE_CUE = re.compile(
    r"(?:(?:最小|局部)\s*(?:修改|改动|调整)(?:建议)?|"
    r"(?:只|仅)(?:需|要)?\s*(?:改|调整|修改))"
)
_OUTLINE_SOURCE_CUE = re.compile(
    r"(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要))"
)
_CHAPTER_SOURCE_CUE = re.compile(
    r"(?:(?:当前|所选|选中)?章节|本章|正文)"
)
_CROSS_SOURCE_COMPARISON_CUE = re.compile(
    r"(?:(?:对照|比对|比较|核对)"
    r"(?=[^。！？!?\n]{0,48}(?:(?:当前|所选|选中)?章节|本章|正文))"
    r"(?=[^。！？!?\n]{0,48}(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要)))"
    r"[^。！？!?\n]{0,48}"
    r"|(?:(?:当前|所选|选中)?章节|本章|正文)"
    r"[^。！？!?\n]{0,20}(?:与|和|同|跟|相比|对比)"
    r"[^。！？!?\n]{0,20}(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要))"
    r"|(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要))"
    r"[^。！？!?\n]{0,20}(?:与|和|同|跟|相比|对比)"
    r"[^。！？!?\n]{0,20}(?:(?:当前|所选|选中)?章节|本章|正文))"
)
_NEGATION_CUE = re.compile(
    r"(?:不要|无需|无须|不必|别|禁止|仅供|只供)"
)
_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;，,\n]+")


def _has_affirmative_cross_source_comparison(text: str) -> bool:
    """Recognize only an explicit, non-negated cross-source comparison.

    This is deliberately a conservative activation gate, not the semantic
    judge itself.  If a clause both mentions negation/reference-only language
    and otherwise resembles a chapter/outline comparison, fail open to the
    ordinary writing response instead of forcing an atomic A/B contract.
    """

    for clause in _CLAUSE_BOUNDARY.split(text):
        if not _CROSS_SOURCE_COMPARISON_CUE.search(clause):
            continue
        if _NEGATION_CUE.search(clause):
            continue
        return True
    return False


@dataclass(frozen=True, slots=True)
class WritingResponseContract:
    """Writing semantics paired with the smaller generic Core constraint."""

    exact_review_item_count: int | None = None
    atomic_continuity_items: bool = False
    summary_max_characters: int | None = None
    single_prose_paragraph: bool = False

    def __post_init__(self) -> None:
        count = self.exact_review_item_count
        if count is not None:
            if isinstance(count, bool) or not isinstance(count, int):
                raise TypeError("exact writing review item count must be an integer")
            if not 1 <= count <= 20:
                raise ValueError(
                    "exact writing review item count must be between 1 and 20"
                )
        if self.atomic_continuity_items and count is None:
            raise ValueError(
                "atomic continuity items require an exact review item count"
            )
        summary_limit = self.summary_max_characters
        if summary_limit is not None:
            if isinstance(summary_limit, bool) or not isinstance(summary_limit, int):
                raise TypeError("summary maximum characters must be an integer")
            if not 1 <= summary_limit <= 10_000:
                raise ValueError(
                    "summary maximum characters must be between 1 and 10000"
                )

    def to_core_constraints(self) -> ResponseConstraints:
        """Expose only the business-agnostic structural portion to Core."""

        return ResponseConstraints(
            # Atomic P5 owns both exact cardinality and its accepted equivalent
            # surfaces in one full-consumption Writing parser. Running Core's
            # numbered-list counter first would reject safe unnumbered or
            # Markdown-wrapped equivalents before Writing can validate them.
            exact_top_level_item_count=(
                None
                if self.atomic_continuity_items
                else self.exact_review_item_count
            ),
        )


def derive_writing_response_contract(user_text: str) -> WritingResponseContract:
    """Derive a conservative trusted contract for one writing response.

    Atomic continuity scope is activated only when the user combines an exact
    review count, both chapter and outline sources, an inconsistency cue, and
    an explicit minimal-change cue. Generic exact-count or chapter-only
    reviews retain the existing independent-unit policy.
    """

    text = str(user_text or "")
    count = derive_exact_review_item_count(text)
    return WritingResponseContract(
        exact_review_item_count=count,
        atomic_continuity_items=bool(
            count is not None
            and _OUTLINE_SOURCE_CUE.search(text)
            and _CHAPTER_SOURCE_CUE.search(text)
            and _has_affirmative_cross_source_comparison(text)
            and _CONTINUITY_DIFFERENCE_CUE.search(text)
            and _MINIMAL_CHANGE_CUE.search(text)
        ),
        summary_max_characters=derive_summary_max_characters(text),
        single_prose_paragraph=requests_single_prose_paragraph(text),
    )


def writing_response_contract_for_request(
    request: AgentRunRequest,
) -> WritingResponseContract:
    """Return only contracts backed by an actual host writing scope.

    An unscoped request may legitimately ask for the "current chapter", but
    the safe answer is an availability refusal.  Applying an exact-count or
    semantic contract in that state would force the refusal into fabricated
    review items.
    """

    context = WritingDomainContext.from_core_context(request.domain_context)
    book_id = str(context.book_id or "").strip()
    if not book_id or not request.tools_enabled:
        return WritingResponseContract()
    contract = derive_writing_response_contract(request.latest_user_text())
    current_chapter_id = str(context.chapter_id or "").strip()
    associated_outline_ids = frozenset(
        str(value).strip()
        for value in context.associated_outline_ids
        if str(value).strip()
    )
    if contract.atomic_continuity_items and (
        not current_chapter_id or not associated_outline_ids
    ):
        # This contract compares one host-bound current chapter with explicitly
        # associated outlines. Without both sides, a safe availability reply
        # must not be forced into fabricated four-line review items.
        return WritingResponseContract()
    return contract


def writing_response_constraints(
    request: AgentRunRequest,
) -> ResponseConstraints:
    """Translate an unambiguous writing-review count into a Core contract."""

    return writing_response_contract_for_request(request).to_core_constraints()


def writing_response_validators(
    request: AgentRunRequest,
) -> tuple[ResponseValidator, ...]:
    """Build request-scoped Writing validators for Core retry orchestration."""

    contract = writing_response_contract_for_request(request)
    context = WritingDomainContext.from_core_context(request.domain_context)
    validators: list[ResponseValidator] = []
    if contract.single_prose_paragraph:
        validators.append(SingleProseParagraphValidator())
    if contract.atomic_continuity_items:
        assert contract.exact_review_item_count is not None
        # Grounding validation parses the strict structure first, so a
        # separate structure validator would emit duplicate violations and
        # duplicate repair guidance for the same candidate response.
        validators.append(AtomicContinuityGroundingValidator(
            expected_item_count=contract.exact_review_item_count,
            current_chapter_id=context.chapter_id,
            associated_outline_ids=frozenset(
                context.associated_outline_ids
            ),
        ))
    if contract.summary_max_characters is not None:
        validators.append(SummaryResponseValidator(
            max_characters=contract.summary_max_characters,
        ))
    return tuple(validators)


def writing_atomic_continuity_judge_policy(
    request: AgentRunRequest,
) -> AtomicContinuityJudgePolicy | None:
    """Return Writing's semantic policy; Composition supplies the model."""

    contract = writing_response_contract_for_request(request)
    if not contract.atomic_continuity_items:
        return None
    assert contract.exact_review_item_count is not None
    context = WritingDomainContext.from_core_context(request.domain_context)
    return AtomicContinuityJudgePolicy(
        expected_item_count=contract.exact_review_item_count,
        current_chapter_id=context.chapter_id,
        associated_outline_ids=frozenset(context.associated_outline_ids),
    )
