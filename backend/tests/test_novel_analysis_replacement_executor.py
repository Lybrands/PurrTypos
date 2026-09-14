from __future__ import annotations

from dataclasses import replace
import asyncio

import pytest
import pytest_asyncio

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.executor import NovelAnalysisReplacementUnitExecutor
from agents.novel_analysis.recipe import AnalysisSegment, compile_analysis_recipe
from agents.novel_analysis.unit_schema import (
    NovelAnalysisModelOutputError,
    validate_model_unit_output,
)
from agents.novel_analysis.submission_tool import SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
from database.connection import DatabaseConnection
from purra.errors import ModelGatewayError
from purra.cancellation import OperationCanceled
from purra.long_tasks import (
    DurableUnitExecutionContext,
    LongTaskRecord,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitStatus,
)


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '测试小说', 'text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, 100, 100)"
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) "
        "VALUES ('section-1', 'revision-1', 0, '第一章', ?, 'section-digest')",
        ["潮水漫过旧城。" + "风" * 93],
    )
    try:
        yield db
    finally:
        await db.close()


def _segment():
    return AnalysisSegment(
        id="segment-1",
        section_id="section-1",
        section_digest="section-digest",
        section_ordinal=0,
        start_character=0,
        end_character=100,
    )


class _ModelRunner:
    def __init__(self, *, invalid_span=False) -> None:
        self.calls = []
        self.invalid_span = invalid_span

    async def run(self, **request):
        self.calls.append(request)
        kind = request["kind"].value
        reference = {
            "segmentId": "segment-1",
            "sourceSpanId": "S1-99" if self.invalid_span else "S0-100",
        }
        if kind == "extract":
            return {
                "facts": [{
                    "factKind": "event",
                    "subjectKey": "旧城",
                    "predicate": "被潮水淹没",
                    "value": True,
                    "evidenceRefs": [reference],
                }],
                "observations": [{
                    "cardKind": "pacing",
                    "title": "潮水倒计时",
                    "bodyMarkdown": "用潮位形成时间压力。",
                    "evidenceRefs": [reference],
                }],
            }
        dependency = request["dependency_payloads"][0]
        if kind == "normalize":
            fact = dict(dependency["facts"][0])
            fact.pop("factId")
            observation = dict(request["observations"][0])
            observation_id = observation.pop("observationId")
            return {
                "facts": [fact],
                "observations": [{
                    **observation,
                    "mergedObservationIds": [observation_id],
                }],
            }
        if kind == "overview":
            return {"storyOverview": {
                "summaryMarkdown": "潮水逼近旧城，形成持续危机。",
                "evidenceRefs": [reference],
            }}
        if kind == "distill_technique":
            return {"techniqueResult": {
                "status": "empty",
                "reason": "当前材料不足以形成可复用技法。",
            }}
        raise AssertionError(f"unexpected model Unit: {kind}")


def _task(recipe):
    return LongTaskRecord(
        id="task-1",
        namespace="purrtypos.novel_analysis",
        kind=recipe.kind,
        owner_id="revision-1",
        created_by_run_id="run-1",
        status=LongTaskStatus.RUNNING,
        revision=1,
        total_units=len(recipe.steps),
        completed_units=0,
        failed_units=0,
        max_parallelism=recipe.max_parallelism,
        metadata={
            "sourceRevisionId": "revision-1",
            "commandId": "command-1",
            "segments": [_segment().to_mapping()],
        },
    )


def _unit(task, step):
    return LongTaskUnitRecord(
        task_id=task.id,
        id=step.id,
        position=0,
        status=LongTaskUnitStatus.RUNNING,
        dependencies=step.depends_on,
        attempt=0,
        max_attempts=step.max_attempts,
        metadata={**dict(step.metadata), "unitKind": step.kind},
    )


@pytest.mark.asyncio
async def test_executor_completes_full_recipe_with_required_review(temp_db) -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(_segment(),),
        plan_step_ids=("analyze",),
    )
    task = _task(recipe)
    model = _ModelRunner()
    executor = NovelAnalysisReplacementUnitExecutor(
        temp_db,
        model_runner=model,
    )
    outputs = {}

    for step in recipe.steps:
        result = await executor.execute(DurableUnitExecutionContext(
            task=task,
            unit=_unit(task, step),
            run_id="run-1",
            dependency_outputs={item: outputs[item] for item in step.depends_on},
        ))
        outputs[step.id] = result.output_ref

    final_id = outputs["review:artifact"].split("://", 1)[1]
    final = await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(final_id)
    assert final["kind"] == "review"
    assert final["reviewStatus"] == "pending_review"
    assert final["storyOverview"]["summaryMarkdown"].startswith("潮水逼近")
    assert final["coverageReport"]["missingSegmentIds"] == []
    assert final["techniqueResult"]["status"] == "empty"
    assert [call["kind"].value for call in model.calls] == [
        "extract", "normalize", "overview", "distill_technique",
    ]
    assert model.calls[0]["enabled_tool_names"] == (
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    )
    assert model.calls[1]["enabled_tool_names"] == (
        "listAnalysisObservations",
        "readAnalysisObservations",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    )


