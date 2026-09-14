"""Replacement Writing final-response contract and validator selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from purra.contracts import AgentRunRequest, ResponseConstraints
from purra.ports import ResponseValidator

from agents.writing.request_contract import WritingRequestContext
from domains.writing.continuity_judging import AtomicContinuityJudgePolicy
from domains.writing.continuity_validation import (
    AtomicContinuityGroundingValidator,
)
from domains.writing.paragraph_validation import (
    SingleProseParagraphValidator,
    requests_single_prose_paragraph,
)
from domains.writing.summary_validation import SummaryResponseValidator


_COUNT_TOKEN = (
    r"(?:20|1[0-9]|[1-9]|二十|十[一二三四五六七八九]?|"
    r"[一二两三四五六七八九])"
)
_COUNT_UNIT_PATTERN = re.compile(
    rf"(?P<count>{_COUNT_TOKEN})\s*(?:个|条|项|点|处|种|组|方面)"
)
_REVIEW_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:(?:可|待)?改进(?:的)?(?:之处|点|项|建议)?|"
    r"(?:具体(?:的)?|主要(?:的)?|核心(?:的)?|关键(?:的)?)?"
    r"(?:修改|改进)?建议|不一致(?:之处)?|差异|矛盾|问题|"
    r"(?:可能(?:存在)?的?)?(?:剧情)?(?:连续性)?风险|"
    r"理由|原因|要点|观察|示例)"
)
_REVIEW_REQUEST_ACTION = re.compile(
    r"(?:用|给(?:出)?|列(?:出)?|找(?:出)?|指(?:出)?|提(?:出)?|"
    r"写(?:出)?|提供|总结|概括|说明|分析|识别|检查|核对|对照|"
    r"比较|挑选|选择|枚举|评估)"
)
_AMBIGUOUS_COUNT_QUALIFIER = re.compile(
    r"(?:至少|不少于|最低|最多|至多|不超过|大约|约|左右|以上|以下|"
    r"两三|几|若干|不要只|不只|不止)|"
    r"(?:[一二两三四五六七八九十0-9]+\s*(?:到|至|-|~)\s*$)|"
    r"(?:[二两]\s*$)"
)
_CLAUSE_BOUNDARIES = "，。；;！？!?\n"
_SUMMARY_MAX_PATTERNS = (
    re.compile(
        r"(?P<limit>[1-9][0-9]{0,4})\s*字\s*(?:以内|内|以下)\s*"
        r"(?:的)?(?:摘要|概括|梗概)"
    ),
    re.compile(
        r"(?:摘要|概括|梗概)[^，。；;！？!?\n]{0,20}?"
        r"(?:控制在|限制在|压缩到)?\s*(?P<limit>[1-9][0-9]{0,4})"
        r"\s*字\s*(?:以内|内|以下)"
    ),
    re.compile(
        r"(?:不超过|至多|最多)\s*(?P<limit>[1-9][0-9]{0,4})\s*字\s*"
        r"(?:的)?(?:摘要|概括|梗概)"
    ),
)
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
_CHAPTER_SOURCE_CUE = re.compile(r"(?:(?:当前|所选|选中)?章节|本章|正文)")
_CROSS_SOURCE_COMPARISON_CUE = re.compile(
    r"(?:(?:对照|比对|比较|核对)"
    r"(?=[^。！？!?\n]{0,48}(?:(?:当前|所选|选中)?章节|本章|正文))"
    r"(?=[^。！？!?\n]{0,48}(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要)))"
    r"[^。！？!?\n]{0,48}|(?:(?:当前|所选|选中)?章节|本章|正文)"
    r"[^。！？!?\n]{0,20}(?:与|和|同|跟|相比|对比)"
    r"[^。！？!?\n]{0,20}(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要))|"
    r"(?:(?:关联|所选|选中)?(?:情节)?(?:大纲|提纲|纲要))"
    r"[^。！？!?\n]{0,20}(?:与|和|同|跟|相比|对比)"
    r"[^。！？!?\n]{0,20}(?:(?:当前|所选|选中)?章节|本章|正文))"
)
_NEGATION_CUE = re.compile(r"(?:不要|无需|无须|不必|别|禁止|仅供|只供)")
_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;，,\n]+")


@dataclass(frozen=True, slots=True)
class WritingResponseContract:
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
        limit = self.summary_max_characters
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("summary maximum characters must be an integer")
            if not 1 <= limit <= 10_000:
                raise ValueError(
                    "summary maximum characters must be between 1 and 10000"
                )

    def to_core_constraints(self) -> ResponseConstraints:
        return ResponseConstraints(
            exact_top_level_item_count=(
                None
                if self.atomic_continuity_items
                else self.exact_review_item_count
            )
        )


def derive_writing_response_contract(user_text: str) -> WritingResponseContract:
    text = str(user_text or "")
    count = _derive_exact_review_item_count(text)
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
        summary_max_characters=_derive_summary_max_characters(text),
        single_prose_paragraph=requests_single_prose_paragraph(text),
    )


def writing_response_contract_for_request(
    request: AgentRunRequest,
) -> WritingResponseContract:
    context = WritingRequestContext.from_core_context(request.domain_context)
    if not str(context.book_id or "").strip() or not request.tools_enabled:
        return WritingResponseContract()
    contract = derive_writing_response_contract(request.latest_user_text())
    outline_ids = frozenset(
        value.strip()
        for value in context.associated_outline_ids
        if value.strip()
    )
    if contract.atomic_continuity_items and (
        not str(context.chapter_id or "").strip() or not outline_ids
    ):
        return WritingResponseContract()
    return contract


def writing_response_constraints(request: AgentRunRequest) -> ResponseConstraints:
    return writing_response_contract_for_request(request).to_core_constraints()


def writing_response_validators(
    request: AgentRunRequest,
) -> tuple[ResponseValidator, ...]:
    contract = writing_response_contract_for_request(request)
    context = WritingRequestContext.from_core_context(request.domain_context)
    validators: list[ResponseValidator] = []
    if contract.single_prose_paragraph:
        validators.append(SingleProseParagraphValidator())
    if contract.atomic_continuity_items:
        assert contract.exact_review_item_count is not None
        validators.append(
            AtomicContinuityGroundingValidator(
                expected_item_count=contract.exact_review_item_count,
                current_chapter_id=context.chapter_id,
                associated_outline_ids=frozenset(
                    context.associated_outline_ids
                ),
            )
        )
    if contract.summary_max_characters is not None:
        validators.append(
            SummaryResponseValidator(
                max_characters=contract.summary_max_characters
            )
        )
    return tuple(validators)


def writing_atomic_continuity_judge_policy(
    request: AgentRunRequest,
) -> AtomicContinuityJudgePolicy | None:
    contract = writing_response_contract_for_request(request)
    if not contract.atomic_continuity_items:
        return None
    assert contract.exact_review_item_count is not None
    context = WritingRequestContext.from_core_context(request.domain_context)
    return AtomicContinuityJudgePolicy(
        expected_item_count=contract.exact_review_item_count,
        current_chapter_id=context.chapter_id,
        associated_outline_ids=frozenset(context.associated_outline_ids),
    )


def _derive_exact_review_item_count(text: str) -> int | None:
    candidates: list[int] = []
    ambiguous = False
    for match in _COUNT_UNIT_PATTERN.finditer(text):
        subject = _REVIEW_SUBJECT_PREFIX.match(
            text[match.end():match.end() + 24]
        )
        if subject is None:
            continue
        clause_start = max(
            (text.rfind(marker, 0, match.start()) for marker in _CLAUSE_BOUNDARIES),
            default=-1,
        ) + 1
        prefix = text[clause_start:match.start()]
        if _REVIEW_REQUEST_ACTION.search(prefix) is None:
            continue
        if _AMBIGUOUS_COUNT_QUALIFIER.search(prefix[-12:]):
            ambiguous = True
            continue
        count = _parse_review_count(match.group("count"))
        if count is not None:
            candidates.append(count)
    if ambiguous or not candidates or len(set(candidates)) != 1:
        return None
    return candidates[0]


def _derive_summary_max_characters(text: str) -> int | None:
    candidates = [
        int(match.group("limit"))
        for pattern in _SUMMARY_MAX_PATTERNS
        for match in pattern.finditer(text)
    ]
    if (
        not candidates
        or any(limit > 10_000 for limit in candidates)
        or len(set(candidates)) != 1
    ):
        return None
    return candidates[0]


def _parse_review_count(token: str) -> int | None:
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 20 else None
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if token == "十":
        return 10
    if token == "二十":
        return 20
    if token.startswith("十") and len(token) == 2:
        ones = digits.get(token[1])
        return 10 + ones if ones is not None else None
    return digits.get(token)


def _has_affirmative_cross_source_comparison(text: str) -> bool:
    for clause in _CLAUSE_BOUNDARY.split(text):
        if _CROSS_SOURCE_COMPARISON_CUE.search(clause) and not _NEGATION_CUE.search(
            clause
        ):
            return True
    return False


__all__ = [
    "WritingResponseContract",
    "derive_writing_response_contract",
    "writing_atomic_continuity_judge_policy",
    "writing_response_constraints",
    "writing_response_contract_for_request",
    "writing_response_validators",
]
