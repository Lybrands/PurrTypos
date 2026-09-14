from __future__ import annotations

import json

import pytest

from agents.novel_analysis.legacy_read_adapter import (
    NovelAnalysisLegacyReadAdapter,
)
from database.connection import DatabaseConnection
from exceptions import AppError
from schemas.novel_sources import ReviewNovelAnalysisRequest
from tests.support.novel_source_fixtures import (
    seed_legacy_analysis_artifact,
    seed_novel_source,
)


async def test_legacy_adapter_preserves_run_and_artifact_projection(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = await seed_novel_source(db)
        reference, payload = await seed_legacy_analysis_artifact(db, revision)
        await db.execute(
            "INSERT INTO ai_agent_long_task_runs "
            "(task_id, run_id, relation) VALUES (?, ?, ?)",
            ["analysis-task", "analysis-run", "created"],
        )
        await db.execute(
            "INSERT INTO ai_agent_long_task_units "
            "(task_id, unit_id, semantic_key, position, status, output_ref, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "analysis-task",
                "artifact:review",
                "artifact:review",
                0,
                "completed",
                reference,
                json.dumps({
                    "displayTitle": "形成待审核分析",
                    "unitKind": "build_review_artifact",
                }),
            ],
        )
        adapter = NovelAnalysisLegacyReadAdapter(db)
        runs = await adapter.list_for_revision(revision["id"])
        assert len(runs) == 1
        assert runs[0]["runId"] == "analysis-run"
        assert runs[0]["artifactRef"] == reference
        assert runs[0]["units"][0]["unitId"] == "artifact:review"
        assert runs[0]["units"][0]["status"] == "completed"
        artifact = await adapter.get_artifact(reference)
        assert artifact["artifactId"] == reference.removeprefix(
            "novel-analysis-artifact://"
        )
        assert artifact["createdByRunId"] == "analysis-run"
        assert artifact["facts"] == payload["facts"]
    finally:
        await db.close()


def test_legacy_adapter_has_no_execution_or_mutation_surface():
    public_methods = {
        name
        for name in dir(NovelAnalysisLegacyReadAdapter)
        if not name.startswith("_")
    }

    assert public_methods == {"get_artifact", "list_for_revision"}




async def test_legacy_artifact_review_and_publish_are_read_only(
    tmp_path,
    monkeypatch,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = await seed_novel_source(db)
        reference, _ = await seed_legacy_analysis_artifact(db, revision)
        artifact_id = reference.removeprefix("novel-analysis-artifact://")
        before = await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts"
        )
        import routers.novel_sources as router

        monkeypatch.setattr(router, "get_db", lambda: db)
        body = ReviewNovelAnalysisRequest(facts=[], craftCards=[])
        with pytest.raises(AppError, match="旧版小说分析仅供查看"):
            await router.review_analysis_artifact(
                artifact_id,
                body,
                "legacy-review-command",
            )
        with pytest.raises(AppError, match="旧版小说分析仅供查看"):
            await router.publish_analysis_artifact(artifact_id)
        after = await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts"
        )
        assert after == before
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM novel_source_analyses"
        ) == {"count": 0}
    finally:
        await db.close()
