"""Read-only projection of published Novel Analysis material."""

from __future__ import annotations

import json

from exceptions import NotFoundError


class NovelAnalysisPublishedQuery:
    def __init__(self, db) -> None:
        self._db = db

    async def list_for_revision(self, revision_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT id FROM novel_source_analyses "
            "WHERE source_revision_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            [revision_id],
        )
        return [await self.get(str(row["id"])) for row in rows]

    async def get(self, analysis_id: str) -> dict:
        analysis = await self._db.fetch_one(
            "SELECT * FROM novel_source_analyses WHERE id = ?",
            [analysis_id],
        )
        if analysis is None:
            raise NotFoundError("正式来源分析不存在")
        facts = await self._db.fetch_all(
            "SELECT * FROM novel_source_analysis_facts "
            "WHERE analysis_id = ? ORDER BY id",
            [analysis_id],
        )
        cards = await self._db.fetch_all(
            "SELECT * FROM novel_source_craft_cards "
            "WHERE analysis_id = ? ORDER BY id",
            [analysis_id],
        )
        summary = json.loads(str(analysis["summary_json"]))
        return {
            "id": analysis["id"],
            "sourceRevisionId": analysis["source_revision_id"],
            "versionNo": analysis["version_no"],
            "coverageEndOrdinal": analysis["coverage_end_ordinal"],
            "schemaVersion": analysis["schema_version"],
            "techniqueResult": summary.get("techniqueResult"),
            "generationPrompt": summary.get("generationPrompt"),
            "contentDigest": analysis["content_digest"],
            "summary": summary,
            "storyOverview": summary.get("storyOverview"),
            "facts": [
                {
                    "id": item["id"],
                    "factKind": item["fact_kind"],
                    "claimNature": item.get("claim_nature") or "fact",
                    "subjectKey": item["subject_key"],
                    "predicate": item["predicate"],
                    "value": json.loads(str(item["value_json"])),
                    "lifecycleStatus": item["lifecycle_status"],
                    "firstSectionOrdinal": item["first_section_ordinal"],
                    "lastSectionOrdinal": item["last_section_ordinal"],
                    "contentDigest": item["content_digest"],
                }
                for item in facts
            ],
            "craftCards": [
                {
                    "id": item["id"],
                    "cardKind": item["card_kind"],
                    "title": item["title"],
                    "bodyMarkdown": item["body_markdown"],
                    "status": item["status"],
                    "contentDigest": item["content_digest"],
                }
                for item in cards
            ],
            "createTime": analysis["create_time"],
        }


__all__ = ["NovelAnalysisPublishedQuery"]
