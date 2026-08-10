import json

import pytest

from purra.contracts import AgentMessage, MessageRole, ToolCall
from purra.ports import ResponseValidator
from domains.writing.summary_validation import SummaryResponseValidator


_CHAPTER_SOURCE = (
    "\u96e8\u591c\uff0c\u6797\u6f88\u5728\u5317\u95e8\u627e\u5230\u4e00\u628a\u94f6\u8272\u94a5\u5319\u3002"
    "\u5979\u544a\u8bc9\u540c\u4f34\uff0c\u81ea\u5df1\u4ece\u672a\u89c1\u8fc7\u5b88\u95e8\u4eba\u5468\u781a\u3002"
    "\u949f\u697c\u5728\u5348\u591c\u6572\u4e86\u5341\u4e8c\u4e0b\uff0c\u5979\u501f\u706b\u628a\u7167\u4eae\u95e8\u9501\uff0c"
    "\u968f\u540e\u72ec\u81ea\u8fdb\u5165\u57ce\u5185\u3002"
)


def _chapter_messages(
    plain_text: str = _CHAPTER_SOURCE,
    *,
    tool_name: str = "getChapterContent",
    result_content: str | None = None,
) -> tuple[AgentMessage, ...]:
    call_id = "call-chapter"
    return (
        AgentMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                ToolCall(
                    id=call_id,
                    name=tool_name,
                    arguments_json="{}",
                ),
            ),
        ),
        AgentMessage(
            role=MessageRole.TOOL,
            tool_call_id=call_id,
            content=(
                result_content
                if result_content is not None
                else json.dumps(
                    {
                        "chapterId": "chapter-1",
                        "title": "\u7b2c\u4e00\u7ae0\uff1a\u5317\u95e8\u96e8\u591c",
                        "plainText": plain_text,
                    },
                    ensure_ascii=False,
                )
            ),
        ),
    )


def _response(summary: str, *, heading: str = "\u6458\u8981\uff08150 \u5b57\u4ee5\u5185\uff09") -> str:
    return (
        f"**{heading}**\n\n{summary}\n\n"
        "1. **\u53ef\u9009\u7684\u5c40\u90e8\u589e\u5f3a**\n"
        "   - \u82e5\u5e0c\u671b\u72ec\u7acb\u4ea4\u4ee3\u52a8\u673a\uff0c\u53ef\u8865\u4e00\u5904\u52a8\u4f5c\uff1b\u82e5\u8fd9\u662f\u6897\u6982\uff0c\u53ef\u4fdd\u7559\u3002\n\n"
        "2. **\u53ef\u9009\u7684\u9053\u5177\u5f3a\u8c03**\n"
        "   - \u82e5\u94a5\u5319\u662f\u6838\u5fc3\u9053\u5177\uff0c\u53ef\u589e\u52a0\u4e00\u5904\u89e6\u89c9\uff1b\u5426\u5219\u53ef\u4fdd\u7559\u3002"
    )


@pytest.mark.parametrize("value", [True, 1.5, "150"])
def test_summary_validator_rejects_non_integer_caps(value):
    with pytest.raises(TypeError):
        SummaryResponseValidator(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 10_001])
def test_summary_validator_rejects_out_of_range_caps(value: int):
    with pytest.raises(ValueError):
        SummaryResponseValidator(value)


def test_summary_validator_implements_generic_core_port_and_accepts_concise_summary():
    validator = SummaryResponseValidator(150)

    result = validator.validate(
        content=_response(
            "\u6797\u6f88\u96e8\u591c\u62fe\u5f97\u94a5\u5319\u540e\u72ec\u81ea\u5165\u57ce\uff0c\u5979\u5e76\u4e0d\u8ba4\u8bc6\u5b88\u95e8\u4eba\u5468\u781a\u3002"
        ),
        messages=_chapter_messages(),
    )

    assert isinstance(validator, ResponseValidator)
    assert result.accepted is True
    assert result.violation_code is None


def test_summary_validator_enforces_non_whitespace_visible_character_cap():
    validator = SummaryResponseValidator(12)

    result = validator.validate(
        content=_response("\u6797 \u6f88 \u5728 \u96e8 \u591c \u627e \u5230 \u94f6 \u8272 \u94a5 \u5319 \u540e \u5165 \u57ce\u3002"),
        messages=(),
    )

    assert result.violation_code == "writing_summary_too_long"
    assert result.details == {
        "maxCharacters": 12,
        "observedCharacters": 15,
        "countingMode": "non_whitespace_visible_characters",
    }
    assert "12 \u5b57" in str(result.repair_guidance)


@pytest.mark.parametrize(
    ("heading", "summary"),
    [
        ("\u6458\u8981\uff08139 \u5b57\uff09", "\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002"),
        ("\u6458\u8981\uff08150 \u5b57\u4ee5\u5185\uff09", "\u672c\u6458\u8981\u5171 16 \u5b57\uff1a\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002"),
        ("\u6458\u8981\uff08150 \u5b57\u4ee5\u5185\uff09", "\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002\n\n\u5b57\u6570\uff1a16 \u5b57"),
        ("150 \u5b57\u6458\u8981", "\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002"),
    ],
)
def test_summary_validator_rejects_unverified_exact_count_claims(
    heading: str,
    summary: str,
):
    result = SummaryResponseValidator(150).validate(
        content=_response(summary, heading=heading),
        messages=(),
    )

    assert result.violation_code == "writing_summary_unverified_exact_count"
    assert result.details["claimedCharacters"] in {16, 139, 150}
    assert "\u672a\u7ecf\u5bbf\u4e3b\u9a8c\u8bc1" in str(result.repair_guidance)


