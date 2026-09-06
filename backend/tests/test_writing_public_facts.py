from __future__ import annotations

import pytest

from purra.contracts import AgentRunResult, RunStatus
from purra.json_values import thaw_json_value
from domains.writing.public_facts import WritingPublicFactsProvider
from domains.writing.response import WritingResponseContract
from application.request_mapping import (
    to_writing_agent_request,
    writing_run_options,
)
from purra.output import PublicPresentationMode, ResponseTransactionMode
from schemas.ai import ChatStreamRequest


def _fact_values(bundle) -> dict[str, object]:
    return {
        fact.key: thaw_json_value(fact.value)
        for fact in bundle.facts
    }


@pytest.mark.asyncio
async def test_atomic_continuity_candidate_becomes_bounded_structured_facts():
    candidate = (
        "1. 【时间】\n"
        "- 证据：大纲“清晨”；正文“午夜”。\n"
        "- 若以大纲为准：仅把正文的“午夜”替换为“清晨”。\n"
        "- 若保留正文：仅把大纲的“清晨”替换为“午夜”。\n"
        "2. 【关系】\n"
        "- 证据：大纲“合作三年”；正文“从未见过”。\n"
        "- 若以大纲为准：仅把正文的“从未见过”替换为“合作三年”。\n"
        "- 若保留正文：仅把大纲的“合作三年”替换为“从未见过”。"
    )
    provider = WritingPublicFactsProvider(WritingResponseContract(
        exact_review_item_count=2,
        atomic_continuity_items=True,
    ))

    bundle = await provider.facts_for(
        "run-private",
        AgentRunResult(
            run_id="run-private",
            status=RunStatus.DONE,
            final_response=candidate,
        ),
    )

    facts = _fact_values(bundle)
    assert facts == {
        "responseKind": "atomicContinuityReview",
        "requiredItemCount": 2,
        "reviewItems": [
            {
                "number": 1,
                "dimension": "时间",
                "outlineValue": "清晨",
                "chapterValue": "午夜",
            },
            {
                "number": 2,
                "dimension": "关系",
                "outlineValue": "合作三年",
                "chapterValue": "从未见过",
            },
        ],
    }
    assert candidate not in str(facts)
    assert bundle.resource_refs == ()


@pytest.mark.asyncio
async def test_summary_candidate_exposes_summary_and_review_items_not_body():
    candidate = (
        "摘要（150字以内）：林澈雨夜找到钥匙，随后独自入城。\n"
        "1. 补充她独自入城的直接动机。\n"
        "2. 增加火把照亮门锁的局部光影。"
    )
    provider = WritingPublicFactsProvider(WritingResponseContract(
        exact_review_item_count=2,
        summary_max_characters=150,
    ))

    bundle = await provider.facts_for(
        "run-summary",
        AgentRunResult(
            run_id="run-summary",
            status=RunStatus.DONE,
            final_response=candidate,
        ),
    )

    facts = _fact_values(bundle)
    assert facts == {
        "responseKind": "summaryReview",
        "summaryMaxCharacters": 150,
        "summary": "林澈雨夜找到钥匙，随后独自入城。",
        "requiredItemCount": 2,
        "reviewItems": [
            "补充她独自入城的直接动机。",
            "增加火把照亮门锁的局部光影。",
        ],
    }
    assert candidate not in str(facts)


@pytest.mark.asyncio
async def test_public_facts_reject_run_mismatch():
    provider = WritingPublicFactsProvider(WritingResponseContract(
        summary_max_characters=150,
    ))

    with pytest.raises(ValueError, match="run"):
        await provider.facts_for(
            "run-a",
            AgentRunResult(
                run_id="run-b",
                status=RunStatus.DONE,
                final_response="摘要：内容",
            ),
        )


def test_validated_writing_request_selects_live_fact_presentation():
    body = ChatStreamRequest(
        messages=[{
            "role": "user",
            "content": "读取当前章节，给出一个 150 字以内的摘要。",
        }],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "model",
            "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
            "max_generation_tokens": 2_048,
        },
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        chatAgentMode="agent",
    )
    request = to_writing_agent_request(
        body,
        {
            "model": "model",
            "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
            "max_generation_tokens": 2_048,
        },
    )

    options = writing_run_options(
        request,
        {"model": "model", "max_generation_tokens": 2_048},
    )

    assert options.resolved_response_transaction_policy.mode is (
        ResponseTransactionMode.VALIDATED_RESULT
    )
    assert options.resolved_response_transaction_policy.public_presentation is (
        PublicPresentationMode.MODEL_LIVE
    )
    assert isinstance(
        options.committed_result_facts_provider,
        WritingPublicFactsProvider,
    )
