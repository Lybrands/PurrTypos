from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore
from agents.screenplay.contracts import ScreenplayPartOperationScope
from agents.screenplay.executor import (
    ScreenplayReplacementUnitExecutor,
    _operation_scope,
)
from agents.screenplay.output_contract import ScreenplayPartOutputEvidence
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.errors import ModelGatewayError
from purra.recovery import FailureCategory


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await create_run(
        db,
        run_id="run-screenplay-unit",
        session_id=None,
        prompt="screenplay unit",
        mode="screenplay",
    )
    try:
        yield db
    finally:
        await db.close()


def _context(*, attempt=1, error_code=None):
    return SimpleNamespace(
        run_id="run-screenplay-unit",
        task=SimpleNamespace(
            id="task-screenplay",
            metadata={"projectId": "project-1", "targetRole": "creativeBrief"},
        ),
        unit=SimpleNamespace(
            id="unit-premise",
            attempt=attempt,
            error_code=error_code,
            metadata={
                "semanticKey": "section:premise",
                "partKind": "document_section",
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "dependencyPartKeys": [],
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            },
        ),
        dependency_outputs={},
    )


def _payload(text):
    return {
        "schemaVersion": 1,
        "partKind": "document_section",
        "partKey": "section:premise",
        "targetRole": "creativeBrief",
        "payload": {"content": text},
    }


@pytest.mark.asyncio
async def test_candidate_unit_settles_only_its_operation_artifact(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)

    class Runner:
        calls = 0

        async def run(self, *, scope, enabled_tool_names, instruction, context, signal=None):
            del instruction, context, signal
            self.calls += 1
            assert enabled_tool_names[-1] == "writeScreenplayCandidatePartV1"
            receipt = await store.commit(
                scope=scope,
                run_id="run-screenplay-unit",
                payload=_payload("故事前提"),
            )
            return ScreenplayPartOutputEvidence(
                candidate_artifact_id=receipt.artifact_id
            )

    runner = Runner()
    result = await ScreenplayReplacementUnitExecutor(
        temp_db,
        model_runner=runner,
    ).execute(_context())

    assert runner.calls == 1
    assert result.output_ref.startswith("screenplay-candidate-v1://")
    assert result.validation_receipt["operationScopeId"] == (
        "task-screenplay:unit-premise:1"
    )


@pytest.mark.asyncio
async def test_missing_candidate_is_retryable_model_output(temp_db) -> None:
    from agents.screenplay.output_contract import ScreenplayPartOutputError

    class Runner:
        async def run(self, **kwargs):
            return ScreenplayPartOutputEvidence()

    executor = ScreenplayReplacementUnitExecutor(temp_db, model_runner=Runner())
    with pytest.raises(ScreenplayPartOutputError) as raised:
        await executor.execute(_context())

    signal = executor.classify_failure(raised.value)

    assert signal.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert signal.code == "screenplay_candidate_missing"
    assert signal.retryable is True

    structured = executor.classify_failure(ModelGatewayError(
        "invalid JSON",
        code="screenplay_structured_output_invalid",
        retryable=True,
    ))
    assert structured.category is FailureCategory.MODEL_OUTPUT_INVALID

    exhausted_repair = executor.classify_failure(ModelGatewayError(
        "response constraint repair was exhausted",
        code="response_constraint_violation",
        retryable=True,
    ))
    assert exhausted_repair.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert exhausted_repair.retryable is True


@pytest.mark.asyncio
async def test_interrupted_finalized_candidate_is_copied_to_new_attempt_without_model(
    temp_db,
) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)
    first_scope = _operation_scope(_context(attempt=1))
    first = await store.commit(
        scope=first_scope,
        run_id="run-screenplay-unit",
        payload=_payload("已完成候选"),
    )

    class Runner:
        async def run(self, **kwargs):
            raise AssertionError("finalized previous attempt must be reconciled")

    result = await ScreenplayReplacementUnitExecutor(
        temp_db,
        model_runner=Runner(),
    ).execute(_context(attempt=2, error_code="execution_recovery_after_restart"))

    assert result.output_ref != first.resource_ref
    recovered_id = result.output_ref.removeprefix("screenplay-candidate-v1://")
    assert (await store.load(recovered_id))["payload"] == {
        "content": "已完成候选"
    }


