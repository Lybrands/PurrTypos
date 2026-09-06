"""Contracts and deterministic parsing for immutable novel sources."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


PARSER_VERSION = 1
MAX_SOURCE_BYTES = 30 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 10_000_000
SUPPORTED_SOURCE_EXTENSIONS = frozenset({".txt", ".md", ".markdown"})


class NovelSourceError(ValueError):
    status_code = 400


class NovelSourceNotFoundError(NovelSourceError):
    status_code = 404


class NovelSourceConflictError(NovelSourceError):
    status_code = 409


@dataclass(frozen=True, slots=True)
class ParsedSourceSection:
    ordinal: int
    title: str
    text: str
    start_character: int
    end_character: int


_HEADING = re.compile(
    r"(?m)^(?:#{1,6}\s+.+|\s*第[0-9一二三四五六七八九十百千万零〇两]+[章节卷部篇回][^\n]*)\s*$"
)


def validate_source_text(*, file_name: str, extension: str, content: str) -> dict:
    normalized_extension = str(extension or "").strip().lower()
    if normalized_extension not in SUPPORTED_SOURCE_EXTENSIONS:
        raise NovelSourceError("首版只支持 TXT、Markdown 文件")
    if not isinstance(content, str) or not content.strip():
        raise NovelSourceError("来源正文不能为空")
    byte_count = len(content.encode("utf-8"))
    character_count = len(content)
    if byte_count > MAX_SOURCE_BYTES or character_count > MAX_SOURCE_CHARACTERS:
        raise NovelSourceError("来源文件超过首版容量限制")
    return {
        "fileName": str(file_name or "").strip() or f"source{normalized_extension}",
        "extension": normalized_extension,
        "byteCount": byte_count,
        "characterCount": character_count,
        "contentDigest": sha256_text(content),
    }


def parse_source_sections(content: str) -> tuple[ParsedSourceSection, ...]:
    matches = list(_HEADING.finditer(content))
    starts = [match.start() for match in matches]
    if not starts:
        return (ParsedSourceSection(0, "全文", content, 0, len(content)),)
    boundaries = ([0] if starts[0] > 0 else []) + starts
    result: list[ParsedSourceSection] = []
    for ordinal, start in enumerate(boundaries):
        end = boundaries[ordinal + 1] if ordinal + 1 < len(boundaries) else len(content)
        text = content[start:end]
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        title = (
            "前言"
            if start == 0 and starts[0] > 0
            else re.sub(r"^#{1,6}\s+", "", first_line).strip() or f"第 {ordinal + 1} 节"
        )
        result.append(ParsedSourceSection(ordinal, title, text, start, end))
    return tuple(result)


def apply_source_section_layout(
    content: str,
    layout: Sequence[Mapping[str, object]],
) -> tuple[ParsedSourceSection, ...]:
    """Validate a user-reviewed layout without changing or dropping source text."""

    if not layout:
        raise NovelSourceConflictError("章节结构不能为空")
    result: list[ParsedSourceSection] = []
    expected_start = 0
    for ordinal, raw in enumerate(layout):
        title = str(raw.get("title") or "").strip()
        try:
            start = int(raw.get("startCharacter"))
            end = int(raw.get("endCharacter"))
        except (TypeError, ValueError) as error:
            raise NovelSourceConflictError("章节位置无效，请重新预览") from error
        if not title or len(title) > 300:
            raise NovelSourceConflictError("章节标题不能为空且不能超过 300 个字符")
        if start != expected_start or end <= start or end > len(content):
            raise NovelSourceConflictError("章节必须按顺序连续覆盖完整原文")
        text = content[start:end]
        if not text.strip():
            raise NovelSourceConflictError("章节正文不能为空")
        result.append(ParsedSourceSection(
            ordinal=ordinal,
            title=title,
            text=text,
            start_character=start,
            end_character=end,
        ))
        expected_start = end
    if expected_start != len(content):
        raise NovelSourceConflictError("章节结构没有覆盖完整原文")
    return tuple(result)


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


__all__ = [
    "MAX_SOURCE_BYTES", "MAX_SOURCE_CHARACTERS", "NovelSourceConflictError",
    "NovelSourceError", "NovelSourceNotFoundError", "PARSER_VERSION",
    "ParsedSourceSection", "SUPPORTED_SOURCE_EXTENSIONS", "apply_source_section_layout",
    "parse_source_sections", "sha256_text", "validate_source_text",
]
