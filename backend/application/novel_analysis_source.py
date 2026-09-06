"""Fail-closed read boundary for one bound source-analysis task."""

from __future__ import annotations

from collections.abc import Sequence

from purra.context_budget import estimate_json_tokens

from domains.novel_analysis import NovelAnalysisSegment, canonical_digest


def analysis_source_token_budget(context_window: int) -> int:
    window = max(8_000, int(context_window or 0))
    output_reserve = max(2_000, min(32_768, window // 4))
    envelope_reserve = max(2_000, window // 16)
    return max(2_000, min(24_000, window - output_reserve - envelope_reserve))


def split_source_text(text: str, token_budget: int) -> tuple[tuple[int, int], ...]:
    """Return contiguous, deterministic ranges that fit the source token budget."""

    content = str(text)
    if not content:
        return ()
    budget = max(256, int(token_budget))
    result: list[tuple[int, int]] = []
    start = 0
    while start < len(content):
        if estimate_json_tokens(content[start:]) <= budget:
            result.append((start, len(content)))
            break
        low = start + 1
        high = len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_json_tokens(content[start:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        end = low
        minimum = start + max(1, (end - start) // 2)
        for marker in ("\n\n", "\n", "。", "！", "？", ". "):
            boundary = content.rfind(marker, minimum, end)
            if boundary >= minimum:
                end = boundary + len(marker)
                break
        if end <= start:
            end = min(len(content), start + 1)
        result.append((start, end))
        start = end
    return tuple(result)


class NovelAnalysisSourceReader:
    def __init__(self, db) -> None:
        self._db = db

    async def list_bound_sections(
        self,
        source_revision_id: str,
        section_ids: Sequence[str] | None = None,
    ) -> tuple[dict, ...]:
        revision_id = str(source_revision_id or "").strip()
        rows = await self._db.fetch_all(
            "SELECT id, revision_id, ordinal, title, text_content, "
            "content_digest FROM novel_source_sections "
            "WHERE revision_id = ? ORDER BY ordinal",
            [revision_id],
        )
        if not rows:
            raise LookupError("novel source revision has no sections")
        by_id = {str(row["id"]): dict(row) for row in rows}
        requested = tuple(str(value or "").strip() for value in section_ids or ())
        if not requested:
            return tuple(dict(row) for row in rows)
        if any(not value or value not in by_id for value in requested):
            raise ValueError("novel analysis section scope is invalid")
        return tuple(by_id[value] for value in requested)

    async def read_section(
        self,
        *,
        source_revision_id: str,
        bound_section_ids: Sequence[str],
        section_id: str,
    ) -> dict:
        revision_id = str(source_revision_id or "").strip()
        normalized_section_id = str(section_id or "").strip()
        allowed = frozenset(str(value or "").strip() for value in bound_section_ids)
        if not normalized_section_id or normalized_section_id not in allowed:
            raise PermissionError("novel analysis source section is outside binding")
        row = await self._db.fetch_one(
            "SELECT id, revision_id, ordinal, title, text_content, content_digest "
            "FROM novel_source_sections WHERE id = ? AND revision_id = ?",
            [normalized_section_id, revision_id],
        )
        if row is None:
            raise PermissionError("novel analysis source binding no longer resolves")
        text = str(row["text_content"])
        return {
            "id": str(row["id"]),
            "revisionId": str(row["revision_id"]),
            "ordinal": int(row["ordinal"]),
            "title": str(row["title"]),
            "text": text,
            "contentDigest": str(row["content_digest"]),
            "evidenceReceipt": {
                "kind": "novel_source_section_read",
                "sourceRevisionId": revision_id,
                "sectionId": normalized_section_id,
                "sectionOrdinal": int(row["ordinal"]),
                "contentDigest": str(row["content_digest"]),
                "scopeDigest": canonical_digest({
                    "sourceRevisionId": revision_id,
                    "sectionIds": sorted(allowed),
                }),
            },
        }

    async def build_segments(
        self,
        *,
        source_revision_id: str,
        section_ids: Sequence[str],
        token_budget: int,
    ) -> tuple[NovelAnalysisSegment, ...]:
        sections = await self.list_bound_sections(source_revision_id, section_ids)
        result: list[NovelAnalysisSegment] = []
        for section in sections:
            section_id = str(section["id"])
            for start, end in split_source_text(str(section["text_content"]), token_budget):
                result.append(NovelAnalysisSegment(
                    id=f"{section_id}:{start}:{end}",
                    section_id=section_id,
                    section_ordinal=int(section["ordinal"]),
                    start_character=start,
                    end_character=end,
                ))
        return tuple(result)

    async def read_segment(
        self,
        *,
        source_revision_id: str,
        bound_section_ids: Sequence[str],
        segment: NovelAnalysisSegment,
    ) -> dict:
        section = await self.read_section(
            source_revision_id=source_revision_id,
            bound_section_ids=bound_section_ids,
            section_id=segment.section_id,
        )
        if (
            segment.section_ordinal != int(section["ordinal"])
            or segment.end_character > len(section["text"])
        ):
            raise PermissionError("novel analysis segment no longer matches source")
        text = section["text"][segment.start_character:segment.end_character]
        receipt = dict(section["evidenceReceipt"])
        receipt.update({
            "kind": "novel_source_segment_read",
            "segmentId": segment.id,
            "startCharacter": segment.start_character,
            "endCharacter": segment.end_character,
            "segmentDigest": canonical_digest(text),
        })
        return {
            **section,
            "title": (
                section["title"]
                if segment.start_character == 0 and segment.end_character == len(section["text"])
                else f"{section['title']} · 片段 {segment.start_character + 1}-{segment.end_character}"
            ),
            "text": text,
            "segmentId": segment.id,
            "segmentStartCharacter": segment.start_character,
            "segmentEndCharacter": segment.end_character,
            "evidenceReceipt": receipt,
        }

    async def validate_excerpt(
        self,
        *,
        source_revision_id: str,
        bound_section_ids: Sequence[str],
        section_id: str,
        excerpt: str,
        start_character: int | None = None,
        end_character: int | None = None,
    ) -> dict:
        section = await self.read_section(
            source_revision_id=source_revision_id,
            bound_section_ids=bound_section_ids,
            section_id=section_id,
        )
        normalized = str(excerpt or "").strip()
        text = str(section["text"])
        start = 0 if start_character is None else int(start_character)
        end = len(text) if end_character is None else int(end_character)
        if start < 0 or end <= start or end > len(text):
            raise ValueError("analysis evidence range is outside section")
        relative_offset = text[start:end].find(normalized)
        if not normalized or relative_offset < 0:
            raise ValueError("analysis evidence excerpt does not exist in section")
        offset = start + relative_offset
        return {
            "sectionId": section["id"],
            "sectionOrdinal": section["ordinal"],
            "excerpt": normalized,
            "locator": {"start": offset, "end": offset + len(normalized)},
            "excerptDigest": canonical_digest(normalized),
            "sectionDigest": section["contentDigest"],
            **(
                {
                    "segmentStartCharacter": start,
                    "segmentEndCharacter": end,
                }
                if start_character is not None or end_character is not None
                else {}
            ),
        }


__all__ = [
    "NovelAnalysisSourceReader",
    "analysis_source_token_budget",
    "split_source_text",
]
