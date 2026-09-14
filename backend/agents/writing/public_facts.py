"""Bounded public facts derived from committed replacement Writing results."""

from __future__ import annotations

import re
from dataclasses import dataclass

from purra.contracts import AgentRunResult, RunStatus
from purra.output import PublicFact, PublicFactBundle

from agents.writing.response_contract import WritingResponseContract
from domains.writing.continuity_validation import parse_atomic_continuity_items
from domains.writing.summary_validation import extract_validated_summary


_NUMBERED_ITEM = re.compile(
    r"(?ms)^\s*(?P<number>[1-9][0-9]*)[.、．)]\s+"
    r"(?P<body>.*?)(?=^\s*[1-9][0-9]*[.、．)]\s+|\Z)"
)


@dataclass(frozen=True, slots=True)
class WritingPublicFactsProvider:
    contract: WritingResponseContract

    def __post_init__(self) -> None:
        if not isinstance(self.contract, WritingResponseContract):
            raise TypeError("writing public facts require a response contract")

    async def facts_for(
        self,
        run_id: str,
        result: AgentRunResult,
    ) -> PublicFactBundle:
        if not isinstance(result, AgentRunResult):
            raise TypeError("writing public facts require an AgentRunResult")
        if result.run_id != run_id:
            raise ValueError("writing public facts run does not match result")
        if result.status is not RunStatus.DONE:
            raise ValueError("writing public facts require a committed result")
        candidate = str(result.final_response or "").strip()
        if not candidate:
            raise ValueError("writing public facts require validated content")

        if self.contract.atomic_continuity_items:
            count = self.contract.exact_review_item_count
            assert count is not None
            items = parse_atomic_continuity_items(
                candidate,
                expected_item_count=count,
            )
            return PublicFactBundle(
                facts=(
                    PublicFact("responseKind", "atomicContinuityReview"),
                    PublicFact("requiredItemCount", count),
                    PublicFact(
                        "reviewItems",
                        [
                            {
                                "number": item.number,
                                "dimension": item.claimed_dimension,
                                "outlineValue": item.outline_value,
                                "chapterValue": item.chapter_value,
                            }
                            for item in items
                        ],
                    ),
                )
            )

        facts: list[PublicFact] = []
        summary_limit = self.contract.summary_max_characters
        if summary_limit is not None:
            summary = extract_validated_summary(candidate)
            if not summary:
                raise ValueError("validated Writing result has no summary")
            facts.extend(
                (
                    PublicFact("responseKind", "summaryReview"),
                    PublicFact("summaryMaxCharacters", summary_limit),
                    PublicFact("summary", summary),
                )
            )
        else:
            facts.append(PublicFact("responseKind", "writingReview"))

        required_count = self.contract.exact_review_item_count
        items = _numbered_items(candidate)
        if required_count is not None:
            if len(items) != required_count:
                raise ValueError(
                    "validated Writing result does not match its item count"
                )
            facts.append(PublicFact("requiredItemCount", required_count))
        if items:
            facts.append(PublicFact("reviewItems", items))
        return PublicFactBundle(facts=tuple(facts))


def _numbered_items(content: str) -> list[str]:
    return [
        str(match.group("body") or "").strip()
        for match in _NUMBERED_ITEM.finditer(content)
        if str(match.group("body") or "").strip()
    ]


__all__ = ["WritingPublicFactsProvider"]
