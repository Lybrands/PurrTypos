"""Deterministic structure checks for writing continuity reviews.

Open-ended semantic classification deliberately does not live here.  This
validator proves only that a candidate can be parsed into the trusted A/B
shape consumed by the independent semantic judge.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_core.contracts import (
    AgentMessage,
    MessageOrigin,
    MessageRole,
    ResponseValidationResult,
)


_TOP_LEVEL_ITEM = re.compile(
    r"^(?P<number>[1-9][0-9]*)[.\u3001\uff0e)]\s+"
    r"(?P<body>.*?)(?=^[1-9][0-9]*[.\u3001\uff0e)]\s+\S|\Z)",
    re.MULTILINE | re.DOTALL,
)
_MARKDOWN_HEADING_PREFIX = re.compile(
    r"^#{1,6}[ \t]+(?P<body>.*?)(?:[ \t]+#+)?$"
)
_MARKDOWN_BULLET_PREFIX = re.compile(r"^[-*+][ \t]+(?P<body>.+)$")
_SURFACE_ITEM_NUMBER = re.compile(
    r"^(?P<number>[1-9][0-9]*)[.\u3001\uff0e)][ \t]*(?P<body>.+)$"
)
_BOLD_NUMBERED_TITLE = re.compile(
    r"^\*\*[1-9][0-9]*[.\u3001\uff0e)](?:\*\*|[ \t].*\*\*)"
    r"(?:[ \t]+.*)?$"
)
_BOLD_NUMBER_TOKEN = re.compile(
    r"^\*\*(?P<number>[1-9][0-9]*[.\u3001\uff0e)])\*\*"
    r"[ \t]*(?P<body>.+)$"
)
_HTML_ENTITY = re.compile(
    r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]{0,31});"
)
_MARKDOWN_LINK_OR_IMAGE = re.compile(
    r"!?\[.*\][ \t]*(?:\(|\[)"
)
_MARKDOWN_INLINE_FORMATTING = re.compile(
    r"(?:`|~~|\*\*|__|"
    r"(?<!\\)\*(?=\S).+?(?<=\S)(?<!\\)\*|"
    r"(?<![\w\\])_(?=\S).+?(?<=\S)_(?!\w)|"
    r"\\[`*_~\[\]()<>\\])"
)
_TITLE = re.compile(
    r"^(?P<title_bold>\*\*)?"
    r"\u3010(?P<dimension>[^\u3010\u3011\r\n]{1,40})\u3011"
    r"(?(title_bold)\*\*)$"
)
_OUTLINE_QUOTED_VALUE = (
    r'(?:\u201c(?P<outline_curly>[^\u201d\r\n]+)\u201d|'
    r'"(?P<outline_ascii>[^"\r\n]+)")'
)
_CHAPTER_QUOTED_VALUE = (
    r'(?:\u201c(?P<chapter_curly>[^\u201d\r\n]+)\u201d|'
    r'"(?P<chapter_ascii>[^"\r\n]+)")'
)
_EVIDENCE = re.compile(
    r"^[-*]\s*(?P<evidence_bold>\*\*)?\u8bc1\u636e"
    r"(?(evidence_bold)\*\*)\s*[:\uff1a]\s*"
    r"(?P<outline_label_bold>\*\*)?(?:\u5173\u8054)?\u5927\u7eb2"
    r"(?(outline_label_bold)\*\*)\s*"
    r"(?:[:\uff1a]|\u4e2d|\u4e3a)?\s*"
    + _OUTLINE_QUOTED_VALUE
    + r"\s*"
    r"[,\uff0c;\uff1b]\s*"
    r"(?P<chapter_label_bold>\*\*)?"
    r"(?:\u5f53\u524d)?(?:\u6b63\u6587|\u7ae0\u8282)"
    r"(?(chapter_label_bold)\*\*)\s*"
    r"(?:[:\uff1a]|\u4e2d|\u4e3a)?\s*"
    + _CHAPTER_QUOTED_VALUE
    + r"\s*"
    r"[.\u3002]?\s*[;\uff1b]?$"
)
_OUTLINE_DIRECTION = re.compile(
    r"^[-*]\s*(?P<label_bold>\*\*)?\u82e5\u4ee5\u5927\u7eb2\u4e3a\u51c6"
    r"(?(label_bold)\*\*)\s*[:\uff1a]\s*(?P<value>\S.*)$"
)
_CHAPTER_DIRECTION = re.compile(
    r"^[-*]\s*(?P<label_bold>\*\*)?\u82e5\u4fdd\u7559\u6b63\u6587"
    r"(?(label_bold)\*\*)\s*[:\uff1a]\s*(?P<value>\S.*)$"
)
_SOURCE_QUOTED_VALUE = (
    r'(?:\u201c(?P<source_curly>[^\u201d\r\n]+)\u201d|'
    r'"(?P<source_ascii>[^"\r\n]+)")'
)
_TARGET_QUOTED_VALUE = (
    r'(?:\u201c(?P<target_curly>[^\u201d\r\n]+)\u201d|'
    r'"(?P<target_ascii>[^"\r\n]+)")'
)


def _ab_replacement_pattern(source_role: str) -> re.Pattern[str]:
    return re.compile(
        r"^(?P<sentence_bold>\*\*)?\s*"
        r"(?:\u4ec5|\u53ea)\s*(?:\u9700(?:\u8981)?\s*)?"
        r"(?:\u628a|\u5c06)\s*"
        + source_role
        + r"\s*(?:\u4e2d\u7684|\u91cc\u7684|\u4e2d|\u91cc|\u7684)\s*"
        + _SOURCE_QUOTED_VALUE
        + r"\s*(?:\u66ff\u6362|\u6539)\s*(?:\u4e3a|\u6210)\s*"
        + _TARGET_QUOTED_VALUE
        + r"\s*(?(sentence_bold)(?:\*\*[.\u3002]?|[.\u3002]\*\*)|[.\u3002]?)$"
    )


_OUTLINE_AB_REPLACEMENT = _ab_replacement_pattern(
    r"(?:\u5f53\u524d)?(?:\u6b63\u6587|\u7ae0\u8282)"
)
_CHAPTER_AB_REPLACEMENT = _ab_replacement_pattern(
    r"(?:\u5173\u8054)?\u5927\u7eb2"
)
_CHAPTER_SOURCE_TOOL = "getChapterContent"
_OUTLINE_SOURCE_TOOL = "queryOutline"
ATOMIC_CONTINUITY_VISIBLE_ORDER_GUIDANCE = (
    "用户要求“先分析再建议”或执行计划含 analyze/review 时，只通过每个四行"
    "单元内第 2 行证据在前、第 3、4 行建议在后来体现；不要展示额外分析过程。"
    "最终可见回答仍只有规定数量的四行单元，不得另建分析区、建议区、总表、"
    "差异清单或第二套编号。"
)
ATOMIC_CONTINUITY_SINGLE_DIMENSION_GUIDANCE = (
    "道具的颜色、材质、来源/获取方式分别属于独立维度；时间与天气也属于独立"
    "维度。A/B 同时包含多种变化时，不得使用整段复合短语，必须改选来源中恰好"
    "只改变一个维度的另一对最短证据。"
)


@dataclass(frozen=True, slots=True)
class AtomicContinuityItem:
    """A strictly parsed, judge-ready continuity comparison."""

    number: int
    claimed_dimension: str
    outline_value: str
    chapter_value: str
    outline_direction: str
    chapter_direction: str


class AtomicContinuityStructureError(ValueError):
    """Candidate output cannot be represented by the atomic A/B contract."""

    def __init__(
        self,
        reason: str,
        *,
        item: int | None = None,
        shape: str | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.item = item
        self.shape = shape


@dataclass(frozen=True, slots=True)
class AtomicContinuitySourceEvidence:
    """Host-derived source excerpts that contain one candidate A/B pair."""

    number: int
    outline_excerpt: str
    chapter_excerpt: str


@dataclass(frozen=True, slots=True)
class AtomicContinuityGroundingIssue:
    """One candidate side that cannot be found in its required host source."""

    number: int
    source: str
    reason: str


def render_atomic_continuity_item(
    *,
    number: int | str = "N",
    dimension: str = "维度名",
    outline_value: str = "A",
    chapter_value: str = "B",
) -> str:
    """Render the one canonical four-line format shared by prompts and parser."""

    return (
        f"{number}. 【{dimension}】\n"
        f"- 证据：大纲“{outline_value}”；正文“{chapter_value}”。\n"
        f"- 若以大纲为准：仅把正文的“{chapter_value}”替换为“{outline_value}”。\n"
        f"- 若保留正文：仅把大纲的“{outline_value}”替换为“{chapter_value}”。"
    )


def normalize_atomic_continuity_response(
    content: str,
    *,
    expected_item_count: int,
) -> str:
    """Normalize only fully explicit, structurally equivalent P5 surfaces.

    The normalizer is intentionally not a prose interpreter. It accepts the
    canonical numbered form or an unnumbered/Markdown-wrapped four-line form.
    Every accepted form must still state the dimension, outline value, chapter
    value, and both inverse edit directions. No missing field or direction is
    synthesized from the other fields. JSON and code fences remain rejected.
    """

    _validate_expected_item_count(expected_item_count)
    raw_content = str(content or "").lstrip("\ufeff")
    _validate_atomic_document_surface(raw_content)
    if _starts_with_surface_item_title(raw_content):
        items = _parse_atomic_markdown_surface(
            raw_content,
            expected_item_count=expected_item_count,
        )
    else:
        items = _parse_numbered_atomic_continuity_items(
            raw_content,
            expected_item_count=expected_item_count,
        )
    return "\n".join(
        render_atomic_continuity_item(
            number=item.number,
            dimension=item.claimed_dimension,
            outline_value=item.outline_value,
            chapter_value=item.chapter_value,
        )
        for item in items
    )


def parse_atomic_continuity_items(
    content: str,
    *,
    expected_item_count: int,
) -> tuple[AtomicContinuityItem, ...]:
    """Parse a full-consumption equivalent surface without inferring semantics.

    Accepted variants are first reduced to the shared canonical renderer and
    then parsed again. Consequently grounding and the semantic Judge always
    consume the same canonical A/B representation even when the model used a
    harmless Markdown wrapper.
    """

    canonical = normalize_atomic_continuity_response(
        content,
        expected_item_count=expected_item_count,
    )
    return _parse_numbered_atomic_continuity_items(
        canonical,
        expected_item_count=expected_item_count,
    )


def _parse_numbered_atomic_continuity_items(
    content: str,
    *,
    expected_item_count: int,
) -> tuple[AtomicContinuityItem, ...]:
    """Parse the canonical numbered grammar and conservative old variants."""

    raw_content = str(content or "").lstrip("\ufeff")
    matches = tuple(_TOP_LEVEL_ITEM.finditer(raw_content))
    if matches and raw_content[:matches[0].start()].strip():
        raise AtomicContinuityStructureError(
            "leading_or_unparsed_content",
            shape=_classify_leading_surface(
                raw_content[:matches[0].start()]
            ),
        )
    expected_numbers = tuple(range(1, expected_item_count + 1))
    observed_numbers = tuple(int(match.group("number")) for match in matches)
    if observed_numbers != expected_numbers:
        raise AtomicContinuityStructureError(
            "top_level_numbering",
            shape=_top_level_numbering_shape(raw_content),
        )

    parsed: list[AtomicContinuityItem] = []
    for match in matches:
        number = int(match.group("number"))
        lines = tuple(
            line.strip()
            for line in match.group("body").splitlines()
            if line.strip()
        )
        if len(lines) != 4:
            raise AtomicContinuityStructureError(
                "four_line_template",
                item=number,
            )
        title = _TITLE.fullmatch(lines[0])
        evidence = _EVIDENCE.fullmatch(lines[1])
        outline_direction = _OUTLINE_DIRECTION.fullmatch(lines[2])
        chapter_direction = _CHAPTER_DIRECTION.fullmatch(lines[3])
        if title is None:
            raise AtomicContinuityStructureError("dimension_title", item=number)
        if evidence is None:
            raise AtomicContinuityStructureError(
                "ab_evidence",
                item=number,
                shape=_classify_evidence_surface(lines[1]),
            )
        if outline_direction is None or chapter_direction is None:
            raise AtomicContinuityStructureError(
                "two_edit_directions",
                item=number,
            )

        dimension = title.group("dimension").strip()
        outline_value = _quoted_capture(evidence, "outline").strip()
        chapter_value = _quoted_capture(evidence, "chapter").strip()
        for visible_value in (dimension, outline_value, chapter_value):
            unsafe_shape = _unsafe_visible_value_shape(visible_value)
            if unsafe_shape is not None:
                raise AtomicContinuityStructureError(
                    "unsafe_visible_content",
                    item=number,
                    shape=unsafe_shape,
                )
        if (
            not outline_value
            or not chapter_value
            or outline_value == chapter_value
        ):
            raise AtomicContinuityStructureError(
                "distinct_ab_values",
                item=number,
            )

        # This is exact structural substitution, not semantic
        # classification.  Free-form edit prose could smuggle a second change
        # past an otherwise atomic A/B pair, so both directions must express
        # the declared inverse replacement and nothing else.
        outline_edit = outline_direction.group("value").strip()
        chapter_edit = chapter_direction.group("value").strip()
        outline_replacement = _OUTLINE_AB_REPLACEMENT.fullmatch(outline_edit)
        chapter_replacement = _CHAPTER_AB_REPLACEMENT.fullmatch(chapter_edit)
        anchor_shape = _direction_anchor_shape(
            outline_replacement=outline_replacement,
            chapter_replacement=chapter_replacement,
            outline_value=outline_value,
            chapter_value=chapter_value,
        )
        if anchor_shape is not None:
            raise AtomicContinuityStructureError(
                "ab_direction_anchors",
                item=number,
                shape=anchor_shape,
            )

        parsed.append(AtomicContinuityItem(
            number=number,
            claimed_dimension=dimension,
            outline_value=outline_value,
            chapter_value=chapter_value,
            outline_direction=outline_edit,
            chapter_direction=chapter_edit,
        ))
    return tuple(parsed)


def _parse_atomic_markdown_surface(
    content: str,
    *,
    expected_item_count: int,
) -> tuple[AtomicContinuityItem, ...]:
    """Parse explicit four-line blocks with surface-only Markdown wrappers."""

    lines = tuple(
        line.strip()
        for line in str(content or "").splitlines()
        if line.strip()
    )
    blocks: list[tuple[int | None, str, list[str]]] = []
    for line in lines:
        title = _surface_item_title(line)
        if title is not None:
            number, dimension = title
            blocks.append((number, dimension, []))
            continue
        if not blocks:
            raise AtomicContinuityStructureError(
                "leading_or_unparsed_content",
                shape=_classify_leading_surface(line),
            )
        blocks[-1][2].append(line)

    if len(blocks) != expected_item_count:
        raise AtomicContinuityStructureError(
            "top_level_numbering",
            shape=_top_level_numbering_shape(content),
        )
    explicit_numbers = tuple(number for number, _, _ in blocks)
    if any(number is None for number in explicit_numbers):
        if not all(number is None for number in explicit_numbers):
            raise AtomicContinuityStructureError(
                "top_level_numbering",
                shape="wrong_explicit_sequence",
            )
        assigned_numbers = tuple(range(1, expected_item_count + 1))
    else:
        assigned_numbers = tuple(int(number) for number in explicit_numbers)
        if assigned_numbers != tuple(range(1, expected_item_count + 1)):
            raise AtomicContinuityStructureError(
                "top_level_numbering",
                shape="wrong_explicit_sequence",
            )

    numbered_blocks: list[str] = []
    for assigned_number, (_, dimension, fields) in zip(
        assigned_numbers,
        blocks,
        strict=True,
    ):
        if len(fields) != 3:
            raise AtomicContinuityStructureError(
                "four_line_template",
                item=assigned_number,
            )
        numbered_blocks.append(
            f"{assigned_number}. \u3010{dimension}\u3011\n"
            + "\n".join(_normalize_markdown_field(line) for line in fields)
        )
    return _parse_numbered_atomic_continuity_items(
        "\n".join(numbered_blocks),
        expected_item_count=expected_item_count,
    )


def _starts_with_surface_item_title(content: str) -> bool:
    first_line = next(
        (line.strip() for line in str(content or "").splitlines() if line.strip()),
        "",
    )
    return _surface_item_title(first_line) is not None


def _surface_item_title(line: str) -> tuple[int | None, str] | None:
    """Return an explicit item number/dimension after surface-only wrappers."""

    value = str(line or "").strip()
    wrapper = _MARKDOWN_HEADING_PREFIX.fullmatch(value)
    if wrapper is None:
        wrapper = _MARKDOWN_BULLET_PREFIX.fullmatch(value)
    if wrapper is not None:
        value = wrapper.group("body").strip()
    if value.startswith("**") and value.endswith("**") and len(value) > 4:
        value = value[2:-2].strip()
    bold_numbered = _BOLD_NUMBER_TOKEN.fullmatch(value)
    numbered = (
        None
        if bold_numbered is not None
        else _SURFACE_ITEM_NUMBER.fullmatch(value)
    )
    number_match = bold_numbered or numbered
    number = (
        int(number_match.group("number").rstrip(".\u3001\uff0e)"))
        if number_match is not None
        else None
    )
    if number_match is not None:
        value = number_match.group("body").strip()
    title = _TITLE.fullmatch(value)
    if title is None:
        return None
    return number, title.group("dimension").strip()


def _top_level_numbering_shape(content: str) -> str:
    """Classify numbering surfaces without retaining candidate content."""

    lines = tuple(
        line.strip()
        for line in str(content or "").splitlines()
        if line.strip()
    )
    if any(_BOLD_NUMBERED_TITLE.fullmatch(line) for line in lines):
        return "bold_numbering"
    if any(_MARKDOWN_HEADING_PREFIX.fullmatch(line) for line in lines):
        return "markdown_heading"
    titles = tuple(
        title
        for line in lines
        if (title := _surface_item_title(line)) is not None
    )
    if titles:
        if any(number is None for number, _ in titles):
            return "unnumbered_title"
        return "wrong_explicit_sequence"
    if any(_TOP_LEVEL_ITEM.match(line) for line in lines):
        return "wrong_explicit_sequence"
    return "no_title"


def _normalize_markdown_field(line: str) -> str:
    value = str(line or "").strip()
    bullet = _MARKDOWN_BULLET_PREFIX.fullmatch(value)
    if bullet is None:
        return value
    body = bullet.group("body").strip()
    if body.startswith("**") and body.endswith("**") and len(body) > 4:
        body = body[2:-2].strip()
    return f"- {body}"


def _validate_expected_item_count(expected_item_count: int) -> None:
    if isinstance(expected_item_count, bool) or not isinstance(
        expected_item_count,
        int,
    ):
        raise TypeError("atomic continuity item count must be an integer")
    if not 1 <= expected_item_count <= 20:
        raise ValueError("atomic continuity item count must be between 1 and 20")


def _validate_atomic_document_surface(content: str) -> None:
    """Reject display controls before whitespace normalization can hide them."""

    for line in str(content or "").splitlines():
        if not line.strip():
            continue
        leading = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in leading or len(leading) >= 4:
            raise AtomicContinuityStructureError(
                "unsafe_markdown_surface",
                shape="indented_code_block",
            )
    for character in str(content or ""):
        if character in "\r\n":
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            raise AtomicContinuityStructureError(
                "unsafe_markdown_surface",
                shape="unicode_control",
            )


def _unsafe_visible_value_shape(value: str) -> str | None:
    """Classify markup that renders differently from the judge's plain AST."""

    text = str(value or "")
    if "<" in text or ">" in text or _HTML_ENTITY.search(text):
        return "html_markup"
    if _MARKDOWN_LINK_OR_IMAGE.search(text):
        return "markdown_link_or_image"
    if _MARKDOWN_INLINE_FORMATTING.search(text):
        return "markdown_inline_formatting"
    return None


