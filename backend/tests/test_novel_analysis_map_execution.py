from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from agents.novel_analysis.map_execution import (
    MapChildRunResult,
    MapSliceScope,
    PurrAChildJoinCoordinator,
    PurrAScalableMapChildRunner,
    READ_NOVEL_SOURCE_SLICE,
    SCALABLE_MAP_SCOPE_STATE_KEY,
    ScalableMapExecutionError,
    ScalableMapOutputError,
    ScalableMapUnitExecutor,
    _validate_map_payload,
    build_scalable_map_source_tool_catalog,
)
from agents.novel_analysis.child_submission import (
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.planner_contract import (
    AnalysisPass,
    ScalableAnalysisPlan,
    compile_scalable_analysis_recipe,
)
from agents.novel_analysis.source_slicing import (
    SliceSourceSection,
    SourceTokenizer,
    compile_source_slice_manifest,
)
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import (
    RunCreateParams,
    SqliteRunRepository,
)
from purra.contracts import ExecutionState
from purra.long_tasks import (
    DurableUnitExecutionContext,
    LongTaskRecord,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitStatus,
)
from purra.recovery import FailureCategory


def test_map_output_keeps_required_fields_and_ignores_analysis_extras():
    result = _validate_map_payload(
        {
            "findings": [{
                "dimension": "characters",
                "subject": "林月",
                "analysis": "人物观察",
                "confidence": 0.8,
            }],
            "notes": "阶段说明",
        },
        pass_id="characters",
        slice_id="slice-1",
        child_run_id="child-1",
        dimensions=("characters",),
    )
    assert result["findings"] == [{
        "dimension": "characters",
        "subject": "林月",
        "analysis": "人物观察",
    }]


@pytest.fixture
async def temp_db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _seed_source(db):
    text = "第一段正文。\n\n第二段正文。"
    digest = sha256(text.encode()).hexdigest()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '来源', 'external_text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, ?, ?)",
        [len(text.encode()), len(text)],
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest, byte_count, character_count) "
        "VALUES ('section-0', 'revision-1', 0, '第一章', ?, ?, ?, ?)",
        [text, digest, len(text.encode()), len(text)],
    )
    _, manifest = await _manifest_without_db(text)
    return text, manifest


async def _manifest_without_db(text="第一段正文。\n\n第二段正文。"):
    digest = sha256(text.encode()).hexdigest()
    section = SliceSourceSection(
        id="section-0",
        ordinal=0,
        title="第一章",
        text=text,
        content_digest=digest,
        byte_count=len(text.encode()),
        character_count=len(text),
        token_count=len(text),
    )
    manifest = compile_source_slice_manifest(
        source_revision_id="revision-1",
        source_revision_digest="revision-digest",
        context_window_tokens=1_000,
        sections=(section,),
        tokenizer=SourceTokenizer(
            id="test.characters", version="1", count_kind="exact",
            count=len, safety_basis_points=10_000,
        ),
    )
    return text, manifest


@pytest.mark.asyncio
async def test_slice_tool_has_no_model_selected_slice_and_reads_only_bound_text(temp_db):
    text, manifest = await _seed_source(temp_db)
    source_slice = manifest.slices[0]
    scope = {
        "sourceRevisionId": manifest.source_revision_id,
        "sourceRevisionDigest": manifest.source_revision_digest,
        "sliceId": source_slice.id,
        "slicePosition": source_slice.position,
        "tokenCount": source_slice.token_count,
        "ranges": [item.to_mapping() for item in source_slice.ranges],
    }
    catalog = build_scalable_map_source_tool_catalog(temp_db)
    registration = catalog.get(READ_NOVEL_SOURCE_SLICE)

    assert registration.schema.parameters["properties"] == {}
    result = await registration.handler(ExecutionState(domain={
        SCALABLE_MAP_SCOPE_STATE_KEY: scope,
    }), {})
    payload = json.loads(result.content)
    assert result.error_code is None
    assert payload["sliceId"] == source_slice.id
    assert "".join(item["text"] for item in payload["items"]) == text


