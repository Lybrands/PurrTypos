"""Fail-closed read boundary for one bound source-analysis task."""

from __future__ import annotations

from collections.abc import Sequence

from domains.novel_analysis import canonical_digest


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

    async def validate_excerpt(
        self,
        *,
        source_revision_id: str,
        bound_section_ids: Sequence[str],
        section_id: str,
        excerpt: str,
    ) -> dict:
        section = await self.read_section(
            source_revision_id=source_revision_id,
            bound_section_ids=bound_section_ids,
            section_id=section_id,
        )
        normalized = str(excerpt or "").strip()
        if not normalized or normalized not in section["text"]:
            raise ValueError("analysis evidence excerpt does not exist in section")
        offset = section["text"].find(normalized)
        return {
            "sectionId": section["id"],
            "sectionOrdinal": section["ordinal"],
            "excerpt": normalized,
            "locator": {"start": offset, "end": offset + len(normalized)},
            "excerptDigest": canonical_digest(normalized),
            "sectionDigest": section["contentDigest"],
        }


__all__ = ["NovelAnalysisSourceReader"]
