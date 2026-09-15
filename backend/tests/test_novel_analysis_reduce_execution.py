from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.reduce_execution import (
    READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
    PurrAScalableReduceChildRunner,
    ReduceChildRunResult,
    ReduceInputScopeCompiler,
    ScalableReduceExecutionError,
    ScalableReduceOutputError,
    ScalableReduceUnitExecutor,
    _validate_reduce_output,
    build_scalable_reduce_input_tool_catalog,
)
from agents.novel_analysis.child_submission import (
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import RunCreateParams, SqliteRunRepository
from purra.contracts import ExecutionState
from purra.recovery import FailureCategory


def test_reduce_output_keeps_required_fields_and_ignores_analysis_extras():
    scope = SimpleNamespace(
        pass_id="story",
        reduce_level=0,
        artifacts=(),
        covered_slice_ids=("slice-1",),
    )
    result = _validate_reduce_output(
        {
            "findings": [{
                "dimension": "plot",
                "subject": "主线",
                "analysis": "整合分析",
                "confidence": 0.9,
            }],
            "conflicts": [],
            "notes": "阶段说明",
        },
        scope=scope,
        dimensions=("plot",),
        child_run_id="child-1",
    )
    assert result["findings"] == [{
        "dimension": "plot",
        "subject": "主线",
        "analysis": "整合分析",
    }]


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _artifact(db, unit_id, slice_id, *, pass_id="story", analysis="观察"):
    payload = {
        "schemaVersion": 1,
        "kind": "map",
        "passId": pass_id,
        "sliceId": slice_id,
        "childRunId": f"child-{slice_id}",
        "findings": [{
            "dimension": "plot",
            "subject": slice_id,
            "analysis": analysis,
        }],
    }
    receipt = await NovelAnalysisAttemptArtifactStore(db).commit(
        task_id="task",
        unit_id=unit_id,
        attempt=1,
        operation_id=f"task:{unit_id}:1",
        run_id=f"child-{slice_id}",
        payload=payload,
    )
    return receipt.resource_ref


def _context(outputs, *, context_window=32_768, fan_in=2):
    dependencies = tuple(outputs)
    return SimpleNamespace(
        task=SimpleNamespace(metadata={
            "id": "not-used",
            "sliceManifest": {"contextWindowTokens": context_window},
        }, id="task"),
        unit=SimpleNamespace(
            id="reduce:story:0:0",
            attempt=1,
            dependencies=dependencies,
            metadata={
                "unitKind": "reduce",
                "passId": "story",
                "reduceLevel": 0,
                "fanIn": fan_in,
            },
        ),
        dependency_outputs=outputs,
        run_id="root-run",
    )


@pytest.mark.asyncio
async def test_reduce_scope_contains_only_verified_locators_and_complete_lineage(db):
    outputs = {
        "map:story:0": await _artifact(db, "map:story:0", "slice-0"),
        "map:story:1": await _artifact(db, "map:story:1", "slice-1"),
    }

    scope = await ReduceInputScopeCompiler(db).compile(_context(outputs))
    mapping = scope.to_mapping()

    assert scope.covered_slice_ids == ("slice-0", "slice-1")
    assert scope.input_token_count <= scope.input_token_limit
    assert [item["unitId"] for item in mapping["artifacts"]] == list(outputs)
    assert "findings" not in mapping
    assert "观察" not in str(mapping)


@pytest.mark.asyncio
async def test_reduce_scope_rejects_missing_wrong_pass_and_overlapping_inputs(db):
    first = await _artifact(db, "map:story:0", "slice-0")
    wrong = await _artifact(db, "map:other:1", "slice-1", pass_id="other")
    compiler = ReduceInputScopeCompiler(db)

    with pytest.raises(ScalableReduceExecutionError, match="dependency contract"):
        await compiler.compile(_context({"map:story:0": first}))
    with pytest.raises(ScalableReduceExecutionError, match="another pass"):
        await compiler.compile(_context({
            "map:story:0": first,
            "map:story:1": wrong,
        }))

    duplicate = await _artifact(db, "map:story:2", "slice-0")
    with pytest.raises(ScalableReduceExecutionError, match="overlapping"):
        await compiler.compile(_context({
            "map:story:0": first,
            "map:story:2": duplicate,
        }))


@pytest.mark.asyncio
async def test_reduce_scope_fails_closed_when_dependency_payload_exceeds_budget(db):
    outputs = {
        "map:story:0": await _artifact(
            db, "map:story:0", "slice-0", analysis="甲" * 2_000
        ),
        "map:story:1": await _artifact(
            db, "map:story:1", "slice-1", analysis="乙" * 2_000
        ),
    }

    with pytest.raises(ScalableReduceExecutionError, match="input budget"):
        await ReduceInputScopeCompiler(db).compile(
            _context(outputs, context_window=1_000)
        )


@pytest.mark.asyncio
async def test_reduce_tool_reads_only_artifacts_bound_to_current_child(db):
    outputs = {
        "map:story:0": await _artifact(db, "map:story:0", "slice-0"),
        "map:story:1": await _artifact(db, "map:story:1", "slice-1"),
    }
    scope = await ReduceInputScopeCompiler(db).compile(_context(outputs))

    class _Tree:
        async def get_run(self, run_id):
            assert run_id == "reduce-child"
            return SimpleNamespace(input_payload={"reduceInputScope": scope.to_mapping()})

    registration = build_scalable_reduce_input_tool_catalog(
        db, run_tree_repository=_Tree()
    ).get(READ_NOVEL_ANALYSIS_REDUCE_INPUTS)
    assert registration.schema.parameters["properties"] == {}
    result = await registration.handler(ExecutionState(run_id="reduce-child"), {})
    payload = json.loads(result.content)

    assert result.error_code is None
    assert payload["coveredSliceIds"] == ["slice-0", "slice-1"]
    assert [item["unitId"] for item in payload["inputs"]] == list(outputs)
    assert all("findings" in item["payload"] for item in payload["inputs"])


@pytest.mark.asyncio
async def test_reduce_runner_uses_public_tree_commands_and_tool_only_grant(db):
    outputs = {
        "map:story:0": await _artifact(db, "map:story:0", "slice-0"),
        "map:story:1": await _artifact(db, "map:story:1", "slice-1"),
    }
    context = _context(outputs)
    scope = await ReduceInputScopeCompiler(db).compile(context)

    class _Core:
        async def spawn_agents(self, command):
                self.command = command
                return SimpleNamespace(items=(SimpleNamespace(
                    agent=SimpleNamespace(
                        agent_id="reduce-agent", context_version=0
                    ),
                    run=SimpleNamespace(run_id="reduce-child")
                ),))

        async def join_agent_runs(self, requester, run_ids, signal):
            assert requester == "root-run"
            assert run_ids == ("reduce-child",)
            return SimpleNamespace(
                pending_run_ids=(),
                required_failures=(),
                results=({
                    "runId": "reduce-child",
                    "result": {"content": "当前归并结果已经提交。"},
                },),
            )

    core = _Core()
    async def load_submission(child_id):
        assert child_id == "reduce-child"
        return {"findings": [], "conflicts": []}
    runner = PurrAScalableReduceChildRunner(
        model_name="test-model",
        submissions=SimpleNamespace(load=load_submission),
    )
    runner.bind_agent_core(core)
    result = await runner.run(
        scope=scope,
        dimensions=("plot",),
        context=context,
    )

    child = core.command.children[0]
    assert child.capability_grant.allowed_tools == (
        READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
    )
    assert child.capability_grant.can_spawn_agents is False
    assert "findings" not in str(child.input_payload)
    assert result.child_run_id == "reduce-child"


@dataclass
class _ReduceRunner:
    db: object
    calls: int = 0

    async def run(self, *, scope, dimensions, context, signal=None):
        del signal
        self.calls += 1
        child_id = f"reduce-child-{context.unit.attempt}"
        await SqliteRunRepository(self.db).create(RunCreateParams(
            session_id=None,
            prompt="bounded reduce",
            mode="novel_analysis_reduce",
            requested_run_id=child_id,
            root_run_id=context.run_id,
            parent_run_id=context.run_id,
            agent_id=child_id,
        ))
        return ReduceChildRunResult(child_id, {
            "findings": [{
                "dimension": dimensions[0],
                "subject": "整书冲突",
                "analysis": "两个分片共同构成一次升级。",
            }],
            "conflicts": [],
        })


@pytest.mark.asyncio
async def test_reduce_executor_commits_lineage_and_replays_without_model(db):
    await SqliteRunRepository(db).create(RunCreateParams(
        session_id=None, prompt="root", mode="analysis",
        requested_run_id="root-run", agent_id="root-run",
    ))
    outputs = {
        "map:story:0": await _artifact(db, "map:story:0", "slice-0"),
        "map:story:1": await _artifact(db, "map:story:1", "slice-1"),
    }
    context = _context(outputs)
    context = SimpleNamespace(
        task=context.task,
        unit=SimpleNamespace(
            id=context.unit.id,
            attempt=context.unit.attempt,
            dependencies=context.unit.dependencies,
            metadata={**context.unit.metadata, "dimensions": ["plot"]},
        ),
        dependency_outputs=context.dependency_outputs,
        run_id=context.run_id,
    )
    runner = _ReduceRunner(db)
    executor = ScalableReduceUnitExecutor(db, child_runner=runner)

    result = await executor.execute(context)
    replay = await executor.execute(context)

    assert runner.calls == 1
    assert result.validation_receipt["coveredSliceIds"] == ["slice-0", "slice-1"]
    assert replay.output_ref == result.output_ref
    assert replay.validation_receipt["artifactReplayed"] is True


@pytest.mark.asyncio
async def test_reduce_output_failure_is_retryable_but_scope_failure_is_not(db):
    executor = ScalableReduceUnitExecutor(db, child_runner=_ReduceRunner(db))
    output = executor.classify_failure(ScalableReduceOutputError("bad"))
    assert output.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert output.retryable is True
    assert executor.classify_failure(ScalableReduceExecutionError("bad")).retryable is False