@pytest.mark.asyncio
async def test_slice_tool_display_names_bound_slice_and_chapter_range(temp_db):
    _, manifest = await _seed_source(temp_db)
    source_slice = manifest.slices[0]
    scope = MapSliceScope.from_manifest(manifest.to_mapping(), source_slice.id)
    registration = build_scalable_map_source_tool_catalog(temp_db).get(
        READ_NOVEL_SOURCE_SLICE
    )

    display = registration.operation_display_params(
        ExecutionState(domain={
            SCALABLE_MAP_SCOPE_STATE_KEY: scope.to_mapping(),
        }),
        {},
        SimpleNamespace(),
    )

    assert display["displayNames"]["zh-CN"] == (
        "读取第 1 个小说分片（第 1 章范围）"
    )
    assert display["slicePosition"] == 1
    assert display["chapterRange"] == "第 1 章范围"


@pytest.mark.asyncio
async def test_slice_tool_resolves_host_scope_from_current_tree_run(temp_db):
    text, manifest = await _seed_source(temp_db)
    source_slice = manifest.slices[0]
    scope = MapSliceScope.from_manifest(manifest.to_mapping(), source_slice.id)

    class _Tree:
        async def get_run(self, run_id):
            assert run_id == "child-run"
            return SimpleNamespace(input_payload={
                "mapSliceScope": scope.to_mapping(),
            })

    registration = build_scalable_map_source_tool_catalog(
        temp_db, run_tree_repository=_Tree()
    ).get(READ_NOVEL_SOURCE_SLICE)
    result = await registration.handler(
        ExecutionState(run_id="child-run"), {}
    )

    assert result.error_code is None
    assert "".join(
        item["text"] for item in json.loads(result.content)["items"]
    ) == text


@pytest.mark.asyncio
async def test_purra_child_runner_uses_public_tree_commands_and_frozen_input():
    _, manifest = await _manifest_without_db()
    scope = MapSliceScope.from_manifest(
        manifest.to_mapping(), manifest.slices[0].id
    )

    class _Core:
        def __init__(self):
            self.command = None

        async def spawn_agents(self, command):
            self.command = command
            return SimpleNamespace(items=(SimpleNamespace(
                agent=SimpleNamespace(agent_id="map-agent", context_version=0),
                run=SimpleNamespace(run_id="child-run")
            ),))

        async def join_agent_runs(self, requester, run_ids, signal):
            assert requester == "root-run"
            assert run_ids == ("child-run",)
            return SimpleNamespace(
                pending_run_ids=(),
                required_failures=(),
                results=({
                    "runId": "child-run",
                    "result": {"content": "当前分片分析已经提交。"},
                },),
            )

    core = _Core()
    async def load_submission(child_id):
        assert child_id == "child-run"
        return {"findings": []}
    runner = PurrAScalableMapChildRunner(
        model_name="test-model",
        submissions=SimpleNamespace(load=load_submission),
    )
    runner.bind_agent_core(core)
    result = await runner.run(
        scope=scope,
        pass_id="story",
        dimensions=("characters",),
        context=SimpleNamespace(
            task=SimpleNamespace(id="task"),
            unit=SimpleNamespace(id="map", attempt=1),
            run_id="root-run",
        ),
    )

    child = core.command.children[0]
    assert core.command.parent_run_id == "root-run"
    assert child.input_payload["mapSliceScope"]["sliceId"] == scope.slice_id
    assert child.input_payload["unitId"] == "map"
    assert child.input_payload["attempt"] == 1
    assert child.capability_grant.allowed_tools == (
        READ_NOVEL_SOURCE_SLICE,
        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
    )
    assert child.capability_grant.can_spawn_agents is False
    assert result == MapChildRunResult("child-run", {"findings": []})


