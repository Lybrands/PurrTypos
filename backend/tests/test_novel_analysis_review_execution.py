from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.review_execution import (
    ScalableReviewExecutionError,
    ScalableReviewOutputError,
    ScalableReviewUnitExecutor,
)
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import (
    RunCreateParams,
    SqliteRunRepository,
)
from purra.json_values import canonical_json_digest
from purra.recovery import FailureCategory


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _commit(db, unit_id, run_id, payload):
    return await NovelAnalysisAttemptArtifactStore(db).commit(
        task_id="task",
        unit_id=unit_id,
        attempt=1,
        operation_id=f"task:{unit_id}:1",
        run_id=run_id,
        payload=payload,
    )


async def _context(db, *, covered=True):
    synthesis_payload = {
        "schemaVersion": 3,
        "kind": "synthesize",
        "childRunId": "synth-child",
        "inputArtifactIds": ["reduce-artifact"],
        "coveredSliceIds": ["slice-0", "slice-1"],
        "summaryMarkdown": "整书初稿总结",
        "facts": [
            {"id": "fact-1", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "人物", "predicate": "人物归纳", "value": {"name": "人物", "tags": "主角", "profile_md": "人物初稿"}, "lifecycleStatus": "active"},
            {"id": "fact-background", "claimNature": "summary", "factKind": "background", "subjectKey": "故事背景", "predicate": "背景归纳", "value": {"content": "旧城背景"}, "lifecycleStatus": "active"},
        ],
        "craftCards": [],
    }
    synthesis = await _commit(
        db, "synthesize:whole-work", "synth-child", synthesis_payload
    )
    coverage = await _commit(db, "coverage:gate", "root-run", {
        "schemaVersion": 1,
        "kind": "coverage",
        "covered": covered,
        "summaryMarkdownPresent": True,
        "expectedSliceIds": ["slice-0", "slice-1"],
        "expectedPassIds": ["story"],
        "synthesisArtifactId": synthesis.artifact_id,
        "synthesisDigest": canonical_json_digest(synthesis_payload),
        "passRoots": [],
    })
    skill_payload = {
        "schemaVersion": 1,
        "kind": "skill",
        "childRunId": "",
        "coverageArtifactId": coverage.artifact_id,
        "coverageDigest": canonical_json_digest(await NovelAnalysisAttemptArtifactStore(db).load_payload(coverage.artifact_id)),
        "synthesisArtifactId": synthesis.artifact_id,
        "synthesisDigest": canonical_json_digest(synthesis_payload),
        "techniqueResult": {
            "status": "insufficient_material", "candidate": None,
            "evidenceRefs": [], "scopeNotes": [], "reason": "未提炼技法",
        },
    }
    skill = await _commit(db, "skill:create", "root-run", skill_payload)
    return SimpleNamespace(
        task=SimpleNamespace(id="task"),
        unit=SimpleNamespace(
            id="review:publish",
            attempt=1,
            dependencies=("skill:create",),
            metadata={"unitKind": "review"},
        ),
        dependency_outputs={"skill:create": skill.resource_ref},
        run_id="root-run",
    )


@dataclass
class _ReviewRunner:
    db: object
    calls: int = 0

    async def run(self, *, scope, context, signal=None):
        del scope, signal
        self.calls += 1
        child_id = "review-child"
        await SqliteRunRepository(self.db).create(RunCreateParams(
            session_id=None,
            prompt="bounded review",
            mode="novel_analysis_review",
            requested_run_id=child_id,
            root_run_id=context.run_id,
            parent_run_id=context.run_id,
            agent_id=child_id,
        ))
        return child_id, {
            "summaryMarkdown": "审核后的整书总结",
            "facts": [
                {"id": "fact-1", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "人物", "predicate": "人物归纳", "value": {"name": "人物", "tags": "主角", "profile_md": "审核后人物分析"}, "lifecycleStatus": "active"},
                {"id": "fact-background", "claimNature": "summary", "factKind": "background", "subjectKey": "故事背景", "predicate": "背景归纳", "value": {"content": "审核后旧城背景"}, "lifecycleStatus": "active"},
            ],
            "craftCards": [],
        }


@pytest.mark.asyncio
async def test_review_commits_final_response_and_replays_without_model(db):
    await SqliteRunRepository(db).create(RunCreateParams(
        session_id=None,
        prompt="root",
        mode="analysis",
        requested_run_id="root-run",
        agent_id="root-run",
    ))
    context = await _context(db)
    runner = _ReviewRunner(db)
    executor = ScalableReviewUnitExecutor(db, child_runner=runner)

    result = await executor.execute(context)
    replay = await executor.execute(context)

    assert runner.calls == 1
    assert result.metadata["finalResponse"] == "审核后的整书总结"
    assert replay.output_ref == result.output_ref
    assert replay.validation_receipt["artifactReplayed"] is True


@pytest.mark.asyncio
async def test_review_rejects_failed_coverage_and_classifies_model_output(db):
    executor = ScalableReviewUnitExecutor(db, child_runner=_ReviewRunner(db))
    with pytest.raises(ScalableReviewExecutionError, match="successful Coverage"):
        await executor.execute(await _context(db, covered=False))

    output = executor.classify_failure(
        ScalableReviewOutputError("bad model output")
    )
    assert output.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert output.retryable is True
    assert executor.classify_failure(
        ScalableReviewExecutionError("bad lineage")
    ).retryable is False


@pytest.mark.asyncio
async def test_review_restart_recovers_previous_attempt_across_continuation_root(db):
    runs = SqliteRunRepository(db)
    for run_id in ("root-run", "continuation-root"):
        await runs.create(RunCreateParams(
            session_id=None,
            prompt=run_id,
            mode="analysis",
            requested_run_id=run_id,
            agent_id=run_id,
        ))
    context = await _context(db)
    runner = _ReviewRunner(db)
    executor = ScalableReviewUnitExecutor(db, child_runner=runner)
    first = await executor.execute(context)
    recovered_context = SimpleNamespace(
        task=context.task,
        unit=SimpleNamespace(
            id=context.unit.id,
            attempt=2,
            dependencies=context.unit.dependencies,
            metadata=context.unit.metadata,
            error_code="execution_recovery_after_restart",
        ),
        dependency_outputs=context.dependency_outputs,
        run_id="continuation-root",
    )

    recovered = await executor.execute(recovered_context)

    assert runner.calls == 1
    assert recovered.metadata["finalResponse"] == first.metadata["finalResponse"]
    assert recovered.validation_receipt["recoveredOperationId"].endswith(":1")
    assert recovered.output_ref != first.output_ref


@pytest.mark.asyncio
async def test_previous_attempt_is_not_reused_for_normal_model_retry(db):
    store = NovelAnalysisAttemptArtifactStore(db)
    await store.commit(
        task_id="task",
        unit_id="unit",
        attempt=1,
        operation_id="task:unit:1",
        run_id="root",
        payload={"value": "old model output"},
    )

    assert await store.try_load_execution_payload(
        task_id="task",
        unit_id="unit",
        attempt=2,
        error_code="novel_analysis_review_output_invalid",
    ) is None
    recovered = await store.try_load_execution_payload(
        task_id="task",
        unit_id="unit",
        attempt=2,
        error_code="execution_recovery_after_restart",
    )
    assert recovered == ({"value": "old model output"}, "task:unit:1")
