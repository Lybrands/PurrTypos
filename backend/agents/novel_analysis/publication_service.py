"""Replacement-only user review and publication for Novel Analysis v1."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import uuid4

from agents.novel_analysis.review_artifact import (
    NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
    NovelAnalysisReviewedArtifactStore,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS
from agents.novel_analysis.review_projection import (
    NOVEL_ANALYSIS_REVIEW_CONTRACT,
    NOVEL_ANALYSIS_REVIEW_REF_PREFIX,
    NovelAnalysisReviewProjection,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.json_values import canonical_json_digest


NOVEL_ANALYSIS_PUBLISHED_SCHEMA_VERSION = 4


class NovelAnalysisPublicationError(ValueError):
    code = "novel_analysis_publication_invalid"


class NovelAnalysisReplacementPublicationService:
    def __init__(self, db) -> None:
        self._db = db
        self._source_reviews = NovelAnalysisReviewProjection(db)
        self._reviews = NovelAnalysisReviewedArtifactStore(db)
        self._artifacts = SqliteArtifactRepository(db)

    async def review(
        self,
        *,
        source_artifact_id: str,
        command_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        artifact = await self._artifacts.load(source_artifact_id)
        if artifact is None:
            raise NovelAnalysisPublicationError("analysis Artifact does not exist")
        if artifact.namespace == NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE:
            source = await self.load_reviewed(source_artifact_id)
            original_id = str(source["sourceArtifactRef"]).removeprefix(
                NOVEL_ANALYSIS_REVIEW_REF_PREFIX
            )
        else:
            source = await self._source_reviews.load(source_artifact_id)
            original_id = str(source["artifactId"])
        await self._require_completed_task(str(source["taskId"]))
        reviewed = self._validate_review(source, payload)
        receipt = await self._reviews.commit(
            source_revision_id=str(source["sourceRevisionId"]),
            source_artifact_id=original_id,
            task_id=str(source["taskId"]),
            command_id=command_id,
            run_id=str(source["createdByRunId"]),
            payload=reviewed,
        )
        return {
            **reviewed,
            "artifactId": receipt.artifact_id,
            "artifactRef": receipt.resource_ref,
            "createdByRunId": source["createdByRunId"],
            "taskId": source["taskId"],
            "publicationSupported": True,
        }

    async def load_reviewed(self, artifact_id: str) -> dict[str, object]:
        artifact, payload = await self._reviews.load(artifact_id)
        source_id = str(artifact.metadata.get("sourceArtifactId") or "")
        source = await self._source_reviews.load(source_id)
        if (
            payload.get("sourceArtifactRef")
            != NOVEL_ANALYSIS_REVIEW_REF_PREFIX + source_id
            or payload.get("sourceRevisionId") != source.get("sourceRevisionId")
            or artifact.owner_id != source.get("sourceRevisionId")
            or artifact.created_by_run_id != source.get("createdByRunId")
        ):
            raise NovelAnalysisPublicationError(
                "reviewed analysis source lineage changed"
            )
        await self._require_completed_task(str(source["taskId"]))
        return {
            **payload,
            "artifactId": artifact.id,
            "artifactRef": NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact.id,
            "createdByRunId": artifact.created_by_run_id,
            "taskId": source["taskId"],
            "publicationSupported": True,
        }

    async def publish(self, artifact_id: str) -> dict[str, object]:
        reviewed = await self.load_reviewed(artifact_id)
        revision_id = str(reviewed["sourceRevisionId"])
        digest = canonical_json_digest({
            key: value
            for key, value in reviewed.items()
            if key not in {
                "artifactId",
                "artifactRef",
                "createdByRunId",
                "taskId",
                "publicationSupported",
            }
        })
        existing_id = None
        analysis_id = None
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT id FROM novel_source_analyses "
                "WHERE source_revision_id = ? AND content_digest = ?",
                [revision_id, digest],
            )
            if existing is not None:
                existing_id = str(existing["id"])
            else:
                analysis_id = "analysis_" + uuid4().hex
                latest = await self._db.fetch_one(
                    "SELECT COALESCE(MAX(version_no), 0) AS version_no "
                    "FROM novel_source_analyses WHERE source_revision_id = ?",
                    [revision_id],
                )
                ordinals = [
                    int(item["sectionOrdinal"])
                    for item in _all_evidence(reviewed)
                ]
                if not ordinals:
                    raise NovelAnalysisPublicationError(
                        "reviewed analysis has no publishable evidence"
                    )
                summary = {
                    "artifactId": artifact_id,
                    "artifactContract": NOVEL_ANALYSIS_REVIEW_CONTRACT,
                    "sourceArtifactRef": reviewed["sourceArtifactRef"],
                    "sectionIds": reviewed["sectionIds"],
                    "analysisTechniqueResult": reviewed["analysisTechniqueResult"],
                    "coverage": reviewed["coverage"],
                    "conflicts": reviewed["conflicts"],
                    "storyOverview": reviewed["storyOverview"],
                }
                await self._db.execute(
                    "INSERT INTO novel_source_analyses "
                    "(id, source_revision_id, version_no, coverage_end_ordinal, "
                    "schema_version, content_digest, summary_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        analysis_id,
                        revision_id,
                        int((latest or {}).get("version_no") or 0) + 1,
                        max(ordinals),
                        NOVEL_ANALYSIS_PUBLISHED_SCHEMA_VERSION,
                        digest,
                        _json(summary),
                    ],
                )
                for fact in reviewed["facts"]:
                    await self._insert_fact(analysis_id, fact)
                for card in reviewed["craftCards"]:
                    await self._insert_card(analysis_id, card)
        return await self._published(existing_id or str(analysis_id))

    def _validate_review(self, source, payload: Mapping[str, object]) -> dict:
        raw = dict(payload)
        evidence = {
            _evidence_key(item): item
            for item in _all_evidence(source)
        }
        source_fact_ids = {str(item["id"]) for item in source["facts"]}
        source_card_ids = {str(item["id"]) for item in source["craftCards"]}
        facts = [
            _review_fact(item, source_fact_ids, evidence)
            for item in _mapping_list(raw.get("facts"), "facts")
        ]
        cards = [
            _review_card(item, source_card_ids, evidence)
            for item in _mapping_list(raw.get("craftCards"), "craftCards")
        ]
        _unique((item["id"] for item in facts), "fact ids")
        _unique((item["id"] for item in cards), "craft card ids")
        overview = _review_overview(raw.get("storyOverview"), evidence)
        technique = _review_technique(
            raw.get("analysisTechniqueResult"),
            {str(item["id"]) for item in cards},
        )
        return {
            "analysisSchemaVersion": 1,
            "artifactContract": NOVEL_ANALYSIS_REVIEW_CONTRACT,
            "artifactKind": "novel_analysis_review",
            "sourceArtifactRef": str(
                source.get("sourceArtifactRef") or source["artifactRef"]
            ),
            "sourceRevisionId": str(source["sourceRevisionId"]),
            "sectionIds": list(source["sectionIds"]),
            "facts": facts,
            "craftCards": cards,
            "storyOverview": overview,
            "analysisTechniqueResult": technique,
            "coverage": dict(source["coverage"]),
            "conflicts": list(source["conflicts"]),
            "reviewStatus": "reviewed",
        }

    async def _require_completed_task(self, task_id: str) -> None:
        row = await self._db.fetch_one(
            "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        if row is None or row.get("status") != "completed":
            raise NovelAnalysisPublicationError(
                "only a completed analysis task can be reviewed or published"
            )

    async def _insert_fact(self, analysis_id: str, fact: Mapping) -> None:
        fact_id = "fact_" + uuid4().hex
        ordinals = [int(item["sectionOrdinal"]) for item in fact["evidence"]]
        content_digest = canonical_json_digest({
            key: value for key, value in fact.items() if key != "id"
        })
        await self._db.execute(
            "INSERT INTO novel_source_analysis_facts "
            "(id, analysis_id, fact_kind, subject_key, predicate, value_json, "
            "lifecycle_status, first_section_ordinal, last_section_ordinal, "
            "content_digest, claim_nature) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                fact_id,
                analysis_id,
                fact["factKind"],
                fact["subjectKey"],
                fact["predicate"],
                _json(fact.get("value")),
                fact["lifecycleStatus"],
                min(ordinals),
                max(ordinals),
                content_digest,
                fact["claimNature"],
            ],
        )
        await self._insert_evidence(analysis_id, "fact", fact_id, fact["evidence"])

    async def _insert_card(self, analysis_id: str, card: Mapping) -> None:
        card_id = "craft_" + uuid4().hex
        content_digest = canonical_json_digest({
            key: value for key, value in card.items() if key != "id"
        })
        await self._db.execute(
            "INSERT INTO novel_source_craft_cards "
            "(id, analysis_id, card_kind, title, body_markdown, metadata_json, "
            "status, content_digest) VALUES (?, ?, ?, ?, ?, '{}', 'verified', ?)",
            [
                card_id,
                analysis_id,
                card["cardKind"],
                card["title"],
                card["bodyMarkdown"],
                content_digest,
            ],
        )
        await self._insert_evidence(
            analysis_id, "craft_card", card_id, card["evidence"]
        )

    async def _insert_evidence(
        self, analysis_id: str, owner_type: str, owner_id: str, evidence
    ) -> None:
        for item in evidence:
            await self._db.execute(
                "INSERT INTO novel_source_analysis_evidence "
                "(id, analysis_id, owner_type, owner_id, section_id, excerpt, "
                "locator_json, excerpt_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    "evidence_" + uuid4().hex,
                    analysis_id,
                    owner_type,
                    owner_id,
                    item["sectionId"],
                    item["excerpt"],
                    _json(item["locator"]),
                    item["excerptDigest"],
                ],
            )

    async def _published(self, analysis_id: str) -> dict[str, object]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_analyses WHERE id = ?",
            [analysis_id],
        )
        if row is None:
            raise NovelAnalysisPublicationError("published analysis disappeared")
        summary = json.loads(str(row["summary_json"]))
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
        evidence_rows = await self._db.fetch_all(
            "SELECT e.*, s.ordinal AS section_ordinal, "
            "s.title AS section_title FROM novel_source_analysis_evidence e "
            "JOIN novel_source_sections s ON s.id = e.section_id "
            "WHERE e.analysis_id = ? ORDER BY e.id",
            [analysis_id],
        )
        evidence_by_owner: dict[tuple[str, str], list[dict]] = {}
        for item in evidence_rows:
            locator = json.loads(str(item["locator_json"]))
            evidence_by_owner.setdefault(
                (str(item["owner_type"]), str(item["owner_id"])), []
            ).append({
                "id": str(item["id"]),
                "sectionId": str(item["section_id"]),
                "sectionOrdinal": int(item["section_ordinal"]),
                "sectionTitle": str(item["section_title"]),
                "referenceKind": locator.get("referenceKind", "quote"),
                "excerpt": str(item["excerpt"]),
                "locator": locator,
                "excerptDigest": str(item["excerpt_digest"]),
            })
        return {
            "id": str(row["id"]),
            "sourceRevisionId": str(row["source_revision_id"]),
            "versionNo": int(row["version_no"]),
            "coverageEndOrdinal": int(row["coverage_end_ordinal"]),
            "schemaVersion": int(row["schema_version"]),
            "contentDigest": str(row["content_digest"]),
            "summary": summary,
            "storyOverview": summary.get("storyOverview"),
            "facts": [{
                "id": str(item["id"]),
                "factKind": str(item["fact_kind"]),
                "claimNature": str(item.get("claim_nature") or "fact"),
                "subjectKey": str(item["subject_key"]),
                "predicate": str(item["predicate"]),
                "value": json.loads(str(item["value_json"])),
                "lifecycleStatus": str(item["lifecycle_status"]),
                "firstSectionOrdinal": int(item["first_section_ordinal"]),
                "lastSectionOrdinal": int(item["last_section_ordinal"]),
                "contentDigest": str(item["content_digest"]),
                "evidence": evidence_by_owner.get(
                    ("fact", str(item["id"])), []
                ),
            } for item in facts],
            "craftCards": [{
                "id": str(item["id"]),
                "cardKind": str(item["card_kind"]),
                "title": str(item["title"]),
                "bodyMarkdown": str(item["body_markdown"]),
                "status": str(item["status"]),
                "contentDigest": str(item["content_digest"]),
                "evidence": evidence_by_owner.get(
                    ("craft_card", str(item["id"])), []
                ),
            } for item in cards],
            "createTime": row["create_time"],
        }


def _review_fact(value, allowed_ids, evidence) -> dict:
    raw = _mapping(value, "fact")
    fact_id = _allowed_id(raw.get("id"), allowed_ids, "fact id")
    kind = _text(raw.get("factKind"), "factKind", 100)
    if kind not in NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS:
        raise NovelAnalysisPublicationError("factKind is not publishable canon")
    return {
        "id": fact_id,
        "claimNature": _text(raw.get("claimNature") or "fact", "claimNature", 100),
        "factKind": kind,
        "subjectKey": _text(raw.get("subjectKey"), "subjectKey", 500),
        "predicate": _text(raw.get("predicate"), "predicate", 500),
        "value": raw.get("value"),
        "lifecycleStatus": _text(
            raw.get("lifecycleStatus") or "active", "lifecycleStatus", 100
        ),
        "evidence": _review_evidence(raw.get("evidence"), evidence),
    }


def _review_card(value, allowed_ids, evidence) -> dict:
    raw = _mapping(value, "craft card")
    return {
        "id": _allowed_id(raw.get("id"), allowed_ids, "craft card id"),
        "cardKind": _text(raw.get("cardKind"), "cardKind", 100),
        "title": _text(raw.get("title"), "title", 500),
        "bodyMarkdown": _text(raw.get("bodyMarkdown"), "bodyMarkdown", 20_000),
        "evidence": _review_evidence(raw.get("evidence"), evidence),
    }


def _review_overview(value, evidence) -> dict:
    raw = _mapping(value, "storyOverview")
    return {
        "summaryMarkdown": _text(
            raw.get("summaryMarkdown"), "summaryMarkdown", 20_000
        ),
        "evidence": _review_evidence(raw.get("evidence"), evidence),
    }


def _review_technique(value, allowed_ids: set[str]) -> dict:
    raw = _mapping(value, "analysisTechniqueResult")
    if raw.get("status") == "empty":
        return {
            "status": "empty",
            "reason": _text(raw.get("reason"), "technique reason", 2_000),
            "techniques": [],
        }
    if raw.get("status") != "generated":
        raise NovelAnalysisPublicationError("technique status is invalid")
    techniques = []
    for item in _mapping_list(raw.get("techniques"), "techniques"):
        ids = [str(value or "").strip() for value in item.get("observationIds", [])]
        if not ids or any(not value or value not in allowed_ids for value in ids):
            raise NovelAnalysisPublicationError(
                "technique references an unavailable reviewed craft card"
            )
        techniques.append({
            "title": _text(item.get("title"), "technique title", 500),
            "bodyMarkdown": _text(
                item.get("bodyMarkdown"), "technique body", 20_000
            ),
            "observationIds": ids,
        })
    if not techniques:
        raise NovelAnalysisPublicationError("generated techniques are empty")
    return {"status": "generated", "techniques": techniques}


def _review_evidence(value, allowed) -> list[dict]:
    result = []
    for raw in _mapping_list(value, "evidence"):
        start = raw.get("segmentStartCharacter")
        end = raw.get("segmentEndCharacter")
        excerpt = str(raw.get("excerpt") or "")
        if type(start) is not int or type(end) is not int or end <= start or not excerpt:
            raise NovelAnalysisPublicationError("review evidence locator is invalid")
        key = (
            str(raw.get("sectionId") or ""),
            start,
            end,
            "sha256:" + hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        )
        source = allowed.get(key)
        if source is None:
            raise NovelAnalysisPublicationError(
                "review evidence does not match the source Artifact"
            )
        result.append(dict(source))
    if not result:
        raise NovelAnalysisPublicationError("review evidence is required")
    return result


def _all_evidence(value) -> list[dict]:
    return [
        evidence
        for item in [
            *value.get("facts", []),
            *value.get("craftCards", []),
            value.get("storyOverview", {}),
        ]
        if isinstance(item, Mapping)
        for evidence in item.get("evidence", [])
        if isinstance(evidence, Mapping)
    ]


def _evidence_key(item: Mapping) -> tuple:
    return (
        str(item.get("sectionId") or ""),
        item.get("segmentStartCharacter"),
        item.get("segmentEndCharacter"),
        str(item.get("excerptDigest") or ""),
    )


def _mapping(value, name: str) -> dict:
    if not isinstance(value, Mapping):
        raise NovelAnalysisPublicationError(f"{name} must be an object")
    return dict(value)


def _mapping_list(value, name: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise NovelAnalysisPublicationError(f"{name} must be a list of objects")
    return [dict(item) for item in value]


def _text(value, name: str, limit: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise NovelAnalysisPublicationError(f"{name} is invalid")
    return normalized


def _allowed_id(value, allowed: set[str], name: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise NovelAnalysisPublicationError(f"{name} is outside the source review")
    return normalized


def _unique(values, name: str) -> None:
    values = tuple(values)
    if len(values) != len(set(values)):
        raise NovelAnalysisPublicationError(f"{name} must be unique")


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "NOVEL_ANALYSIS_PUBLISHED_SCHEMA_VERSION",
    "NovelAnalysisPublicationError",
    "NovelAnalysisReplacementPublicationService",
]
