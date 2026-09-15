"""Deterministic source slicing for scalable novel-analysis Runs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from purra.context_budget import estimate_json_tokens


SOURCE_CONTEXT_SHARE_NUMERATOR = 2
SOURCE_CONTEXT_SHARE_DENOMINATOR = 5
DEFAULT_ESTIMATOR_SAFETY_BPS = 9_000


class NovelAnalysisSliceCompilationError(ValueError):
    code = "novel_analysis_slice_compilation_failed"


@dataclass(frozen=True, slots=True)
class SourceTokenizer:
    id: str
    version: str
    count_kind: str
    count: Callable[[str], int]
    safety_basis_points: int = DEFAULT_ESTIMATOR_SAFETY_BPS

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.version.strip():
            raise NovelAnalysisSliceCompilationError(
                "source tokenizer identity is required"
            )
        if self.count_kind not in {"exact", "estimated"}:
            raise NovelAnalysisSliceCompilationError(
                "source tokenizer count kind is invalid"
            )
        if not 1 <= self.safety_basis_points <= 10_000:
            raise NovelAnalysisSliceCompilationError(
                "source tokenizer safety factor is invalid"
            )


DEFAULT_SOURCE_TOKENIZER = SourceTokenizer(
    id="purra.estimate_json_tokens",
    version="1",
    count_kind="estimated",
    count=estimate_json_tokens,
)


@dataclass(frozen=True, slots=True)
class SliceSourceSection:
    id: str
    ordinal: int
    title: str
    text: str
    content_digest: str
    byte_count: int
    character_count: int
    token_count: int


@dataclass(frozen=True, slots=True)
class SliceSourceRange:
    section_id: str
    section_ordinal: int
    start_character: int
    end_character: int
    character_count: int
    token_count: int
    content_digest: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "sectionId": self.section_id,
            "sectionOrdinal": self.section_ordinal,
            "startCharacter": self.start_character,
            "endCharacter": self.end_character,
            "characterCount": self.character_count,
            "tokenCount": self.token_count,
            "contentDigest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class NovelAnalysisSourceSlice:
    id: str
    position: int
    ranges: tuple[SliceSourceRange, ...]
    character_count: int
    token_count: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "sliceId": self.id,
            "position": self.position,
            "characterCount": self.character_count,
            "tokenCount": self.token_count,
            "ranges": [item.to_mapping() for item in self.ranges],
        }


@dataclass(frozen=True, slots=True)
class NovelAnalysisSliceManifest:
    source_revision_id: str
    source_revision_digest: str
    context_window_tokens: int
    source_token_limit: int
    packing_token_limit: int
    tokenizer_id: str
    tokenizer_version: str
    token_count_kind: str
    total_character_count: int
    total_token_count: int
    slices: tuple[NovelAnalysisSourceSlice, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "sourceRevisionId": self.source_revision_id,
            "sourceRevisionDigest": self.source_revision_digest,
            "contextWindowTokens": self.context_window_tokens,
            "sourceTokenLimit": self.source_token_limit,
            "packingTokenLimit": self.packing_token_limit,
            "tokenizer": {
                "id": self.tokenizer_id,
                "version": self.tokenizer_version,
                "countKind": self.token_count_kind,
            },
            "totalCharacterCount": self.total_character_count,
            "totalTokenCount": self.total_token_count,
            "sliceCount": len(self.slices),
            "slices": [item.to_mapping() for item in self.slices],
        }


async def compile_persisted_source_slice_manifest(
    db,
    *,
    source_revision_id: str,
    context_window_tokens: int,
    tokenizer: SourceTokenizer = DEFAULT_SOURCE_TOKENIZER,
) -> NovelAnalysisSliceManifest:
    revision_id = str(source_revision_id or "").strip()
    revision = await db.fetch_one(
        "SELECT id, content_digest, character_count "
        "FROM novel_source_revisions WHERE id = ?",
        [revision_id],
    )
    if revision is None:
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source revision does not exist"
        )
    rows = await db.fetch_all(
        "SELECT id, ordinal, title, text_content, content_digest, "
        "byte_count, character_count FROM novel_source_sections "
        "WHERE revision_id = ? ORDER BY ordinal",
        [revision_id],
    )
    if not rows:
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source revision has no sections"
        )
    sections = []
    for row in rows:
        text = str(row["text_content"])
        digest = str(row["content_digest"] or "").strip()
        if (
            int(row["byte_count"]) != len(text.encode("utf-8"))
            or int(row["character_count"]) != len(text)
        ):
            raise NovelAnalysisSliceCompilationError(
                "novel analysis source section capacity metadata is invalid"
            )
        cached = await db.fetch_one(
            "SELECT token_count, count_kind, content_digest "
            "FROM novel_source_section_token_metrics "
            "WHERE section_id = ? AND tokenizer_id = ? AND tokenizer_version = ?",
            [row["id"], tokenizer.id, tokenizer.version],
        )
        if cached is not None and (
            str(cached["content_digest"]) != digest
            or str(cached["count_kind"]) != tokenizer.count_kind
        ):
            raise NovelAnalysisSliceCompilationError(
                "cached source token metric does not match immutable section"
            )
        token_count = (
            int(cached["token_count"])
            if cached is not None
            else _count_tokens(tokenizer, text)
        )
        if cached is None:
            await db.execute(
                "INSERT INTO novel_source_section_token_metrics "
                "(source_revision_id, section_id, tokenizer_id, tokenizer_version, "
                "token_count, count_kind, content_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [revision_id, row["id"], tokenizer.id, tokenizer.version,
                 token_count, tokenizer.count_kind, digest],
            )
        sections.append(SliceSourceSection(
            id=str(row["id"]),
            ordinal=int(row["ordinal"]),
            title=str(row["title"]),
            text=text,
            content_digest=digest,
            byte_count=int(row["byte_count"]),
            character_count=int(row["character_count"]),
            token_count=token_count,
        ))
    return compile_source_slice_manifest(
        source_revision_id=revision_id,
        source_revision_digest=str(revision["content_digest"]),
        context_window_tokens=context_window_tokens,
        sections=sections,
        tokenizer=tokenizer,
        total_character_count=int(revision["character_count"]),
    )


def compile_source_slice_manifest(
    *,
    source_revision_id: str,
    source_revision_digest: str,
    context_window_tokens: int,
    sections: Sequence[SliceSourceSection],
    tokenizer: SourceTokenizer = DEFAULT_SOURCE_TOKENIZER,
    total_character_count: int | None = None,
) -> NovelAnalysisSliceManifest:
    if type(context_window_tokens) is not int or context_window_tokens < 1_000:
        raise NovelAnalysisSliceCompilationError(
            "novel analysis context window is invalid"
        )
    source_token_limit = (
        context_window_tokens * SOURCE_CONTEXT_SHARE_NUMERATOR
        // SOURCE_CONTEXT_SHARE_DENOMINATOR
    )
    packing_token_limit = (
        source_token_limit * tokenizer.safety_basis_points // 10_000
    )
    if packing_token_limit < 1:
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source token budget is empty"
        )
    ordered = tuple(sections)
    if not ordered or any(
        item.ordinal != index or not item.id or not item.content_digest
        for index, item in enumerate(ordered)
    ):
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source sections are not canonical"
        )

    groups: list[tuple[SliceSourceRange, ...]] = []
    current: list[SliceSourceRange] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            groups.append(tuple(current))
            current = []
            current_tokens = 0

    for section in ordered:
        _validate_section(section, tokenizer)
        if section.token_count > packing_token_limit:
            flush()
            for item in _split_oversized_section(
                section,
                packing_token_limit=packing_token_limit,
                tokenizer=tokenizer,
            ):
                groups.append((item,))
            continue
        item = _whole_section_range(section)
        if current and current_tokens + item.token_count > packing_token_limit:
            flush()
        current.append(item)
        current_tokens += item.token_count
    flush()

    slices = tuple(
        _slice_from_ranges(
            source_revision_id=source_revision_id,
            source_revision_digest=source_revision_digest,
            context_window_tokens=context_window_tokens,
            packing_token_limit=packing_token_limit,
            tokenizer_id=tokenizer.id,
            tokenizer_version=tokenizer.version,
            position=position,
            ranges=ranges,
        )
        for position, ranges in enumerate(groups)
    )
    _validate_complete_coverage(ordered, slices)
    observed_characters = sum(item.character_count for item in ordered)
    if total_character_count is not None and total_character_count < observed_characters:
        raise NovelAnalysisSliceCompilationError(
            "source revision character count is smaller than its sections"
        )
    return NovelAnalysisSliceManifest(
        source_revision_id=source_revision_id,
        source_revision_digest=source_revision_digest,
        context_window_tokens=context_window_tokens,
        source_token_limit=source_token_limit,
        packing_token_limit=packing_token_limit,
        tokenizer_id=tokenizer.id,
        tokenizer_version=tokenizer.version,
        token_count_kind=tokenizer.count_kind,
        total_character_count=(
            observed_characters
            if total_character_count is None
            else total_character_count
        ),
        total_token_count=sum(item.token_count for item in ordered),
        slices=slices,
    )


def _validate_section(section: SliceSourceSection, tokenizer: SourceTokenizer) -> None:
    del tokenizer
    if (
        section.character_count != len(section.text)
        or section.byte_count != len(section.text.encode("utf-8"))
        or type(section.token_count) is not int
        or section.token_count < 0
    ):
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source section metrics do not match content"
        )


def _whole_section_range(section: SliceSourceSection) -> SliceSourceRange:
    return SliceSourceRange(
        section_id=section.id,
        section_ordinal=section.ordinal,
        start_character=0,
        end_character=section.character_count,
        character_count=section.character_count,
        token_count=section.token_count,
        content_digest=section.content_digest,
    )


def _split_oversized_section(
    section: SliceSourceSection,
    *,
    packing_token_limit: int,
    tokenizer: SourceTokenizer,
) -> tuple[SliceSourceRange, ...]:
    result = []
    start = 0
    while start < len(section.text):
        low = start + 1
        high = len(section.text)
        while low < high:
            middle = (low + high + 1) // 2
            if _count_tokens(tokenizer, section.text[start:middle]) <= packing_token_limit:
                low = middle
            else:
                high = middle - 1
        end = low
        if end < len(section.text):
            minimum = start + max(1, (end - start) // 2)
            for marker in ("\n\n", "\n", "。", "！", "？", ". "):
                boundary = section.text.rfind(marker, minimum, end)
                if boundary >= minimum:
                    end = boundary + len(marker)
                    break
        if end <= start:
            raise NovelAnalysisSliceCompilationError(
                "novel analysis source slicing made no progress"
            )
        content = section.text[start:end]
        token_count = _count_tokens(tokenizer, content)
        if token_count > packing_token_limit:
            raise NovelAnalysisSliceCompilationError(
                "novel analysis source slice exceeds its packing budget"
            )
        result.append(SliceSourceRange(
            section_id=section.id,
            section_ordinal=section.ordinal,
            start_character=start,
            end_character=end,
            character_count=end - start,
            token_count=token_count,
            content_digest=section.content_digest,
        ))
        start = end
    return tuple(result)


def _slice_from_ranges(
    *,
    source_revision_id: str,
    source_revision_digest: str,
    context_window_tokens: int,
    packing_token_limit: int,
    tokenizer_id: str,
    tokenizer_version: str,
    position: int,
    ranges: tuple[SliceSourceRange, ...],
) -> NovelAnalysisSourceSlice:
    token_count = sum(item.token_count for item in ranges)
    if token_count > packing_token_limit:
        raise NovelAnalysisSliceCompilationError(
            "novel analysis source slice exceeds its packing budget"
        )
    identity = {
        "schemaVersion": 1,
        "sourceRevisionId": source_revision_id,
        "sourceRevisionDigest": source_revision_digest,
        "contextWindowTokens": context_window_tokens,
        "packingTokenLimit": packing_token_limit,
        "tokenizerId": tokenizer_id,
        "tokenizerVersion": tokenizer_version,
        "position": position,
        "ranges": [item.to_mapping() for item in ranges],
    }
    digest = sha256(json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return NovelAnalysisSourceSlice(
        id=f"source-slice-{digest[:24]}",
        position=position,
        ranges=ranges,
        character_count=sum(item.character_count for item in ranges),
        token_count=token_count,
    )


def _validate_complete_coverage(
    sections: tuple[SliceSourceSection, ...],
    slices: tuple[NovelAnalysisSourceSlice, ...],
) -> None:
    by_section: dict[str, list[SliceSourceRange]] = {}
    for source_slice in slices:
        for item in source_slice.ranges:
            by_section.setdefault(item.section_id, []).append(item)
    for section in sections:
        ranges = by_section.get(section.id, [])
        cursor = 0
        for item in ranges:
            if item.start_character != cursor:
                raise NovelAnalysisSliceCompilationError(
                    "novel analysis source slices do not cover content contiguously"
                )
            cursor = item.end_character
        if cursor != section.character_count:
            raise NovelAnalysisSliceCompilationError(
                "novel analysis source slices do not cover every section"
            )


def _count_tokens(tokenizer: SourceTokenizer, text: str) -> int:
    value = tokenizer.count(text)
    if type(value) is not int or value < 0:
        raise NovelAnalysisSliceCompilationError(
            "source tokenizer returned an invalid token count"
        )
    return value


__all__ = [
    "DEFAULT_SOURCE_TOKENIZER",
    "NovelAnalysisSliceCompilationError",
    "NovelAnalysisSliceManifest",
    "NovelAnalysisSourceSlice",
    "SliceSourceRange",
    "SliceSourceSection",
    "SourceTokenizer",
    "compile_persisted_source_slice_manifest",
    "compile_source_slice_manifest",
]
