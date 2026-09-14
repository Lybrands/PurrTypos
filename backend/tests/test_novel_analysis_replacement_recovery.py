from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.composition import (
    create_isolated_novel_analysis_replacement_composition,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
from agents.novel_analysis.executor import NovelAnalysisReplacementUnitExecutor
from agents.novel_analysis.profile import NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
from agents.novel_analysis.unit_schema import validate_model_unit_output
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionPlan,
    MessageRole,
    ModelRequest,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskStep,
)
from purra.model_protocol import generic_capability_snapshot
from purra.errors import ModelGatewayError


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
    await create_run(
        db,
        run_id="analysis-root",
        session_id=None,
        prompt="分析这部小说",
        mode="novel_analysis",
    )
    try:
        yield db
    finally:
        await db.close()


class DeterministicAnalysisRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, **request):
        kind = request["kind"].value
        self.calls.append(kind)
        if kind == "extract":
            segment = request["scope"].segments[0]
            return _extract_payload(
                segment_id=segment.id,
                start=segment.start_character,
                end=segment.end_character,
            )
        dependencies = request["dependency_payloads"]
        dependency = dependencies[0]
        if kind == "normalize":
            facts = []
            for item in dependencies:
                for value in item["facts"]:
                    fact = dict(value)
                    fact.pop("factId")
                    facts.append(fact)
            observations = []
            for value in request["observations"]:
                observation = dict(value)
                observation_id = observation.pop("observationId")
                observations.append({
                    **observation,
                    "mergedObservationIds": [observation_id],
                })
            return {
                "facts": facts,
                "observations": observations,
            }
        if kind == "overview":
            references = [
                reference
                for fact in dependency["facts"]
                for reference in fact["evidenceRefs"]
            ]
            return {"storyOverview": {
                "summaryMarkdown": "潮水淹没旧城。",
                "evidenceRefs": references,
            }}
        if kind == "distill_technique":
            return {"techniqueResult": {
                "status": "empty",
                "reason": "材料不足以形成独立技法。",
            }}
        raise AssertionError(f"unexpected model Unit: {kind}")


