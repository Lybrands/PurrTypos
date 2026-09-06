from __future__ import annotations

import pytest

from application.writing_method_candidates import WritingMethodCandidateService
from database.connection import DatabaseConnection
from domains.writing.methods import WritingMethodConflictError


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    import json
    from tests.support.writing_distillation import report
    observations = [{"contentDigest": "digest-card-1", "evidence": [{"excerpt": "原文秘密句。"}]}]
    await connection.execute("INSERT INTO novel_source_analyses (id, source_revision_id, version_no, coverage_end_ordinal, schema_version, content_digest, summary_json) VALUES ('analysis-candidate', 'source-rev-1', 1, 0, 2, 'analysis-digest', ?)", [json.dumps({"distillation": report(observations)})])
    await connection.execute("INSERT INTO novel_source_craft_cards (id, analysis_id, card_kind, title, body_markdown, metadata_json, status, content_digest) VALUES ('card-1', 'analysis-candidate', '信息释放', '观察', '原文秘密句。', '{}', 'verified', 'digest-card-1')")
    await connection.execute("INSERT INTO novel_source_analysis_evidence (id, analysis_id, owner_type, owner_id, section_id, excerpt, locator_json, excerpt_digest) VALUES ('evidence-card-1', 'analysis-candidate', 'craft_card', 'card-1', 'section-1', '原文秘密句。', '{}', 'excerpt-digest')")
    try:
        yield connection
    finally:
        await connection.close()


async def test_verified_cards_create_reviewable_drafts_without_copying_evidence(db):
    batch = await WritingMethodCandidateService(db).create_from_analysis(
        "analysis-candidate"
    )
    assert batch["bindingChanged"] is False
    assert len(batch["methods"]) == 1
    first = batch["methods"][0]
    assert first["source_ref"]["craftCardIds"] == ["card-1"]
    assert first["source_ref"]["analysisDigest"] == "analysis-digest"
    assert "原文秘密句。" not in first["draft_markdown"]
    assert "## 执行方法" in first["draft_markdown"]
    assert first["method_type"] == "primary"
    assert batch["scheme"]["source_ref"]["candidateMethodIds"] == [
        item["id"] for item in batch["methods"]
    ]


async def test_legacy_fragment_cards_cannot_be_published_as_a_writing_skill(db):
    before = await db.fetch_one("SELECT COUNT(*) AS count FROM writing_methods")
    await db.execute(
        "UPDATE novel_source_analyses SET schema_version = 1 WHERE id = 'analysis-candidate'"
    )

    with pytest.raises(WritingMethodConflictError, match="重新分析"):
        await WritingMethodCandidateService(db).create_from_analysis(
            "analysis-candidate"
        )

    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM writing_methods"
    ) == before


async def test_candidate_publish_is_atomic_and_never_binds_a_book(db):
    service = WritingMethodCandidateService(db)
    batch = await service.create_from_analysis("analysis-candidate")
    method_ids = [item["id"] for item in batch["methods"]]
    published = await service.publish_batch(batch["scheme"]["id"], method_ids=method_ids)
    assert len(published["methodRevisions"]) == 1
    assert len(published["schemeRevision"]["members"]) == 1
    assert published["schemeRevision"]["metadata"]["sourceRef"]["analysisId"] == (
        "analysis-candidate"
    )
    assert published["bindingChanged"] is False
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM book_writing_method_bindings"
    ) == {"count": 0}

    before = published["methodRevisions"][0]["markdown_body"]
    await db.execute(
        "UPDATE novel_source_craft_cards SET body_markdown = '分析后来改变' WHERE id = 'card-1'"
    )
    revision = await db.fetch_one(
        "SELECT markdown_body FROM writing_method_revisions WHERE id = ?",
        [published["methodRevisions"][0]["id"]],
    )
    assert revision["markdown_body"] == before


async def test_publish_failure_rolls_back_every_candidate_revision(db, monkeypatch):
    service = WritingMethodCandidateService(db)
    batch = await service.create_from_analysis("analysis-candidate")
    method_ids = [item["id"] for item in batch["methods"]]

    async def fail(_scheme_id):
        raise RuntimeError("scheme publish failed")

    monkeypatch.setattr(service._methods, "publish_scheme", fail)
    with pytest.raises(RuntimeError, match="scheme publish failed"):
        await service.publish_batch(batch["scheme"]["id"], method_ids=method_ids)
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM writing_method_revisions WHERE method_id = ?",
        method_ids,
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM writing_scheme_revisions WHERE scheme_id = ?",
        [batch["scheme"]["id"]],
    ) == {"count": 0}


async def test_deleting_candidate_draft_does_not_delete_analysis_evidence(db):
    service = WritingMethodCandidateService(db)
    batch = await service.create_from_analysis(
        "analysis-candidate"
    )
    await service._methods.delete_method(batch["methods"][0]["id"])
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_analysis_evidence "
        "WHERE id = 'evidence-card-1'"
    ) == {"count": 1}


async def test_distilled_method_is_injected_after_explicit_binding_without_keyword_match(db):
    from domains.writing.method_resolution import resolve_writing_methods
    service = WritingMethodCandidateService(db)
    batch = await service.create_from_analysis('analysis-candidate')
    replayed = await service.create_from_analysis('analysis-candidate')
    assert replayed['scheme']['id'] == batch['scheme']['id']
    published = await service.publish_batch(batch['scheme']['id'], method_ids=[batch['methods'][0]['id']])
    await db.execute("INSERT INTO books(id,title) VALUES('target','新作品')")
    await service._methods.bind_book_revision(book_id='target', binding_type='scheme', revision_id=published['schemeRevision']['id'])
    snapshot = await service._methods.resolve_book_binding_snapshot('target')
    resolved = resolve_writing_methods(snapshot, task=None, token_budget=8000)
    assert resolved['usedRevisionIds'] == [published['methodRevisions'][0]['id']]
    assert '执行方法' in resolved['content']
    assert '原文秘密句' not in resolved['content']