@pytest.mark.asyncio
async def test_map_runner_continues_the_same_agent_for_the_same_slice():
    _, manifest = await _manifest_without_db()
    scope = MapSliceScope.from_manifest(
        manifest.to_mapping(), manifest.slices[0].id
    )

    class _Core:
        def __init__(self):
            self.spawn_commands = []
            self.continue_commands = []

        async def spawn_agents(self, command):
            self.spawn_commands.append(command)
            return SimpleNamespace(items=(SimpleNamespace(
                agent=SimpleNamespace(agent_id="map-agent", context_version=0),
                run=SimpleNamespace(run_id="child-1"),
            ),))

        async def continue_agent(self, command):
            self.continue_commands.append(command)
            return SimpleNamespace(
                agent=SimpleNamespace(agent_id="map-agent", context_version=1),
                run=SimpleNamespace(run_id="child-2"),
            )

        async def join_agent_runs(self, requester, run_ids, signal):
            del requester, signal
            child_id = run_ids[0]
            return SimpleNamespace(
                pending_run_ids=(), required_failures=(),
                results=({
                    "runId": child_id,
                    "result": {"content": "当前分片分析已经提交。"},
                },),
            )

    core = _Core()
    async def load_submission(child_id):
        return {"findings": []}
    runner = PurrAScalableMapChildRunner(
        model_name="test-model",
        submissions=SimpleNamespace(load=load_submission),
    )
    runner.bind_agent_core(core)
    base = SimpleNamespace(
        task=SimpleNamespace(id="task"),
        unit=SimpleNamespace(id="map:story", attempt=1),
        run_id="root-run",
    )

    first = await runner.run(
        scope=scope, pass_id="story", dimensions=("characters",), context=base,
    )
    second = await runner.run(
        scope=scope,
        pass_id="setting",
        dimensions=("settings",),
        context=SimpleNamespace(
            task=base.task,
            unit=SimpleNamespace(id="map:setting", attempt=1),
            run_id=base.run_id,
        ),
    )

    assert first.child_run_id == "child-1"
    assert second.child_run_id == "child-2"
    assert len(core.spawn_commands) == 1
    assert len(core.continue_commands) == 1
    continuation = core.continue_commands[0]
    assert continuation.agent_id == "map-agent"
    assert continuation.expected_context_version == 1
    assert "setting" in continuation.message


@pytest.mark.asyncio
async def test_map_runner_spawns_fresh_agent_for_retry_attempt():
    _, manifest = await _manifest_without_db()
    scope = MapSliceScope.from_manifest(
        manifest.to_mapping(), manifest.slices[0].id
    )

    class _Core:
        def __init__(self):
            self.spawn_commands = []
            self.continue_commands = []

        async def spawn_agents(self, command):
            self.spawn_commands.append(command)
            position = len(self.spawn_commands)
            return SimpleNamespace(items=(SimpleNamespace(
                agent=SimpleNamespace(
                    agent_id=f"map-agent-{position}", context_version=0
                ),
                run=SimpleNamespace(run_id=f"child-{position}"),
            ),))

        async def continue_agent(self, command):
            self.continue_commands.append(command)
            raise AssertionError("retry must not continue the failed Agent")

        async def join_agent_runs(self, requester, run_ids, signal):
            del requester, signal
            child_id = run_ids[0]
            return SimpleNamespace(
                pending_run_ids=(), required_failures=(),
                results=({
                    "runId": child_id,
                    "result": {"content": "当前分片分析已经提交。"},
                },),
            )

    core = _Core()
    async def load_submission(child_id):
        return {"findings": []}
    runner = PurrAScalableMapChildRunner(
        model_name="test-model",
        submissions=SimpleNamespace(load=load_submission),
    )
    runner.bind_agent_core(core)
    for attempt in (1, 2):
        await runner.run(
            scope=scope,
            pass_id="story",
            dimensions=("characters",),
            context=SimpleNamespace(
                task=SimpleNamespace(id="task"),
                unit=SimpleNamespace(id="map:story", attempt=attempt),
                run_id="root-run",
            ),
        )

    assert len(core.spawn_commands) == 2
    assert core.continue_commands == []


@pytest.mark.asyncio
async def test_slice_tool_inherits_scope_from_previous_agent_run(temp_db):
    text, manifest = await _seed_source(temp_db)
    scope = MapSliceScope.from_manifest(
        manifest.to_mapping(), manifest.slices[0].id
    )

    class _Tree:
        async def get_run(self, run_id):
            if run_id == "continued-run":
                return SimpleNamespace(input_payload={}, previous_run_id="initial-run")
            assert run_id == "initial-run"
            return SimpleNamespace(
                input_payload={"mapSliceScope": scope.to_mapping()},
                previous_run_id=None,
            )

    registration = build_scalable_map_source_tool_catalog(
        temp_db, run_tree_repository=_Tree()
    ).get(READ_NOVEL_SOURCE_SLICE)
    result = await registration.handler(ExecutionState(run_id="continued-run"), {})

    assert result.error_code is None
    assert "".join(
        item["text"] for item in json.loads(result.content)["items"]
    ) == text


