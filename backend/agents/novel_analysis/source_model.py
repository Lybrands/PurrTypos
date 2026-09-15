"""Fail-closed source reads for one replacement analysis scope."""

from __future__ import annotations

from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.novel_analysis.source_segment import AnalysisSegment


class NovelAnalysisSourceScopeError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SqliteNovelAnalysisSourceRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def validate_scope(
        self,
        scope: NovelAnalysisRequestScope,
    ) -> NovelAnalysisRequestScope:
        revision = await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?",
            [scope.source_revision_id],
        )
        if revision is None:
            raise NovelAnalysisSourceScopeError(
                "novel analysis source revision does not exist",
                code="analysis_source_revision_missing",
            )
        for segment in scope.segments:
            await self._require_section(scope, segment)
        return scope

    async def list_segments(self, scope: NovelAnalysisRequestScope) -> dict:
        await self.validate_scope(scope)
        return {
            "sourceRevisionId": scope.source_revision_id,
            "total": len(scope.segments),
            "items": [{
                "segmentId": item.id,
                "sectionId": item.section_id,
                "sectionOrdinal": item.section_ordinal,
                "startCharacter": item.start_character,
                "endCharacter": item.end_character,
                "characterCount": item.end_character - item.start_character,
                "sectionDigest": item.section_digest,
            } for item in scope.segments],
        }

    async def read_segment(
        self,
        scope: NovelAnalysisRequestScope,
        segment_id: str,
    ) -> dict:
        normalized = str(segment_id or "").strip()
        segment = next(
            (item for item in scope.segments if item.id == normalized),
            None,
        )
        if segment is None:
            raise NovelAnalysisSourceScopeError(
                "novel analysis segment is outside the frozen scope",
                code="analysis_source_segment_outside_scope",
            )
        section = await self._require_section(scope, segment)
        text = str(section["text_content"])[
            segment.start_character:segment.end_character
        ]
        return {
            "sourceRevisionId": scope.source_revision_id,
            "segmentId": segment.id,
            "sectionId": segment.section_id,
            "sectionOrdinal": segment.section_ordinal,
            "sectionTitle": str(section["title"]),
            "sectionDigest": segment.section_digest,
            "startCharacter": segment.start_character,
            "endCharacter": segment.end_character,
            "sourceSpans": _source_spans(text, segment.start_character),
        }

    async def resolve_source_span(
        self,
        scope: NovelAnalysisRequestScope,
        *,
        segment_id: str,
        source_span_id: str,
    ) -> dict:
        segment = await self.read_segment(scope, segment_id)
        normalized = str(source_span_id or "").strip()
        span = next((
            item for item in segment["sourceSpans"]
            if item["sourceSpanId"] == normalized
        ), None)
        if span is None:
            raise NovelAnalysisSourceScopeError(
                "analysis source span is not a canonical host handle",
                code="analysis_source_span_invalid",
            )
        return {
            "sourceRevisionId": scope.source_revision_id,
            "segmentId": segment["segmentId"],
            "sectionId": segment["sectionId"],
            "sectionOrdinal": segment["sectionOrdinal"],
            "sectionDigest": segment["sectionDigest"],
            **span,
        }

    async def _require_section(
        self,
        scope: NovelAnalysisRequestScope,
        segment: AnalysisSegment,
    ) -> dict:
        row = await self._db.fetch_one(
            "SELECT id, ordinal, title, text_content, content_digest "
            "FROM novel_source_sections WHERE revision_id = ? AND id = ?",
            [scope.source_revision_id, segment.section_id],
        )
        if row is None:
            raise NovelAnalysisSourceScopeError(
                "novel analysis source section no longer resolves",
                code="analysis_source_scope_drift",
            )
        if (
            int(row["ordinal"]) != segment.section_ordinal
            or str(row["content_digest"]) != segment.section_digest
            or segment.end_character > len(str(row["text_content"]))
        ):
            raise NovelAnalysisSourceScopeError(
                "novel analysis source section changed after admission",
                code="analysis_source_scope_drift",
            )
        return dict(row)


def _source_spans(text: str, absolute_start: int) -> list[dict[str, object]]:
    result = []
    offset = 0
    while offset < len(text):
        end = min(len(text), offset + 800)
        if end < len(text):
            boundary = max(
                text.rfind(marker, offset + 400, end)
                for marker in ("\n\n", "\n", "。", "！", "？", ". ")
            )
            if boundary >= offset + 400:
                end = boundary + 1
        start_character = absolute_start + offset
        end_character = absolute_start + end
        result.append({
            "sourceSpanId": f"S{start_character}-{end_character}",
            "startCharacter": start_character,
            "endCharacter": end_character,
            "text": text[offset:end],
        })
        offset = end
    return result


__all__ = [
    "NovelAnalysisSourceScopeError",
    "SqliteNovelAnalysisSourceRepository",
]