@pytest.mark.asyncio
async def test_host_capture_unit_commits_and_recovers_without_model_rewrite(temp_db) -> None:
    from agents.screenplay.capture_artifact import ScreenplayCaptureArtifactStore

    context = _context(attempt=1)
    context.unit.metadata.update({
        "partKind": "episode_metadata",
        "semanticKey": "episode:1:metadata",
        "episodeNumber": 1,
    })

    class Runner:
        calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            return ScreenplayPartOutputEvidence(host_capture={
                "episodeNumber": 1,
                "title": "雨夜",
                "continuitySummary": "两人被迫合作，追踪仍未结束。",
            })

    runner = Runner()
    first = await ScreenplayReplacementUnitExecutor(
        temp_db,
        model_runner=runner,
    ).execute(context)
    retry = _context(attempt=2, error_code="execution_recovery_after_restart")
    retry.unit.metadata.update(dict(context.unit.metadata))
    second = await ScreenplayReplacementUnitExecutor(
        temp_db,
        model_runner=Runner(),
    ).execute(retry)

    assert runner.calls == 1
    assert first.output_ref.startswith("screenplay-capture-v1://")
    assert second.output_ref != first.output_ref
    loaded = await ScreenplayCaptureArtifactStore(temp_db).try_load(
        _operation_scope(retry)
    )
    assert loaded[2]["title"] == "雨夜"


@pytest.mark.asyncio
async def test_host_only_chain_accepts_only_settled_dependency_receipts(temp_db) -> None:
    from agents.screenplay.host_result_artifact import ScreenplayHostResultArtifactStore

    store = ScreenplayCandidateArtifactStore(temp_db)
    dependency_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-host-only",
        unit_id="unit-source",
        attempt=1,
        part_kind="document_section",
        part_key="section:source",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )
    candidate = await store.commit(
        scope=dependency_scope,
        run_id="run-screenplay-unit",
        payload={
            "schemaVersion": 1,
            "partKind": "document_section",
            "partKey": "section:source",
            "targetRole": "creativeBrief",
            "payload": {"content": "已结算内容"},
        },
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref) "
        "VALUES ('task-host-only', 'unit-source', 'section:source', 0, "
        "'completed', ?)",
        [candidate.resource_ref],
    )
    projection_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-host-only",
        unit_id="unit-projection",
        attempt=1,
        part_kind="host_projection",
        part_key="projection:creativeBrief",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
        dependency_part_keys=("section:source",),
    )
    _, projection_ref, _ = await ScreenplayHostResultArtifactStore(temp_db).commit(
        scope=projection_scope,
        run_id="run-screenplay-unit",
        result={
            "projectionStatus": "committed",
            "targetRole": "creativeBrief",
            "revisionId": "revision-projected",
        },
        dependencies=({
            "partKey": "section:source",
            "outputRef": candidate.resource_ref,
            "contentDigest": "candidate-digest",
        },),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref) "
        "VALUES ('task-host-only', 'unit-projection', "
        "'projection:creativeBrief', 1, 'completed', ?)",
        [projection_ref],
    )

    def host_context(kind, key, dependency_key, dependency_ref, attempt=1):
        return SimpleNamespace(
            run_id="run-screenplay-unit",
            task=SimpleNamespace(id="task-host-only", metadata={
                "projectId": "project-1",
                "targetRole": "creativeBrief",
            }),
            unit=SimpleNamespace(
                id=f"unit-{kind}",
                attempt=attempt,
                error_code=None,
                metadata={
                    "partKind": kind,
                    "semanticKey": key,
                    "sourceRevisionRefs": [],
                    "deliverableRevisionScope": {},
                    "dependencyPartKeys": [dependency_key],
                    "sourceBookId": None,
                    "sourceItems": [],
                    "episodeNumber": None,
                    "sceneId": None,
                },
            ),
            dependency_outputs={"dependency-unit": dependency_ref},
        )

    executor = ScreenplayReplacementUnitExecutor(temp_db, model_runner=None)
    validation_context = host_context(
        "validation",
        "validation:creativeBrief",
        "projection:creativeBrief",
        projection_ref,
    )
    validation = await executor.execute(validation_context)
    assert validation.output_ref.startswith("screenplay-host-result-v1://")
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref) "
        "VALUES ('task-host-only', 'unit-validation', "
        "'validation:creativeBrief', 2, 'completed', ?)",
        [validation.output_ref],
    )
    final = await executor.execute(host_context(
        "final_response",
        "final:creativeBrief",
        "validation:creativeBrief",
        validation.output_ref,
    ))

    assert final.output_ref.startswith("screenplay-host-result-v1://")
    assert final.metadata["finalResponse"] == final.output_ref

    wrong = host_context(
        "validation",
        "validation:wrong",
        "section:source",
        "screenplay-candidate-v1://not-the-winner",
    )
    with pytest.raises(ValueError, match="settled winner"):
        await executor.execute(wrong)