def test_summary_validator_accepts_a_user_supplied_upper_bound_label():
    result = SummaryResponseValidator(150).validate(
        content=_response("\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002"),
        messages=(),
    )

    assert result.accepted is True


def test_summary_validator_rejects_latest_p3_near_full_light_rewrite():
    latest_p3_summary = (
        "\u96e8\u591c\uff0c\u6797\u6f88\u5728\u5317\u95e8\u627e\u5230\u4e00\u628a\u94f6\u8272\u94a5\u5319\u3002"
        "\u5979\u5411\u540c\u4f34\u8868\u793a\u4ece\u672a\u89c1\u8fc7\u5b88\u95e8\u4eba\u5468\u781a\u3002"
        "\u949f\u697c\u5348\u591c\u6572\u54cd\u5341\u4e8c\u4e0b\uff0c\u5979\u501f\u706b\u628a\u7167\u4eae\u95e8\u9501\uff0c"
        "\u968f\u540e\u72ec\u81ea\u8fdb\u5165\u57ce\u5185\u3002"
    )

    result = SummaryResponseValidator(150).validate(
        content=_response(latest_p3_summary),
        messages=_chapter_messages(),
    )

    assert result.violation_code == "writing_summary_near_source"
    assert result.details["detection"] == "whole_source_similarity"
    assert result.details["lengthRatioBasisPoints"] >= 7200
    assert result.details["similarityBasisPoints"] >= 7400
    assert result.details["sourceCharacters"] == 57
    assert result.details["effectiveSummaryTarget"] == 34
    assert "归一化口径为 57 个字符" in str(result.repair_guidance)
    assert "内部明显压缩目标约 34 字以内" in str(result.repair_guidance)
    assert "只写 1 至 2 句" in str(result.repair_guidance)
    assert "省略不影响主线" in str(result.repair_guidance)
    assert "\u9010\u53e5\u8f7b\u6539" in str(result.repair_guidance)


def test_summary_validator_rejects_reordered_sentence_by_sentence_light_rewrite():
    source = (
        "\u96e8\u591c\uff0c\u6797\u6f88\u5728\u5317\u95e8\u627e\u5230\u94f6\u8272\u94a5\u5319\u3002"
        "\u5979\u544a\u8bc9\u540c\u4f34\u81ea\u5df1\u4ece\u672a\u89c1\u8fc7\u5468\u781a\u3002"
        "\u949f\u697c\u5728\u5348\u591c\u6572\u54cd\u5341\u4e8c\u4e0b\u3002"
        "\u5979\u501f\u706b\u628a\u7167\u4eae\u95e8\u9501\u3002"
        "\u5979\u968f\u540e\u72ec\u81ea\u8fdb\u5165\u57ce\u5185\u3002"
    )
    reordered = (
        "\u949f\u697c\u4e8e\u5348\u591c\u6572\u54cd\u5341\u4e8c\u4e0b\u3002"
        "\u6797\u6f88\u5728\u96e8\u591c\u7684\u5317\u95e8\u627e\u5230\u94f6\u8272\u94a5\u5319\u3002"
        "\u5979\u5bf9\u540c\u4f34\u8bf4\u81ea\u5df1\u4ece\u672a\u89c1\u8fc7\u5468\u781a\u3002"
        "\u5979\u501f\u52a9\u706b\u628a\u7167\u4eae\u95e8\u9501\u3002"
        "\u968f\u540e\uff0c\u5979\u72ec\u81ea\u8fdb\u5165\u57ce\u5185\u3002"
    )

    result = SummaryResponseValidator(150).validate(
        content=_response(reordered),
        messages=_chapter_messages(source),
    )

    assert result.violation_code == "writing_summary_near_source"
    assert result.details["detection"] == "sentence_by_sentence_similarity"
    assert result.details["sentenceCoverageBasisPoints"] >= 7500


def test_summary_validator_only_compares_get_chapter_content_results():
    summary = (
        "\u8fd9\u6bb5\u5185\u5bb9\u4e0e\u67d0\u4e2a\u5176\u4ed6\u5de5\u5177\u7684\u8fd4\u56de\u6587\u672c\u5b8c\u5168\u4e00\u81f4\uff0c"
        "\u4f46\u5b83\u4e0d\u662f\u672c\u8f6e\u7ae0\u8282\u6b63\u6587\u7684\u6765\u6e90\u3002"
    )

    result = SummaryResponseValidator(150).validate(
        content=_response(summary),
        messages=_chapter_messages(summary, tool_name="getOutlineContent"),
    )

    assert result.accepted is True


def test_summary_validator_fails_open_on_malformed_tool_json():
    result = SummaryResponseValidator(150).validate(
        content=_response("\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002"),
        messages=_chapter_messages(result_content="{not-json"),
    )

    assert result.accepted is True


def test_summary_validator_reads_inline_summary_before_numbered_review_items():
    content = _response(
        "\u8fd9\u884c\u4f1a\u88ab\u66ff\u6362\u3002",
    ).replace(
        "**\u6458\u8981\uff08150 \u5b57\u4ee5\u5185\uff09**\n\n\u8fd9\u884c\u4f1a\u88ab\u66ff\u6362\u3002",
        "**\u6458\u8981\uff08150 \u5b57\u4ee5\u5185\uff09\uff1a\u6797\u6f88\u96e8\u591c\u72ec\u81ea\u5165\u57ce\u3002**",
    )

    result = SummaryResponseValidator(150).validate(
        content=content,
        messages=(),
    )

    assert result.accepted is True
