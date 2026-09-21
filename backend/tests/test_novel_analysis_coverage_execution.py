from types import SimpleNamespace

import pytest

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.coverage_execution import ScalableCoverageError, ScalableCoverageUnitExecutor
from database.connection import DatabaseConnection


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _commit(db, unit_id, payload):
    return await NovelAnalysisAttemptArtifactStore(db).commit(
        task_id="task", unit_id=unit_id, attempt=1,
        operation_id=f"task:{unit_id}:1", run_id="root", payload=payload,
    )


async def _context(db, *, summary="整书总结", expected_slices=("s0", "s1")):
    root = await _commit(db, "reduce:story", {
        "schemaVersion": 1, "kind": "reduce", "passId": "story",
        "reduceLevel": 1, "childRunId": "child", "inputArtifactIds": ["a", "b"],
        "coveredSliceIds": ["s0", "s1"], "findings": [], "conflicts": [],
    })
    synthesis = await _commit(db, "synthesize:whole-work", {
        "schemaVersion": 3, "kind": "synthesize", "childRunId": "synth-child",
        "inputArtifactIds": [root.artifact_id], "coveredSliceIds": ["s0", "s1"],
        "summaryMarkdown": summary,
        "facts": [{"id": "fact-1", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "人物", "predicate": "人物归纳", "value": "内容", "lifecycleStatus": "active"}],
        "craftCards": [],
    })
    return SimpleNamespace(
        task=SimpleNamespace(id="task"),
        unit=SimpleNamespace(
            id="coverage:gate", attempt=1,
            dependencies=("synthesize:whole-work",),
            metadata={
                "unitKind": "coverage", "expectedSliceIds": list(expected_slices),
                "expectedPassIds": ["story"], "qualityChecks": ["完整覆盖"],
                "deterministic": True,
            },
        ),
        dependency_outputs={"synthesize:whole-work": synthesis.resource_ref},
        run_id="root",
    )


@pytest.mark.asyncio
async def test_coverage_gate_commits_deterministic_receipt_and_replays(db):
    context = await _context(db)
    executor = ScalableCoverageUnitExecutor(db)
    result = await executor.execute(context)
    replay = await executor.execute(context)

    assert result.validation_receipt["coveredSliceCount"] == 2
    assert replay.output_ref == result.output_ref
    assert replay.validation_receipt["artifactReplayed"] is True


@pytest.mark.asyncio
async def test_coverage_gate_rejects_missing_slice_and_empty_summary(db, tmp_path):
    with pytest.raises(ScalableCoverageError, match="incomplete"):
        await ScalableCoverageUnitExecutor(db).execute(
            await _context(db, expected_slices=("s0", "s1", "s2"))
        )

    other = DatabaseConnection(tmp_path / "empty-summary")
    await other.init()
    try:
        with pytest.raises(ScalableCoverageError, match="incomplete"):
            await ScalableCoverageUnitExecutor(other).execute(
                await _context(other, summary="")
            )
    finally:
        await other.close()
