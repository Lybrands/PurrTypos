"""Bridge verified craft cards into user-reviewable writing-method drafts."""

from __future__ import annotations

import json
from collections.abc import Sequence

from application.writing_method_service import WritingMethodService
from domains.writing_distillation import validate_report, render_skill, assessment_passed
from domains.novel_analysis import NOVEL_ANALYSIS_SCHEMA_VERSION, canonical_digest
from domains.writing.methods import WritingMethodConflictError, canonical_json


class WritingMethodCandidateService:
    def __init__(self, db) -> None:
        self._db = db
        self._methods = WritingMethodService(db)

    async def create_from_analysis(self, analysis_id: str) -> dict:
        async with self._db.transaction(cancellation_linearizable=True):
            analysis = await self._db.fetch_one("SELECT * FROM novel_source_analyses WHERE id = ?", [analysis_id])
            if analysis is None or int(analysis["schema_version"]) != NOVEL_ANALYSIS_SCHEMA_VERSION:
                raise WritingMethodConflictError("当前来源尚无完整蒸馏结果，请重新分析")
            summary = json.loads(analysis["summary_json"])
            report = summary.get("distillation")
            if not report or not assessment_passed(report["assessment"]):
                raise WritingMethodConflictError("写作方法尚未通过迁移复核，请根据检验结果重新蒸馏")
            rows = await self._db.fetch_all("SELECT * FROM novel_source_craft_cards WHERE analysis_id = ?", [analysis_id])
            evidence = await self._db.fetch_all("SELECT owner_id, excerpt FROM novel_source_analysis_evidence WHERE analysis_id = ? AND owner_type = 'craft_card'", [analysis_id])
            observations = [{"contentDigest": row["content_digest"], "evidence": [item for item in evidence if item["owner_id"] == row["id"]]} for row in rows]
            skill = validate_report(report, observations)
            source_ref = {
                "analysisId": analysis_id,
                "analysisVersionNo": int(analysis["version_no"]),
                "analysisDigest": analysis["content_digest"],
                "sourceRevisionId": analysis["source_revision_id"],
                "skillDigest": canonical_digest(skill),
                "craftCardIds": [row["id"] for row in rows if row["content_digest"] in skill["evidenceIds"]],
            }
            existing = await self._db.fetch_one(
                "SELECT id FROM writing_schemes WHERE source_type = 'analysis_candidate' AND json_extract(source_ref_json, '$.analysisId') = ?", [analysis_id]
            )
            if existing:
                return await self.get_batch(existing["id"])
            method = await self._methods.create_method(
                name=skill["name"], description=skill["purpose"], method_type="primary",
                tags=skill["tags"], markdown=render_skill(skill),
                metadata={"schemaVersion": 3, "sourceRef": source_ref},
            )
            await self._db.execute("UPDATE writing_methods SET source_type = 'analysis_candidate', source_ref_json = ? WHERE id = ?", [canonical_json(source_ref), method["id"]])
            scheme = await self._methods.create_scheme(name=skill["name"], description=skill["purpose"], member_revision_ids=[])
            await self._db.execute("UPDATE writing_schemes SET source_type = 'analysis_candidate', source_ref_json = ? WHERE id = ?", [canonical_json({**source_ref, "candidateMethodIds": [method["id"]]}), scheme["id"]])
        return await self.get_batch(scheme["id"])

    async def get_batch(self, scheme_id: str) -> dict:
        scheme = await self._methods.get_scheme(scheme_id)
        source_ref = dict(scheme.get("source_ref") or {})
        if scheme.get("source_type") != "analysis_candidate":
            raise WritingMethodConflictError("写作方案不是来源技法候选")
        methods = []
        for method_id in source_ref.get("candidateMethodIds") or ():
            try:
                methods.append(await self._methods.get_method(str(method_id)))
            except Exception:
                # User deletion is an explicit review action. Missing candidates
                # are omitted and cannot reappear during publication.
                continue
        return {
            "analysisId": source_ref.get("analysisId"),
            "scheme": scheme,
            "methods": methods,
            "bindingChanged": False,
        }

    async def publish_batch(
        self,
        scheme_id: str,
        *,
        method_ids: Sequence[str],
    ) -> dict:
        selected = tuple(dict.fromkeys(
            str(item).strip() for item in method_ids if str(item).strip()
        ))
        if not selected:
            raise WritingMethodConflictError("候选方案至少保留一个候选方法")
        async with self._db.transaction(cancellation_linearizable=True):
            scheme = await self._methods.get_scheme(scheme_id)
            scheme_ref = dict(scheme.get("source_ref") or {})
            allowed = {str(item) for item in scheme_ref.get("candidateMethodIds") or ()}
            if scheme.get("source_type") != "analysis_candidate" or not set(selected) <= allowed:
                raise WritingMethodConflictError("候选方法不属于该候选方案")
            revisions = []
            for method_id in selected:
                method = await self._methods.get_method(method_id)
                source_ref = dict(method.get("source_ref") or {})
                if (
                    method.get("source_type") != "analysis_candidate"
                    or source_ref.get("analysisId") != scheme_ref.get("analysisId")
                    or not source_ref.get("craftCardIds")
                ):
                    raise WritingMethodConflictError("候选方法来源引用不完整")
                revisions.append(await self._methods.publish_method(method_id))
            await self._methods.update_scheme(
                scheme_id,
                expected_draft_revision=int(scheme["draft_revision"]),
                name=str(scheme["name"]),
                description=str(scheme.get("description") or ""),
                member_revision_ids=[str(item["id"]) for item in revisions],
            )
            scheme_revision = await self._methods.publish_scheme(scheme_id)
        return {
            "methodRevisions": revisions,
            "schemeRevision": scheme_revision,
            "bindingChanged": False,
        }



__all__ = ["WritingMethodCandidateService"]