@pytest.mark.asyncio
async def test_child_join_coordinator_batches_parallel_units_per_root():
    class _Core:
        def __init__(self):
            self.calls = []

        async def join_agent_runs(self, requester, run_ids, signal):
            self.calls.append((requester, run_ids, signal))
            return SimpleNamespace(
                pending_run_ids=(), required_failures=(), results=(),
            )

    core = _Core()
    coordinator = PurrAChildJoinCoordinator()
    coordinator.bind_agent_core(core)
    results = await asyncio.gather(*(
        coordinator.join("root-run", f"child-{index}")
        for index in range(4)
    ))

    assert len(results) == 4
    assert core.calls == [(
        "root-run",
        ("child-0", "child-1", "child-2", "child-3"),
        None,
    )]


@pytest.mark.asyncio
async def test_child_join_coordinator_recovers_after_canceled_batch():
    class _Core:
        def __init__(self):
            self.calls = 0

        async def join_agent_runs(self, requester, run_ids, signal):
            del requester, run_ids, signal
            self.calls += 1
            if self.calls == 1:
                raise asyncio.CancelledError()
            return SimpleNamespace(
                pending_run_ids=(), required_failures=(), results=(),
            )

    core = _Core()
    coordinator = PurrAChildJoinCoordinator()
    coordinator.bind_agent_core(core)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.join("root-run", "canceled-child")
    result = await coordinator.join("root-run", "replacement-child")

    assert result.pending_run_ids == ()
    assert core.calls == 2


@dataclass
class _Runner:
    db: object
    root_run_id: str
    calls: int = 0

    async def run(self, *, scope, pass_id, dimensions, context, signal=None):
        del signal
        self.calls += 1
        child_id = f"child-{context.unit.id}-{context.unit.attempt}"
        await SqliteRunRepository(self.db).create(RunCreateParams(
            session_id=None,
            prompt="bounded map",
            mode="novel_analysis_map",
            requested_run_id=child_id,
            root_run_id=self.root_run_id,
            parent_run_id=self.root_run_id,
            agent_id=child_id,
        ))
        return MapChildRunResult(
            child_run_id=child_id,
            payload={"findings": [{
                "dimension": dimensions[0],
                "subject": "主人公",
                "analysis": f"来自 {scope.slice_id} 的局部观察",
            }]},
        )


def _context(manifest, recipe, *, root_run_id="root-run"):
    step = next(item for item in recipe.steps if item.kind == "map")
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.novel_analysis",
        kind=recipe.kind,
        owner_id="revision-1",
        created_by_run_id=root_run_id,
        status=LongTaskStatus.RUNNING,
        revision=1,
        total_units=len(recipe.steps),
        completed_units=0,
        failed_units=0,
        max_parallelism=recipe.max_parallelism,
        metadata={"sliceManifest": manifest.to_mapping()},
    )
    unit = LongTaskUnitRecord(
        task_id=task.id,
        id=step.id,
        position=0,
        status=LongTaskUnitStatus.RUNNING,
        attempt=1,
        max_attempts=step.max_attempts,
        metadata={**dict(step.metadata), "unitKind": step.kind},
    )
    return DurableUnitExecutionContext(
        task=task,
        unit=unit,
        run_id=root_run_id,
        dependency_outputs={},
    )


async def _root(db, run_id="root-run"):
    await SqliteRunRepository(db).create(RunCreateParams(
        session_id=None,
        prompt="root",
        mode="novel_analysis",
        requested_run_id=run_id,
        agent_id=run_id,
    ))


