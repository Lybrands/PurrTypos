"""Deterministic final-response checks for capped writing summaries.

The validator deliberately checks delivery shape rather than factual truth.
Grounding still depends on the Writing evidence policy and semantic checks; a
lexical heuristic must not decide whether a paraphrased story fact is true.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from agent_core.contracts import (
    AgentMessage,
    MessageRole,
    ResponseValidationResult,
)


_TOP_LEVEL_FIRST_ITEM = re.compile(
    r"^1[.\u3001\uff0e)]\s+\S",
    re.MULTILINE,
)
_SUMMARY_HEADER = re.compile(
    r"^(?:\u4ee5\u4e0b(?:\u662f|\u4e3a)\s*)?(?:\u5185\u5bb9)?"
    r"(?:\u6458\u8981|\u6982\u62ec|\u6897\u6982)"
    r"(?:\s*[\uff08(][^\uff09)\n]*[\uff09)])?\s*"
    r"(?:[\uff1a:]\s*(?P<inline>.+))?$"
)
_FOLLOWING_SECTION_HEADER = re.compile(
    r"^(?:\u6539\u8fdb\u5efa\u8bae|\u53ef\u6539\u8fdb\u4e4b\u5904|\u5efa\u8bae|\u95ee\u9898|\u5ba1\u9605\u9879)"
)
_MARKDOWN_HEADING_PREFIX = re.compile(r"^\s{0,3}#{1,6}\s+")
_MARKDOWN_QUOTE_PREFIX = re.compile(r"(?m)^\s{0,3}>\s?")
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)]\([^)]*\)")
_SENTENCE_BOUNDARY = re.compile(r"[\u3002\uff01\uff1f!?\uff1b;\n]+")

# These patterns require an exact-looking claim.  Qualified upper bounds such
# as ``150 \u5b57\u4ee5\u5185`` and estimates such as ``\u7ea6 100 \u5b57`` do not match.
_UNVERIFIED_EXACT_COUNT = (
    re.compile(
        r"(?:\u6458\u8981|\u6982\u62ec|\u6897\u6982)\s*[\uff08(]\s*"
        r"(?P<count>[1-9][0-9]{0,4})\s*\u5b57\s*[\uff09)]"
    ),
    re.compile(
        r"(?:\u672c(?:\u6b21|\u6587)?\u6458\u8981|\u6458\u8981|\u6982\u62ec|\u6897\u6982|\u672c\u6587)"
        r"\s*(?:\u5171|\u5408\u8ba1|\u603b\u8ba1|\u5b9e\u9645|\u8ba1)\s*"
        r"(?P<count>[1-9][0-9]{0,4})\s*\u5b57"
    ),
    re.compile(
        r"(?:\u5171|\u5408\u8ba1|\u603b\u8ba1|\u5b9e\u9645|\u8ba1\u6570\u4e3a)\s*"
        r"(?P<count>[1-9][0-9]{0,4})\s*\u5b57(?:\s|[\uff0c\u3002\uff1b;\uff01!\uff1f?]|$)"
    ),
    re.compile(
        r"\u5b57\u6570\s*(?:\u4e3a|[\uff1a:])\s*"
        r"(?P<count>[1-9][0-9]{0,4})(?:\s*\u5b57)?"
    ),
    re.compile(
        r"(?m)^\s{0,3}(?:#{1,6}\s*)?(?:[*_`]\s*)*"
        r"(?P<count>[1-9][0-9]{0,4})\s*\u5b57\s*"
        r"(?:\u6458\u8981|\u6982\u62ec|\u6897\u6982)(?:\s*[*_`])*(?:\s*$|[\uff1a:])"
    ),
)

_MIN_SOURCE_CHARACTERS = 40
_NEAR_SOURCE_MIN_LENGTH_RATIO = 0.72
_NEAR_SOURCE_MIN_SIMILARITY = 0.74
_SENTENCE_MATCH_MIN_SIMILARITY = 0.72
_SENTENCE_MATCH_MIN_COVERAGE = 0.75
_SENTENCE_MATCH_MIN_LENGTH_RATIO = 0.65
_SOURCE_COMPRESSION_RATIO = 0.60
_MIN_EFFECTIVE_COMPRESSION_CHARACTERS = 24


@dataclass(frozen=True, slots=True)
class SummaryResponseValidator:
    """Enforce one explicit summary cap and prevent source-shaped delivery."""

    max_characters: int

    def __post_init__(self) -> None:
        value = self.max_characters
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("summary maximum characters must be an integer")
        if not 1 <= value <= 10_000:
            raise ValueError(
                "summary maximum characters must be between 1 and 10000"
            )

    def validate(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> ResponseValidationResult:
        """Return a trusted repair request for deterministic violations only."""

        response = str(content or "")
        exact_count = _unverified_exact_count(response)
        if exact_count is not None:
            return ResponseValidationResult(
                violation_code="writing_summary_unverified_exact_count",
                repair_guidance=_repair_guidance(self.max_characters),
                details={"claimedCharacters": exact_count},
            )

        summary = _extract_summary(response)
        if not summary.strip():
            return ResponseValidationResult(
                violation_code="writing_summary_missing",
                repair_guidance=_repair_guidance(self.max_characters),
                details={"maxCharacters": self.max_characters},
            )

        observed_characters = _visible_character_count(summary)
        if observed_characters > self.max_characters:
            return ResponseValidationResult(
                violation_code="writing_summary_too_long",
                repair_guidance=_repair_guidance(self.max_characters),
                details={
                    "maxCharacters": self.max_characters,
                    "observedCharacters": observed_characters,
                    "countingMode": "non_whitespace_visible_characters",
                },
            )

        for source in _get_chapter_plain_texts(messages):
            similarity = _near_source_similarity(summary, source)
            if similarity is None:
                continue
            source_characters = int(similarity["sourceCharacters"])
            effective_target = _effective_compression_target(
                max_characters=self.max_characters,
                source_characters=source_characters,
            )
            return ResponseValidationResult(
                violation_code="writing_summary_near_source",
                repair_guidance=_repair_guidance(
                    self.max_characters,
                    source_characters=source_characters,
                    effective_target=effective_target,
                ),
                details={
                    **similarity,
                    "effectiveSummaryTarget": effective_target,
                },
            )

        return ResponseValidationResult()


def _repair_guidance(
    max_characters: int,
    *,
    source_characters: int | None = None,
    effective_target: int | None = None,
) -> str:
    compression_guidance = ""
    if source_characters is not None and effective_target is not None:
        compression_guidance = (
            f"本轮读取正文按守卫的归一化口径为 {source_characters} 个字符；"
            f"内部明显压缩目标约 {effective_target} 字以内。这个数字只用于修复，"
            "不得在用户可见答复中声称摘要实际为该精确字数。摘要只写 1 至 2 句，"
            "保留主线角色、关键动作与结果，省略不影响主线的场景修饰、动作方式、"
            "精确数量等次要细节。"
        )
    return (
        "重写完整答复：摘要正文按非空白可见字符计数不得超过 "
        f"{max_characters} 字，并保留用户要求的其他审阅项。只可在标题中重申“"
        f"{max_characters} 字以内”这一上限；不得声称未经宿主验证的实际精确字数。"
        + compression_guidance
        + "摘要应压缩为关键事件，不得复制近乎整段来源，也不得沿用来源句序逐句轻改。"
        "不要改变事实强度或新增推断，只返回修复后的完整答复。"
    )


def _effective_compression_target(
    *,
    max_characters: int,
    source_characters: int,
) -> int:
    proportional_target = int(source_characters * _SOURCE_COMPRESSION_RATIO)
    return min(
        max_characters,
        max(_MIN_EFFECTIVE_COMPRESSION_CHARACTERS, proportional_target),
    )


def _unverified_exact_count(content: str) -> int | None:
    for pattern in _UNVERIFIED_EXACT_COUNT:
        match = pattern.search(content)
        if match is not None:
            return int(match.group("count"))
    return None


def _extract_summary(content: str) -> str:
    first_item = _TOP_LEVEL_FIRST_ITEM.search(content)
    prefix = content[:first_item.start()] if first_item is not None else content
    lines = prefix.splitlines()

    header_index: int | None = None
    inline = ""
    for index, line in enumerate(lines):
        header = _plain_heading(line)
        match = _SUMMARY_HEADER.fullmatch(header)
        if match is not None:
            header_index = index
            inline = str(match.group("inline") or "").strip()
            break

    if header_index is None:
        return prefix.strip()

    body: list[str] = []
    if inline:
        body.append(inline)
    for line in lines[header_index + 1:]:
        plain = _plain_heading(line)
        if plain and (
            _MARKDOWN_HEADING_PREFIX.match(line)
            or _FOLLOWING_SECTION_HEADER.match(plain)
        ):
            break
        body.append(line)
    return "\n".join(body).strip()


def _plain_heading(line: str) -> str:
    value = _MARKDOWN_HEADING_PREFIX.sub("", str(line or "").strip())
    value = value.replace("**", "").replace("__", "")
    return value.strip("*_` ")


def _visible_text(content: str) -> str:
    value = _MARKDOWN_LINK.sub(r"\1", str(content or ""))
    value = _MARKDOWN_QUOTE_PREFIX.sub("", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", value)
    value = value.replace("**", "").replace("__", "")
    value = value.replace("`", "").replace("*", "").replace("_", "")
    return value


def _visible_character_count(content: str) -> int:
    return sum(not character.isspace() for character in _visible_text(content))


def _get_chapter_plain_texts(
    messages: Sequence[AgentMessage],
) -> tuple[str, ...]:
    chapter_call_ids = {
        call.id
        for message in messages
        for call in message.tool_calls
        if call.name == "getChapterContent"
    }
    sources: list[str] = []
    for message in messages:
        if (
            message.role is not MessageRole.TOOL
            or message.tool_call_id not in chapter_call_ids
        ):
            continue
        decoded = _decode_tool_content(message.content)
        sources.extend(_plain_text_values(decoded))
    return tuple(dict.fromkeys(source for source in sources if source.strip()))


def _decode_tool_content(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def _plain_text_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, Mapping):
        plain_text = value.get("plainText")
        if isinstance(plain_text, str) and plain_text.strip():
            values.append(plain_text)
        for nested in value.values():
            if isinstance(nested, (Mapping, list, tuple)):
                values.extend(_plain_text_values(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            values.extend(_plain_text_values(nested))
    return values


def _near_source_similarity(
    summary: str,
    source: str,
) -> dict[str, int | str] | None:
    normalized_summary = _normalize_for_similarity(summary)
    normalized_source = _normalize_for_similarity(source)
    if len(normalized_source) < _MIN_SOURCE_CHARACTERS:
        return None

    length_ratio = len(normalized_summary) / len(normalized_source)
    similarity = _sequence_similarity(normalized_summary, normalized_source)
    if (
        length_ratio >= _NEAR_SOURCE_MIN_LENGTH_RATIO
        and similarity >= _NEAR_SOURCE_MIN_SIMILARITY
    ):
        return {
            "detection": "whole_source_similarity",
            "sourceCharacters": len(normalized_source),
            "summaryCharacters": len(normalized_summary),
            "lengthRatioBasisPoints": round(length_ratio * 10_000),
            "similarityBasisPoints": round(similarity * 10_000),
        }

    source_sentences = _normalized_sentences(source)
    summary_sentences = _normalized_sentences(summary)
    if len(source_sentences) < 3 or len(summary_sentences) < 3:
        return None
    matched = sum(
        max(
            (_sequence_similarity(sentence, candidate)
             for candidate in summary_sentences),
            default=0.0,
        )
        >= _SENTENCE_MATCH_MIN_SIMILARITY
        for sentence in source_sentences
    )
    sentence_coverage = matched / len(source_sentences)
    if (
        length_ratio >= _SENTENCE_MATCH_MIN_LENGTH_RATIO
        and sentence_coverage >= _SENTENCE_MATCH_MIN_COVERAGE
    ):
        return {
            "detection": "sentence_by_sentence_similarity",
            "sourceCharacters": len(normalized_source),
            "summaryCharacters": len(normalized_summary),
            "lengthRatioBasisPoints": round(length_ratio * 10_000),
            "sentenceCoverageBasisPoints": round(sentence_coverage * 10_000),
            "matchedSourceSentences": matched,
            "sourceSentences": len(source_sentences),
        }
    return None


def _normalized_sentences(content: str) -> tuple[str, ...]:
    return tuple(
        normalized
        for sentence in _SENTENCE_BOUNDARY.split(_visible_text(content))
        if len(normalized := _normalize_for_similarity(sentence)) >= 6
    )


def _normalize_for_similarity(content: str) -> str:
    value = unicodedata.normalize("NFKC", _visible_text(content)).casefold()
    return "".join(character for character in value if character.isalnum())


def _sequence_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()
