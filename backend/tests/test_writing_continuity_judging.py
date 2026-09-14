from __future__ import annotations

import json

import pytest

from purra.contracts import AgentMessage, MessageOrigin, ToolCall
from domains.writing.continuity_judging import (
    AtomicContinuityJudgeContractError,
    AtomicContinuityJudgePolicy,
)


_CANDIDATE = (
    "1. 【天气】\n"
    "- 证据：大纲“雨夜”；正文“晴夜”。\n"
    "- 若以大纲为准：仅把正文的“晴夜”替换为“雨夜”。\n"
    "- 若保留正文：仅把大纲的“雨夜”替换为“晴夜”。\n"
    "2. 【道具颜色】\n"
    "- 证据：大纲“蓝色铜钥匙”；正文“银色铜钥匙”。\n"
    "- 若以大纲为准：仅把正文的“银色铜钥匙”替换为“蓝色铜钥匙”。\n"
    "- 若保留正文：仅把大纲的“蓝色铜钥匙”替换为“银色铜钥匙”。"
)


def _frame_untrusted_context(blocks: dict[str, str]) -> str:
    payload = [
        {"source": source, "content": content}
        for source, content in blocks.items()
        if content.strip()
    ]
    return "【参考材料】\n" + json.dumps(payload, ensure_ascii=False)


def _source_messages() -> tuple[AgentMessage, ...]:
    retrieval = _frame_untrusted_context({
        "associated_chapters_and_outlines": (
            "## 关联大纲《第一章》(outlineId=outline-1)\n"
            "本章发生在雨夜，使用蓝色铜钥匙。"
        ),
    })
    return (
        AgentMessage(
            role="developer",
            content="Untrusted context block 'writing_retrieval'.\n" + retrieval,
            origin=MessageOrigin.HOST_CONTEXT,
            attributes={
                "context_name": "writing_retrieval",
                "untrusted": True,
            },
            host_metadata={
                "writing_outline_sources": ({
                    "outlineId": "outline-1",
                    "text": "本章发生在雨夜，使用蓝色铜钥匙。",
                },),
            },
        ),
        AgentMessage(
            role="assistant",
            tool_calls=(ToolCall(
                id="read-chapter",
                name="getChapterContent",
                arguments_json="{}",
            ),),
            origin=MessageOrigin.MODEL,
        ),
        AgentMessage(
            role="tool",
            content=json.dumps({
                "chapterId": "chapter-1",
                "plainText": "正文发生在晴夜，使用银色铜钥匙。",
            }, ensure_ascii=False),
            tool_call_id="read-chapter",
            origin=MessageOrigin.HOST_TOOL_RESULT,
            host_metadata={"purra_tool_name": "getChapterContent"},
        ),
    )


def _policy() -> AtomicContinuityJudgePolicy:
    return AtomicContinuityJudgePolicy(
        expected_item_count=2,
        current_chapter_id="chapter-1",
        associated_outline_ids=frozenset({"outline-1"}),
    )


def _verdict(*, first_dimensions=None, confidence: float = 0.98):
    return json.dumps({
        "schemaVersion": 1,
        "items": [
            {
                "number": 1,
                "changedDimensions": first_dimensions or [{
                    "id": "weather.condition",
                    "outlineEvidence": "雨",
                    "chapterEvidence": "晴",
                }],
                "claimedDimensionMatches": True,
                "uncertainties": [],
                "confidence": confidence,
            },
            {
                "number": 2,
                "changedDimensions": [{
                    "id": "prop.color",
                    "outlineEvidence": "蓝色",
                    "chapterEvidence": "银色",
                }],
                "claimedDimensionMatches": True,
                "uncertainties": [],
                "confidence": 0.96,
            },
        ],
    }, ensure_ascii=False)


def test_policy_builds_untrusted_json_only_from_strict_candidate_items():
    messages = _policy().build_messages(
        content=_CANDIDATE,
        messages=_source_messages(),
    )

    assert [message.role.value for message in messages] == ["system", "user"]
    assert "untrusted story data" in messages[0].content
    assert "has been verified" in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["items"][0] == {
        "number": 1,
        "claimedDimension": "天气",
        "outlineValue": "雨夜",
        "chapterValue": "晴夜",
        "outlineDirection": "仅把正文的“晴夜”替换为“雨夜”。",
        "chapterDirection": "仅把大纲的“雨夜”替换为“晴夜”。",
        "outlineSourceExcerpt": "本章发生在雨夜，使用蓝色铜钥匙。",
        "chapterSourceExcerpt": "正文发生在晴夜，使用银色铜钥匙。",
    }