def _recipe(manifest):
    return compile_scalable_analysis_recipe(
        manifest=manifest,
        plan=ScalableAnalysisPlan(
            passes=(AnalysisPass("story", ("characters", "plot")),),
            reduce_fan_in=2,
            synthesis_sections=("整书分析",),
            quality_checks=("覆盖全部分片",),
        ),
    )


@pytest.mark.asyncio
async def test_map_executor_requires_child_ownership_and_commits_attempt_artifact(temp_db):
    _, manifest = await _seed_source(temp_db)
    await _root(temp_db)
    recipe = _recipe(manifest)
    runner = _Runner(temp_db, "root-run")
    result = await ScalableMapUnitExecutor(
        temp_db, child_runner=runner
    ).execute(_context(manifest, recipe))

    assert runner.calls == 1
    assert result.validation_receipt["childRunId"].startswith("child-map:story:0")
    assert result.validation_receipt["sliceId"] == manifest.slices[0].id
    artifact = await temp_db.fetch_one(
        "SELECT created_by_run_id FROM ai_agent_artifacts WHERE id = ?",
        [result.metadata["artifactId"]],
    )
    assert artifact["created_by_run_id"] == result.validation_receipt["childRunId"]
    replay = await ScalableMapUnitExecutor(
        temp_db, child_runner=runner
    ).execute(_context(manifest, recipe))
    assert runner.calls == 1
    assert replay.output_ref == result.output_ref
    assert replay.validation_receipt["artifactReplayed"] is True


@pytest.mark.asyncio
async def test_map_executor_recovers_finalized_attempt_on_new_root(temp_db):
    _, manifest = await _seed_source(temp_db)
    await _root(temp_db)
    recipe = _recipe(manifest)
    runner = _Runner(temp_db, "root-run")
    executor = ScalableMapUnitExecutor(temp_db, child_runner=runner)
    first_context = _context(manifest, recipe)
    first = await executor.execute(first_context)
    await _root(temp_db, "continuation-root")
    continued = _context(manifest, recipe, root_run_id="continuation-root")
    continued = replace(continued, unit=replace(
        continued.unit,
        attempt=2,
        error_code="execution_recovery_after_restart",
    ))

    recovered = await executor.execute(continued)

    assert runner.calls == 1
    assert recovered.output_ref != first.output_ref
    assert recovered.validation_receipt["recoveredOperationId"].endswith(":1")

class _RootRunner:
    async def run(self, *, context, **kwargs):
        del kwargs
        return MapChildRunResult(context.run_id, {"findings": []})


@pytest.mark.asyncio
async def test_map_executor_accepts_root_owned_model_execution_when_planned(temp_db):
    _, manifest = await _seed_source(temp_db)
    await _root(temp_db)
    recipe = compile_scalable_analysis_recipe(
        manifest=manifest,
        plan=ScalableAnalysisPlan(
            passes=(AnalysisPass("story", ("characters", "plot"), "root"),),
            reduce_fan_in=2,
            synthesis_sections=("整书分析",),
            quality_checks=("覆盖全部分片",),
            execution_modes={
                "synthesize": "root", "review": "root",
            },
        ),
    )
    result = await ScalableMapUnitExecutor(
        temp_db, child_runner=_RootRunner()
    ).execute(_context(manifest, recipe))

    assert result.validation_receipt["childRunId"] == "root-run"


class _InvalidOutputRunner(_Runner):
    async def run(self, **kwargs):
        result = await super().run(**kwargs)
        return MapChildRunResult(result.child_run_id, {"unexpected": []})


@pytest.mark.asyncio
async def test_invalid_model_output_is_retryable_but_wrong_run_ownership_is_not(temp_db):
    _, manifest = await _seed_source(temp_db)
    await _root(temp_db)
    executor = ScalableMapUnitExecutor(
        temp_db, child_runner=_InvalidOutputRunner(temp_db, "root-run")
    )
    with pytest.raises(ScalableMapOutputError) as failure:
        await executor.execute(_context(manifest, _recipe(manifest)))

    signal = executor.classify_failure(failure.value)
    ownership = executor.classify_failure(
        ScalableMapExecutionError("wrong owner")
    )
    assert signal.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert signal.retryable is True
    assert ownership.category is FailureCategory.BUSINESS_INVARIANT
    assert ownership.retryable is False