@dataclass(frozen=True, slots=True)
class AtomicContinuityResponseValidator:
    """Require the judge-ready A/B structure; make no semantic decision."""

    expected_item_count: int

    def __post_init__(self) -> None:
        count = self.expected_item_count
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("atomic continuity item count must be an integer")
        if not 1 <= count <= 20:
            raise ValueError(
                "atomic continuity item count must be between 1 and 20"
            )

    def validate(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> ResponseValidationResult:
        del messages
        try:
            parse_atomic_continuity_items(
                content,
                expected_item_count=self.expected_item_count,
            )
        except AtomicContinuityStructureError as error:
            details: dict[str, object] = {"reason": error.reason}
            if error.item is not None:
                details["item"] = error.item
            if error.shape is not None:
                details["shape"] = error.shape
            return ResponseValidationResult(
                violation_code="writing.atomic_continuity_structure",
                repair_guidance=_atomic_repair_guidance(
                    self.expected_item_count,
                    item=error.item,
                    reason=error.reason,
                    shape=error.shape,
                ),
                details=details,
            )
        return ResponseValidationResult()


@dataclass(frozen=True, slots=True)
class AtomicContinuityGroundingValidator:
    """Require candidate A/B values to occur in real outline/chapter sources."""

    expected_item_count: int
    current_chapter_id: str | None
    associated_outline_ids: frozenset[str]

    def __post_init__(self) -> None:
        count = self.expected_item_count
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("atomic continuity item count must be an integer")
        if not 1 <= count <= 20:
            raise ValueError(
                "atomic continuity item count must be between 1 and 20"
            )
        object.__setattr__(
            self,
            "current_chapter_id",
            str(self.current_chapter_id or "").strip() or None,
        )
        object.__setattr__(
            self,
            "associated_outline_ids",
            frozenset(
                str(value).strip()
                for value in self.associated_outline_ids
                if str(value).strip()
            ),
        )

    def validate(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> ResponseValidationResult:
        try:
            items = parse_atomic_continuity_items(
                content,
                expected_item_count=self.expected_item_count,
            )
        except AtomicContinuityStructureError as error:
            details: dict[str, object] = {"reason": error.reason}
            if error.item is not None:
                details["item"] = error.item
            if error.shape is not None:
                details["shape"] = error.shape
            return ResponseValidationResult(
                violation_code="writing.atomic_continuity_structure",
                repair_guidance=_atomic_repair_guidance(
                    self.expected_item_count,
                    item=error.item,
                    reason=error.reason,
                    shape=error.shape,
                ),
                details=details,
            )

        _, issues = ground_atomic_continuity_items(
            items,
            messages,
            current_chapter_id=self.current_chapter_id,
            associated_outline_ids=self.associated_outline_ids,
        )
        if not issues:
            return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code="writing.atomic_continuity_grounding",
            repair_guidance=_grounding_repair_guidance(
                self.expected_item_count,
            ),
            details={
                "items": [
                    {
                        "item": issue.number,
                        "source": issue.source,
                        "reason": issue.reason,
                    }
                    for issue in issues
                ],
            },
        )


def ground_atomic_continuity_items(
    items: Sequence[AtomicContinuityItem],
    messages: Sequence[AgentMessage],
    *,
    current_chapter_id: str | None,
    associated_outline_ids: frozenset[str],
) -> tuple[
    tuple[AtomicContinuitySourceEvidence, ...],
    tuple[AtomicContinuityGroundingIssue, ...],
]:
    """Ground A in host outline material and B in chapter read results.

    Only typed host-context receipts and host-executed tool results are
    considered. Provider-shaped/HTTP messages always have caller provenance,
    so candidate text and conversation history cannot establish provenance.
    """

    outline_sources = _outline_source_texts(
        messages,
        associated_outline_ids=associated_outline_ids,
    )
    chapter_sources = _chapter_source_texts(
        messages,
        current_chapter_id=current_chapter_id,
    )
    evidence: list[AtomicContinuitySourceEvidence] = []
    issues: list[AtomicContinuityGroundingIssue] = []
    for item in items:
        outline_excerpt = _matching_excerpt(
            item.outline_value,
            outline_sources,
        )
        chapter_excerpt = _matching_excerpt(
            item.chapter_value,
            chapter_sources,
        )
        if outline_excerpt is None:
            issues.append(AtomicContinuityGroundingIssue(
                number=item.number,
                source="outline",
                reason=(
                    "source_unavailable"
                    if not outline_sources
                    else "value_not_found"
                ),
            ))
        if chapter_excerpt is None:
            issues.append(AtomicContinuityGroundingIssue(
                number=item.number,
                source="chapter",
                reason=(
                    "source_unavailable"
                    if not chapter_sources
                    else "value_not_found"
                ),
            ))
        if outline_excerpt is not None and chapter_excerpt is not None:
            evidence.append(AtomicContinuitySourceEvidence(
                number=item.number,
                outline_excerpt=outline_excerpt,
                chapter_excerpt=chapter_excerpt,
            ))
    return tuple(evidence), tuple(issues)


def _outline_source_texts(
    messages: Sequence[AgentMessage],
    *,
    associated_outline_ids: frozenset[str],
) -> tuple[str, ...]:
    sources: list[str] = []
    for message in messages:
        if (
            message.role is MessageRole.DEVELOPER
            and message.origin is MessageOrigin.HOST_CONTEXT
            and message.attributes.get("context_name") == "writing_retrieval"
        ):
            values = message.host_metadata.get("writing_outline_sources")
            if isinstance(values, Sequence) and not isinstance(
                values,
                (str, bytes, bytearray),
            ):
                for value in values:
                    if not isinstance(value, Mapping):
                        continue
                    outline_id = str(value.get("outlineId") or "").strip()
                    text = value.get("text")
                    if (
                        outline_id in associated_outline_ids
                        and isinstance(text, str)
                        and text.strip()
                    ):
                        sources.append(text)

    for value in _host_tool_payloads(messages, _OUTLINE_SOURCE_TOOL):
        if not isinstance(value, Mapping):
            continue
        outlines = value.get("outlines")
        if not isinstance(outlines, Sequence) or isinstance(
            outlines,
            (str, bytes, bytearray),
        ):
            continue
        for outline in outlines:
            if not isinstance(outline, Mapping):
                continue
            outline_id = str(
                outline.get("id") or outline.get("outlineId") or ""
            ).strip()
            markdown = outline.get("markdown")
            if (
                outline_id in associated_outline_ids
                and isinstance(markdown, str)
                and markdown.strip()
            ):
                sources.append(markdown)
    return _unique_nonempty(sources)


def _chapter_source_texts(
    messages: Sequence[AgentMessage],
    *,
    current_chapter_id: str | None,
) -> tuple[str, ...]:
    sources: list[str] = []
    if current_chapter_id is None:
        return ()
    for value in _host_tool_payloads(messages, _CHAPTER_SOURCE_TOOL):
        if not isinstance(value, Mapping):
            continue
        chapter_id = str(value.get("chapterId") or "").strip()
        plain_text = value.get("plainText")
        if (
            chapter_id == current_chapter_id
            and isinstance(plain_text, str)
            and plain_text.strip()
        ):
            sources.append(plain_text)
    return _unique_nonempty(sources)


def _host_tool_payloads(
    messages: Sequence[AgentMessage],
    tool_name: str,
) -> tuple[Any, ...]:
    values: list[Any] = []
    for message in messages:
        if (
            message.role is MessageRole.TOOL
            and message.origin is MessageOrigin.HOST_TOOL_RESULT
            and message.host_metadata.get("agent_core_tool_name") == tool_name
        ):
            values.append(_decode_json_content(message.content))
    return tuple(values)


def _decode_json_content(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def _matching_excerpt(
    value: str,
    sources: Sequence[str],
    *,
    radius: int = 80,
) -> str | None:
    needle = str(value or "")
    if not needle:
        return None
    for source in sources:
        index = source.find(needle)
        if index < 0:
            continue
        start = max(0, index - radius)
        end = min(len(source), index + len(needle) + radius)
        return source[start:end]
    return None


def _unique_nonempty(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value for value in values if isinstance(value, str) and value.strip()
    ))


def _quoted_capture(match: re.Match[str], name: str) -> str:
    """Return one value from an explicitly paired curly/ASCII quote branch."""

    for suffix in ("curly", "ascii"):
        value = match.group(f"{name}_{suffix}")
        if value is not None:
            return value
    raise ValueError(f"missing paired quoted capture: {name}")


def _direction_anchor_shape(
    *,
    outline_replacement: re.Match[str] | None,
    chapter_replacement: re.Match[str] | None,
    outline_value: str,
    chapter_value: str,
) -> str | None:
    """Return a content-free syntax/anchor mismatch category, if any."""

    if outline_replacement is None and chapter_replacement is None:
        return "both_replacement_syntax"
    if outline_replacement is None:
        return "outline_replacement_syntax"
    if chapter_replacement is None:
        return "chapter_replacement_syntax"
    mismatches: list[str] = []
    if _quoted_capture(outline_replacement, "source") != chapter_value:
        mismatches.append("outline_source_anchor")
    if _quoted_capture(outline_replacement, "target") != outline_value:
        mismatches.append("outline_target_anchor")
    if _quoted_capture(chapter_replacement, "source") != outline_value:
        mismatches.append("chapter_source_anchor")
    if _quoted_capture(chapter_replacement, "target") != chapter_value:
        mismatches.append("chapter_target_anchor")
    if not mismatches:
        return None
    return mismatches[0] if len(mismatches) == 1 else "multiple_anchor_mismatches"


def _classify_evidence_surface(line: str) -> str:
    """Return a content-free diagnostic for one rejected evidence line."""

    value = str(line or "")
    if not value.lstrip().startswith(("-", "*")):
        return "bullet"
    if "证据" not in value:
        return "field_label"
    if not any(mark in value for mark in ('“', '”', '"')):
        return "quotes"
    if not any(mark in value for mark in (";", "；", ",", "，")):
        return "separator"
    if "大纲" not in value or not any(
        mark in value for mark in ("正文", "章节")
    ):
        return "source_labels"
    return "unsupported_surface_form"


def _classify_leading_surface(prefix: str) -> str:
    """Classify, but never retain, content before the first atomic item."""

    value = str(prefix or "").strip()
    if value.startswith("```"):
        return "code_fence"
    if any(line.lstrip().startswith("#") for line in value.splitlines()):
        return "markdown_heading"
    if value.startswith(("<", "[")):
        return "markup_wrapper"
    if len(value) <= 120:
        return "lead_in"
    return "other"


def _atomic_repair_guidance(
    expected_item_count: int,
    *,
    item: int | None = None,
    reason: str | None = None,
    shape: str | None = None,
) -> str:
    location = f"第 {item} 项" if item is not None else "当前回答"
    reason_guidance = {
        "leading_or_unparsed_content": "含有模板之外的前言或未解析内容",
        "top_level_numbering": "顶层编号不是从 1 开始连续编号",
        "four_line_template": "没有严格保持每项四个物理行",
        "dimension_title": "第 1 行不是单一的【维度名】标题",
        "ab_evidence": "第 2 行的 A/B 证据字段格式不符",
        "two_edit_directions": "第 3、4 行的条件标签或顺序不符",
        "distinct_ab_values": "A、B 为空或不是两个不同取值",
        "ab_direction_anchors": "两个修改方向不是声明值的严格互逆替换",
        "unsafe_markdown_surface": "含有会改变可见结构的 Markdown 或控制字符",
        "unsafe_visible_content": "维度或 A/B 含有非纯文本显示标记",
    }.get(str(reason or ""), "不符合原子 A/B 输出格式")
    shape_guidance = {
        "outline_replacement_syntax": "第 3 行替换句必须使用固定的单次替换句式。",
        "chapter_replacement_syntax": "第 4 行替换句必须使用固定的单次替换句式。",
        "both_replacement_syntax": "第 3、4 行都必须使用固定的单次替换句式。",
        "outline_source_anchor": "第 3 行的源值必须逐字复用第 2 行的 B。",
        "outline_target_anchor": "第 3 行的目标值必须逐字复用第 2 行的 A。",
        "chapter_source_anchor": "第 4 行的源值必须逐字复用第 2 行的 A。",
        "chapter_target_anchor": "第 4 行的目标值必须逐字复用第 2 行的 B。",
        "multiple_anchor_mismatches": "第 3、4 行的 A/B 必须逐字复用第 2 行并严格互逆。",
        "bold_numbering": "不要把顶层编号和标题一起包在 Markdown 加粗中。",
        "unnumbered_title": "请给所有顶层标题补上从 1 开始的连续编号。",
        "markdown_heading": "请把 Markdown 标题改为模板中的连续顶层编号。",
        "wrong_explicit_sequence": "显式顶层编号必须从 1 开始连续且不得混用无编号标题。",
        "no_title": "每项都必须有一个可识别的顶层编号和【维度名】标题。",
        "indented_code_block": "结构行不得使用四空格或 Tab 的代码块缩进。",
        "unicode_control": "删除零宽、双向、NUL 或其他 Unicode 控制字符。",
        "html_markup": "维度和 A/B 只能写可见纯文本，不得包含 HTML、注释或实体。",
        "markdown_link_or_image": "维度和 A/B 不得使用 Markdown 链接或图片。",
        "markdown_inline_formatting": "维度和 A/B 不得使用行内代码、强调、删除线或转义标记。",
    }.get(str(shape or ""), "")
    template = render_atomic_continuity_item()
    return (
        f"{location}：{reason_guidance}。连续性审阅必须使用可验证的原子 A/B "
        f"结构。{shape_guidance}请保留有来源依据的取值，重新输出恰好 "
        f"{expected_item_count} 个问题，每个顶层单元只声明一个可独立修改的"
        "维度。"
        f"{ATOMIC_CONTINUITY_VISIBLE_ORDER_GUIDANCE}"
        f"{ATOMIC_CONTINUITY_SINGLE_DIMENSION_GUIDANCE}"
        "回答的第一个非空字符必须是 1，不得添加标题、引导语或代码围栏。"
        "每项严格套用以下四个物理行；模板行内不得增加说明：\n"
        f"{template}\n"
        "模板说明（不属于输出）：N 替换为连续编号，维度名只写一个维度，"
        "A、B 只写两侧最短取值。\n"
        "两个方向必须严格采用上述互逆的 A↔B 替换，不得追加其他修改；不得写"
        "前言、后记、解释或额外字段；A、B 引号内不得放完整句或复合修改。"
        "不要自行判断复合词属于几个维度；"
        "语义校验会独立比较 A、B 及两个修改方向。"
    )


def _grounding_repair_guidance(expected_item_count: int) -> str:
    return (
        "连续性审阅中的 A/B 必须逐字来自本轮宿主提供的真实来源：A 只能从"
        "关联大纲正文或 queryOutline 结果中选择，B 只能从 getChapterContent "
        "正文结果中选择。不得根据候选回答、常识或记忆补造证据。请重新输出恰好 "
        f"{expected_item_count} 个四行检查项，每侧只引用能在对应来源中直接找到的"
        "最短原文片段；若任一来源不可用，必须停止猜测。"
    )
