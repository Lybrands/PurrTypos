"""Bridge verified craft cards into user-reviewable writing-method drafts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from application.writing_method_service import WritingMethodService
from domains.novel_analysis import novel_analysis_craft_category_label
from domains.writing.methods import WritingMethodConflictError, canonical_json


class WritingMethodCandidateService:
    def __init__(self, db) -> None:
        self._db = db
        self._methods = WritingMethodService(db)

    async def create_from_analysis(
        self,
        analysis_id: str,
        *,
        craft_card_ids: Sequence[str] = (),
    ) -> dict:
        analysis = await self._db.fetch_one(
            "SELECT id, source_revision_id, version_no, content_digest "
            "FROM novel_source_analyses WHERE id = ?",
            [analysis_id],
        )
        if analysis is None:
            raise WritingMethodConflictError("正式来源分析不存在")
        selected = tuple(dict.fromkeys(str(item).strip() for item in craft_card_ids if str(item).strip()))
        rows = await self._db.fetch_all(
            "SELECT * FROM novel_source_craft_cards WHERE analysis_id = ? "
            "AND status = 'verified' ORDER BY id",
            [analysis_id],
        )
        if selected:
            by_id = {str(row["id"]): row for row in rows}
            if any(card_id not in by_id for card_id in selected):
                raise WritingMethodConflictError("候选技法卡不属于该正式分析")
            rows = [by_id[card_id] for card_id in selected]
        if not rows:
            raise WritingMethodConflictError("正式分析没有已验证技法卡")
        if any(
            "## 写作逻辑" not in str(card["body_markdown"])
            or "## 风格特征" not in str(card["body_markdown"])
            for card in rows
        ):
            raise WritingMethodConflictError(
                "这份旧分析仍是零散技法卡，请重新分析后再创建写作 Skill"
            )

        async with self._db.transaction(cancellation_linearizable=True):
            evidence_rows = await self._db.fetch_all(
                "SELECT owner_id, excerpt FROM novel_source_analysis_evidence "
                "WHERE analysis_id = ? AND owner_type = 'craft_card'",
                [analysis_id],
            )
            evidence_by_card: dict[str, list[str]] = {}
            for item in evidence_rows:
                evidence_by_card.setdefault(str(item["owner_id"]), []).append(
                    str(item["excerpt"])
                )
            grouped: dict[str, list[Mapping]] = {}
            for card in rows:
                grouped.setdefault(str(card["card_kind"]), []).append(card)
            method_ids: list[str] = []
            for category, cards in grouped.items():
                card_ids = [str(card["id"]) for card in cards]
                category_label = novel_analysis_craft_category_label(category)
                markdown = _method_markdown(
                    category_label,
                    cards,
                    [
                        excerpt
                        for card_id in card_ids
                        for excerpt in evidence_by_card.get(card_id, ())
                    ],
                )
                source_ref = {
                    "analysisId": analysis_id,
                    "analysisVersionNo": int(analysis["version_no"]),
                    "analysisDigest": str(analysis["content_digest"]),
                    "sourceRevisionId": str(analysis["source_revision_id"]),
                    "craftCardIds": card_ids,
                    "craftCardDigests": [
                        str(card["content_digest"]) for card in cards
                    ],
                }
                method = await self._methods.create_method(
                    name=f"写作 Skill · {category_label}",
                    description="写作 Skill 的分类模块；内容已与来源证据分离，需审核后主动发布。",
                    method_type="technique",
                    tags=["writing-skill", category],
                    markdown=markdown,
                    metadata={"schemaVersion": 2, "sourceRef": source_ref},
                )
                await self._db.execute(
                    "UPDATE writing_methods SET source_type = 'analysis_candidate', "
                    "source_ref_json = ? WHERE id = ?",
                    [canonical_json(source_ref), method["id"]],
                )
                method_ids.append(str(method["id"]))
            scheme = await self._methods.create_scheme(
                name="来源写作 Skill",
                description="按技法类型组织的可复用写作 Skill；审核分类模块后一次确认发布。",
                member_revision_ids=[],
            )
            scheme_ref = {
                "analysisId": analysis_id,
                "analysisVersionNo": int(analysis["version_no"]),
                "analysisDigest": str(analysis["content_digest"]),
                "sourceRevisionId": str(analysis["source_revision_id"]),
                "candidateMethodIds": method_ids,
            }
            await self._db.execute(
                "UPDATE writing_schemes SET source_type = 'analysis_candidate', "
                "source_ref_json = ? WHERE id = ?",
                [canonical_json(scheme_ref), scheme["id"]],
            )
        return await self.get_batch(str(scheme["id"]))

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
                    or not (
                        source_ref.get("craftCardIds")
                        or source_ref.get("craftCardId")
                    )
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


def _method_markdown(
    category: str,
    cards: Sequence[Mapping],
    evidence_excerpts: Sequence[str],
) -> str:
    sections = []
    for card in cards:
        body = str(card["body_markdown"] or "").strip()
        sections.append(
            f"## {str(card['title']).strip()}\n\n"
            + body.replace("## 写作逻辑", "### 写作逻辑").replace(
                "## 风格特征", "### 风格特征"
            )
        )
    cleaned = "\n\n".join(sections)
    for excerpt in sorted(
        {str(item).strip() for item in evidence_excerpts if str(item).strip()},
        key=len,
        reverse=True,
    ):
        cleaned = cleaned.replace(excerpt, "")
    return f"# {category.strip()}\n\n{cleaned}".strip()


__all__ = ["WritingMethodCandidateService"]