def test_policy_keeps_grounding_and_judge_for_markdown_surface_equivalent():
    markdown_candidate = _CANDIDATE.replace(
        "1. 【天气】",
        "### **1.** 【天气】",
    ).replace(
        "2. 【道具颜色】",
        "### **2.** 【道具颜色】",
    )

    messages = _policy().build_messages(
        content=markdown_candidate,
        messages=_source_messages(),
    )
    payload = json.loads(messages[1].content)

    assert [item["number"] for item in payload["items"]] == [1, 2]
    assert payload["items"][0]["outlineValue"] == "雨夜"
    assert payload["items"][0]["chapterValue"] == "晴夜"
    assert payload["items"][0]["outlineDirection"] == (
        "仅把正文的“晴夜”替换为“雨夜”。"
    )
    assert _policy().evaluate(
        judgment_content=_verdict(),
        candidate_content=markdown_candidate,
    ).accepted is True


def test_policy_refuses_to_judge_candidate_values_absent_from_host_sources():
    fabricated = _CANDIDATE.replace("雨夜", "暴雪黄昏", 3)

    with pytest.raises(AtomicContinuityJudgeContractError, match="host sources"):
        _policy().build_messages(
            content=fabricated,
            messages=_source_messages(),
        )


def test_policy_accepts_a_semantic_delta_not_shared_compound_context():
    result = _policy().evaluate(
        judgment_content=_verdict(),
        candidate_content=_CANDIDATE,
    )

    assert result.accepted is True


def test_policy_rejects_multiple_changed_dimensions_and_low_confidence():
    result = _policy().evaluate(
        judgment_content=_verdict(
            first_dimensions=[
                {
                    "id": "weather.condition",
                    "outlineEvidence": "雨",
                    "chapterEvidence": "晴",
                },
                {
                    "id": "time.daypart",
                    "outlineEvidence": "雨夜",
                    "chapterEvidence": "晴夜",
                },
            ],
            confidence=0.61,
        ),
        candidate_content=_CANDIDATE,
    )

    assert result.violation_code == "writing.atomic_continuity_semantics"
    assert result.details["items"][0]["reasons"] == (
        "changed_dimension_count",
        "low_confidence",
    )
    assert "第1项" in str(result.repair_guidance)


def test_policy_treats_uncertainty_as_repairable_semantics_not_adapter_error():
    value = json.loads(_verdict())
    value["items"][0]["uncertainties"] = ["雨夜在此处可能是意象而非天气"]

    result = _policy().evaluate(
        judgment_content=json.dumps(value, ensure_ascii=False),
        candidate_content=_CANDIDATE,
    )

    assert result.violation_code == "writing.atomic_continuity_semantics"
    assert result.details["items"][0]["reasons"] == ("uncertain_semantics",)
    assert "语义不够明确" in str(result.repair_guidance)


def test_policy_fails_closed_when_judge_evidence_is_not_an_exact_ab_substring():
    invalid = json.loads(_verdict())
    invalid["items"][0]["changedDimensions"][0]["outlineEvidence"] = "暴雨"

    with pytest.raises(
        AtomicContinuityJudgeContractError,
        match="not grounded",
    ):
        _policy().evaluate(
            judgment_content=json.dumps(invalid, ensure_ascii=False),
            candidate_content=_CANDIDATE,
        )


def test_policy_rejects_evidence_that_is_equal_after_normalization():
    candidate = _CANDIDATE.replace("大纲“雨夜”", "大纲“Ａ 雨夜”").replace(
        "正文“晴夜”",
        "正文“A雨夜”",
    ).replace(
        "正文的“晴夜”替换为“雨夜”",
        "正文的“A雨夜”替换为“Ａ 雨夜”",
    ).replace(
        "大纲的“雨夜”替换为“晴夜”",
        "大纲的“Ａ 雨夜”替换为“A雨夜”",
    )
    value = json.loads(_verdict())
    value["items"][0]["changedDimensions"][0] = {
        "id": "fixture.same",
        "outlineEvidence": "Ａ 雨夜",
        "chapterEvidence": "A雨夜",
    }

    with pytest.raises(AtomicContinuityJudgeContractError, match="not grounded"):
        _policy().evaluate(
            judgment_content=json.dumps(value, ensure_ascii=False),
            candidate_content=candidate,
        )


def test_policy_rejects_markdown_wrapped_or_extra_field_judge_output():
    with pytest.raises(AtomicContinuityJudgeContractError, match="not JSON"):
        _policy().evaluate(
            judgment_content=f"```json\n{_verdict()}\n```",
            candidate_content=_CANDIDATE,
        )

    value = json.loads(_verdict())
    value["explanation"] = "not allowed"
    with pytest.raises(AtomicContinuityJudgeContractError, match="top-level"):
        _policy().evaluate(
            judgment_content=json.dumps(value, ensure_ascii=False),
            candidate_content=_CANDIDATE,
        )


def test_policy_rejects_duplicate_json_keys():
    duplicate = _verdict().replace(
        '"schemaVersion": 1',
        '"schemaVersion": 1, "schemaVersion": 1',
        1,
    )
    with pytest.raises(AtomicContinuityJudgeContractError, match="duplicate"):
        _policy().evaluate(
            judgment_content=duplicate,
            candidate_content=_CANDIDATE,
        )
