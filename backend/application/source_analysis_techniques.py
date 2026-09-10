"""Durable source-result identities; file publication remains authoritative."""
import asyncio
import json
from domains.writing.techniques import TechniqueError


async def register(db, analysis_id, ref, stage):
    row = await db.fetch_one("SELECT a.source_revision_id,a.coverage_end_ordinal,r.work_id FROM novel_source_analyses a JOIN novel_source_revisions r ON r.id=a.source_revision_id WHERE a.id=?", [analysis_id])
    if not row:
        raise TechniqueError("invalid_reference", "来源分析不存在")
    await db.execute("INSERT INTO source_analysis_techniques VALUES (?,?,?,?,?,?,?) ON CONFLICT(analysis_id,technique_id,version_id) DO UPDATE SET stage=excluded.stage",
        [analysis_id, row["work_id"], row["source_revision_id"], row["coverage_end_ordinal"], ref["id"], ref["versionId"], stage])


async def register_published(service, object_id):
    record = await asyncio.to_thread(service.techniques.get_record, object_id)
    for draft_id in record["draftIds"]:
        draft = await asyncio.to_thread(service.techniques.get_draft, object_id, draft_id)
        ref = draft.get("sealedRef")
        analysis_id = draft.get("owner", {}).get("analysisId")
        if analysis_id and await service.db.fetch_one("SELECT id FROM novel_source_analyses WHERE id=?", [analysis_id]) and ref and ref["versionId"] in record.get("publishedVersions", []):
            await register(service.db, analysis_id, ref, "published")


async def results(db, analysis_id, fork_ordinal=None, *, include_candidates=False):
    from application.writing_technique_access import WritingTechniqueAccess
    access = WritingTechniqueAccess(db)
    # Recover a publication whose file commit completed before the SQL receipt.
    for record in await access.library.list_objects("technique", include_archived=True):
        await register_published(access.library, record["id"])
    rows = await db.fetch_all("SELECT * FROM source_analysis_techniques WHERE analysis_id=? ORDER BY technique_id,version_id", [analysis_id])
    preferred = {}
    for row in rows:
        if row["stage"] == "published":
            try:
                record = await asyncio.to_thread(access.library.techniques.get_record, row["technique_id"])
                versions = record.get("publishedVersions", [])
                rank = versions.index(row["version_id"]) if row["version_id"] in versions else -1
                if rank > preferred.get(row["technique_id"], (-2, ""))[0]:
                    preferred[row["technique_id"]] = (rank, row["version_id"])
            except TechniqueError:
                pass
    analysis = await db.fetch_one("SELECT summary_json FROM novel_source_analyses WHERE id=?", [analysis_id])
    summary = json.loads(analysis["summary_json"]) if analysis else {}
    values = []
    for row in rows:
        ref = {"kind": "technique", "id": row["technique_id"], "versionId": row["version_id"]}
        item = {"ref": ref, "stage": row["stage"], "available": False, "coverageEndOrdinal": row["coverage_end_ordinal"], "sectionIds": summary.get("sectionIds", []), "coverage": summary.get("coverage", {})}
        if row["stage"] == "published":
            try:
                expanded = await access._expand(ref, verify_files=True)
                item.update(name=expanded["metadata"]["name"], available=(fork_ordinal is None or row["coverage_end_ordinal"] <= fork_ordinal) and preferred.get(row["technique_id"], (None, None))[1] == row["version_id"])
                if not item["available"]:
                    item["reason"] = "技法分析范围超过分叉点" if fork_ordinal is not None and row["coverage_end_ordinal"] > fork_ordinal else "此分析已有较新的可用版本"
            except TechniqueError as error:
                item["reason"] = str(error)
        elif row["stage"] == "candidate":
            try:
                record = await asyncio.to_thread(access.library.techniques.get_record, ref["id"])
                manifest = await asyncio.to_thread(access.library.techniques.get_version_manifest, ref, verify_files=True)
                item["name"] = manifest["metadata"]["name"]
                item["available"] = include_candidates and record["status"] == "active" and (fork_ordinal is None or row["coverage_end_ordinal"] <= fork_ordinal) and ref["id"] not in preferred
                if not item["available"]:
                    item["reason"] = "候选，需先保存到技法库" if not include_candidates else "已有已发布版本或分析范围超过分叉点，候选不继承"
            except TechniqueError as error:
                item["reason"] = str(error)
        values.append(item)
    return values