@pytest.mark.asyncio
async def test_progress_observer_failure_preserves_completed_business_unit(
    temp_db,
) -> None:
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    runner = DeterministicAnalysisRunner()
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, runner)

        async def fail_after_first_completion(update):
            if int(update.event.payload.get("completedUnits") or 0) >= 1:
                raise RuntimeError("presentation observer failed")

        with pytest.raises(RuntimeError, match="presentation observer failed"):
            await dispatcher.execute(
                task_id,
                run_id="analysis-root",
                observer=fail_after_first_completion,
            )

        interrupted = await composition.long_task_repository.list_units(task_id)
        assert interrupted[0].status.value == "completed"
        assert interrupted[0].output_ref.startswith("novel-analysis-v1://")
        assert all(item.status.value != "failed" for item in interrupted)
        task = await composition.long_task_repository.load(task_id)
        assert task.status.value == "paused"

        await composition.long_task_repository.resume(task_id)

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="analysis-root",
            observer=observe,
        )
        assert result.status.value == "completed"
        assert runner.calls.count("extract") == 1
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_restart_reconciles_finalized_attempt_without_model_recall(
    temp_db,
) -> None:
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    runner = DeterministicAnalysisRunner()
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, runner)
        repository = composition.long_task_repository
        task = await repository.load(task_id)
        task = await repository.start(task_id, expected_revision=task.revision)
        unit = await repository.claim_ready_unit(
            task_id,
            worker_id="crashed-worker",
            lease_duration_ms=30_000,
        )
        assert unit is not None
        await NovelAnalysisAttemptArtifactStore(temp_db).commit(
            task_id=task_id,
            unit_id=unit.id,
            attempt=unit.attempt,
            operation_id=f"{task_id}:{unit.id}:{unit.attempt}",
            run_id="analysis-root",
            payload=validate_model_unit_output("extract", _extract_payload()),
        )

        assert await repository.recover_after_restart() == (task_id,)
        recovered = (await repository.list_units(task_id))[0]
        assert recovered.status.value == "pending"
        assert recovered.error_code == "execution_recovery_after_restart"
        await repository.resume(task_id)

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="analysis-root",
            observer=observe,
        )
        units = await repository.list_units(task_id)
        assert result.status.value == "completed"
        assert runner.calls.count("extract") == 0
        assert units[0].attempt == 2
        assert units[0].validation_receipt["operationId"].endswith(":2")
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_transient_provider_failure_retries_the_unit_and_completes(
    temp_db,
) -> None:
    stable = DeterministicAnalysisRunner()

    class FailExtractOnce:
        def __init__(self) -> None:
            self.extract_attempts = 0

        async def run(self, **request):
            if request["kind"].value == "extract":
                self.extract_attempts += 1
                if self.extract_attempts == 1:
                    raise ModelGatewayError(
                        "provider is busy",
                        code="provider_capacity_limited",
                        retryable=True,
                    )
            return await stable.run(**request)

    flaky = FailExtractOnce()
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, flaky)

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="analysis-root",
            observer=observe,
        )
        units = await composition.long_task_repository.list_units(task_id)

        assert result.status.value == "completed"
        assert flaky.extract_attempts == 2
        assert units[0].attempt == 2
        assert units[0].error_code is None
        assert stable.calls.count("extract") == 1
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_two_source_segments_complete_parallel_extract_and_full_coverage(
    temp_db,
) -> None:
    runner = DeterministicAnalysisRunner()
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        dispatcher, task_id = await _dispatch(
            composition,
            temp_db,
            runner,
            request=_request(two_segments=True),
        )

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="analysis-root",
            observer=observe,
        )
        final_id = result.final_response.split("://", 1)[1]
        final = await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(
            final_id
        )

        assert result.status.value == "completed"
        assert runner.calls.count("extract") == 2
        assert final["coverageReport"]["expectedSegmentIds"] == [
            "segment-1a",
            "segment-1b",
        ]
        assert final["coverageReport"]["missingSegmentIds"] == []
    finally:
        await composition.shutdown()


async def _dispatch(composition, db, runner, *, request=None):
    profile = composition.profile(NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID)
    request = await profile.prepare_request(request or _request())
    plan = ExecutionPlan(
        title="分析并复核",
        task_spec=TaskSpec(goal="形成可审核分析", operation="analyze"),
        steps=(TaskStep(
            id="analysis",
            title="分析小说",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),),
    )
    decision = await profile.evaluate(request, plan)
    dispatcher = profile.create_long_task_dispatcher(
        long_task_repository=composition.long_task_repository,
        executor=NovelAnalysisReplacementUnitExecutor(db, model_runner=runner),
    )
    receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        run_id="analysis-root",
    )
    return dispatcher, receipt.task_id


def _request(*, two_segments: bool = False) -> AgentRunRequest:
    segments = (
        [{
            "id": "segment-1a",
            "sectionId": "section-1",
            "sectionDigest": "section-digest",
            "sectionOrdinal": 0,
            "startCharacter": 0,
            "endCharacter": 50,
        }, {
            "id": "segment-1b",
            "sectionId": "section-1",
            "sectionDigest": "section-digest",
            "sectionOrdinal": 0,
            "startCharacter": 50,
            "endCharacter": 100,
        }]
        if two_segments
        else [{
            "id": "segment-1",
            "sectionId": "section-1",
            "sectionDigest": "section-digest",
            "sectionOrdinal": 0,
            "startCharacter": 0,
            "endCharacter": 100,
        }]
    )
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "分析这部小说"),),
        model=ModelRequest(
            provider="test",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
            ),
        ),
        domain_context=DomainContext(
            namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "sourceRevisionId": "revision-1",
                "commandId": "command-1",
                "segments": segments,
            },
        ),
    )


def _extract_payload(
    *,
    segment_id: str = "segment-1",
    start: int = 0,
    end: int = 100,
) -> dict[str, object]:
    reference = {
        "segmentId": segment_id,
        "sourceSpanId": f"S{start}-{end}",
    }
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