@pytest.mark.asyncio
async def test_invalid_source_handle_is_retryable_model_failure(temp_db) -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(_segment(),),
        plan_step_ids=("analyze",),
    )
    step = recipe.steps[0]
    task = _task(recipe)
    executor = NovelAnalysisReplacementUnitExecutor(
        temp_db,
        model_runner=_ModelRunner(invalid_span=True),
    )

    with pytest.raises(ModelGatewayError) as caught:
        await executor.execute(DurableUnitExecutionContext(
            task=task,
            unit=_unit(task, step),
            run_id="run-1",
            dependency_outputs={},
        ))
    assert caught.value.code == "novel_analysis_evidence_invalid"
    assert caught.value.retryable is True


def test_structured_output_shape_failure_is_retryable() -> None:
    with pytest.raises(NovelAnalysisModelOutputError) as caught:
        validate_model_unit_output("overview", {"storyOverview": {}})
    assert caught.value.code == "novel_analysis_model_output_invalid"
    assert caught.value.retryable is True
    with pytest.raises(NovelAnalysisModelOutputError, match="publishable canon"):
        validate_model_unit_output("extract", {
            "facts": [{
                "factKind": "invented_kind",
                "subjectKey": "未知",
                "predicate": "未知",
                "value": True,
                "evidenceRefs": [{
                    "segmentId": "segment-1",
                    "sourceSpanId": "S0-100",
                }],
            }],
            "observations": [],
        })


@pytest.mark.asyncio
async def test_interrupted_submitted_attempt_is_reconciled_without_model_recall(
    temp_db,
) -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(_segment(),),
        plan_step_ids=("analyze",),
    )
    step = recipe.steps[0]
    task = _task(recipe)
    payload = validate_model_unit_output("extract", {
        "facts": [{
            "factKind": "event",
            "subjectKey": "旧城",
            "predicate": "被潮水淹没",
            "value": True,
            "evidenceRefs": [{
                "segmentId": "segment-1",
                "sourceSpanId": "S0-100",
            }],
        }],
        "observations": [],
    })
    await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        task_id=task.id,
        unit_id=step.id,
        attempt=1,
        operation_id=f"{task.id}:{step.id}:1",
        run_id="run-1",
        payload=payload,
    )

    class MustNotRun:
        async def run(self, **request):
            raise AssertionError(f"model was recalled: {request}")

    unit = replace(
        _unit(task, step),
        attempt=2,
        error_code="execution_interrupted",
    )
    result = await NovelAnalysisReplacementUnitExecutor(
        temp_db,
        model_runner=MustNotRun(),
    ).execute(DurableUnitExecutionContext(
        task=task,
        unit=unit,
        run_id="run-1",
        dependency_outputs={},
    ))

    assert result.output_ref.startswith("novel-analysis-v1://")
    assert result.validation_receipt["operationId"].endswith(":2")
    assert result.validation_receipt["artifactReplayed"] is False


def test_executor_classifies_model_and_provider_failures_for_durable_recovery(
    temp_db,
) -> None:
    executor = NovelAnalysisReplacementUnitExecutor(temp_db, model_runner=None)
    invalid = executor.classify_failure(NovelAnalysisModelOutputError("bad"))
    missing = executor.classify_failure(ModelGatewayError(
        "not submitted",
        code="novel_analysis_result_not_submitted",
        retryable=True,
    ))
    provider = executor.classify_failure(ModelGatewayError(
        "busy",
        code="provider_capacity_limited",
        retryable=True,
    ))
    invariant = executor.classify_failure(ValueError("host contract changed"))

    assert invalid.category.value == "model_output_invalid"
    assert invalid.retryable is True
    assert missing.category.value == "model_output_invalid"
    assert missing.retryable is True
    assert provider.category.value == "transient_provider"
    assert provider.scope.value == "systemic"
    assert invariant.category.value == "business_invariant"
    assert invariant.retryable is False


@pytest.mark.asyncio
async def test_cancellation_before_model_call_leaves_no_attempt_artifact(
    temp_db,
) -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(_segment(),),
        plan_step_ids=("analyze",),
    )
    step = recipe.steps[0]
    task = _task(recipe)

    class MustNotRun:
        async def run(self, **request):
            raise AssertionError(f"model was called after cancellation: {request}")

    signal = asyncio.Event()
    signal.set()
    with pytest.raises(OperationCanceled):
        await NovelAnalysisReplacementUnitExecutor(
            temp_db,
            model_runner=MustNotRun(),
        ).execute(DurableUnitExecutionContext(
            task=task,
            unit=_unit(task, step),
            run_id="run-1",
            dependency_outputs={},
        ), signal=signal)

    assert await NovelAnalysisAttemptArtifactStore(
        temp_db
    ).try_load_operation_payload(
        task_id=task.id,
        operation_id=f"{task.id}:{step.id}:0",
    ) is None


def test_normalize_must_account_for_every_input_observation() -> None:
    with pytest.raises(NovelAnalysisModelOutputError, match="every input"):
        validate_model_unit_output(
            "normalize",
            {"facts": [], "observations": [{
                "cardKind": "pacing",
                "title": "只合并一项",
                "bodyMarkdown": "遗漏另一项。",
                "evidenceRefs": [{
                    "segmentId": "segment-1",
                    "sourceSpanId": "S0-100",
                }],
                "mergedObservationIds": ["observation-1"],
            }]},
            allowed_observation_ids=frozenset({
                "observation-1", "observation-2",
            }),
        )
