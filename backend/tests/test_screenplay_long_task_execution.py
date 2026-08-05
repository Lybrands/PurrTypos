from __future__ import annotations

import asyncio
import json

import pytest

from agent_core.long_tasks import (
    LongTaskRecord,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitStatus,
)
from agent_core.contracts import AgentRunResult, RunLineage, RunStatus
from agent_core.events import AgentEvent, CoreEventType
from application.screenplay_long_task_execution import ScreenplayLongTaskExecution
from domains.screenplay.long_task_response import (
    ScreenplayDraftBatchResponseValidator,
)


class _Database:
    async def fetch_one(self, query, params):
        if "kind = 'scene_list'" in query:
            return {
                "id": "scene-list-1",
                "content_json": {
                    "scenes": [
                        {"id": "s05", "heading": "第五场", "episodeNumber": 1},
                        {"id": "s06", "heading": "第六场", "episodeNumber": 1},
                    ],
                },
            }
        if "kind = 'scene_draft'" in query:
            return {"id": "draft-1", "content_json": {}, "content_text": "已接受正文"}
        raise AssertionError((query, params))


class _Repository:
    def __init__(self, previous):
        self.previous = previous

    async def list_units(self, task_id):
        assert task_id == "task-1"
        return (self.previous,)


class _DelegationService:
    def __init__(self):
        self.view = None
        self.recorded = []
        self.failed = []

    async def delegate(self, **kwargs):
        self.view = {
            "delegationId": "delegation-writer-1",
            "parentRunId": kwargs["parent_run_id"],
            "rootRunId": kwargs["parent_run_id"],
            "childRunId": None,
            "agentRole": kwargs["agent_role"],
            "objective": kwargs["objective"],
            "status": "queued",
            "required": True,
            "priority": 0,
        }
        return self.view

    async def claim_delegation(self, **kwargs):
        assert self.view is not None
        assert kwargs["delegation_id"] == self.view["delegationId"]
        return self.view, RunLineage(
            parent_run_id=self.view["parentRunId"],
            root_run_id=self.view["rootRunId"],
            delegation_id=self.view["delegationId"],
            agent_role=self.view["agentRole"],
            depth=1,
        )

    async def record_result(self, **kwargs):
        self.recorded.append(kwargs)
        return True

    async def fail_claim(self, **kwargs):
        self.failed.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_batch_prompt_thaws_completed_unit_metadata_before_json_encoding():
    previous = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.COMPLETED,
        metadata={
            "scenes": [{
                "sceneId": "s05",
                "sceneText": "第五场正文",
                "execution": {
                    "objectiveResult": "完成目标",
                    "conflictResult": "推进冲突",
                    "turnResult": "完成转折",
                    "continuityState": "人物进入下一场",
                    "unresolvedNotes": [],
                },
                "continuitySummary": "第五场连续性",
            }],
            "continuitySummary": "第五场连续性",
        },
    )
    current = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0002",
        position=1,
        status=LongTaskUnitStatus.CLAIMED,
        dependencies=("batch-0001",),
        metadata={"sceneIds": ["s06"]},
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=3,
        completed_units=1,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )
    composition = type("Composition", (), {"database": _Database()})()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_Repository(previous),
        work_items=object(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
        delegation_service=_DelegationService(),
    )

    prompt = await execution._build_batch_prompt(task, current)
    payload = json.loads(prompt.split("\n", 1)[1])

    assert payload["requiredSceneIds"] == ["s06"]
    assert payload["recentGeneratedScenes"][0]["sceneId"] == "s05"
    assert payload["continuitySummary"] == "第五场连续性"
    assert payload["boundaryContext"]["previousScene"]["id"] == "s05"


@pytest.mark.asyncio
async def test_reviewer_prompt_uses_only_planner_dependency_outputs():
    previous = LongTaskUnitRecord(
        task_id="task-1",
        id="writer-opening",
        position=0,
        status=LongTaskUnitStatus.COMPLETED,
        metadata={
            "unitKind": "scene_generation",
            "scenes": [json.loads(_valid_batch_response())["scenes"][0]],
        },
    )
    current = LongTaskUnitRecord(
        task_id="task-1",
        id="review-opening",
        position=1,
        status=LongTaskUnitStatus.CLAIMED,
        dependencies=("writer-opening",),
        metadata={
            "unitKind": "continuity_review",
            "sceneIds": ["s05"],
        },
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=3,
        completed_units=1,
        failed_units=0,
        max_parallelism=2,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )
    composition = type("Composition", (), {"database": _Database()})()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_Repository(previous),
        work_items=object(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
        delegation_service=_DelegationService(),
    )

    prompt = await execution._build_batch_prompt(task, current)
    payload = json.loads(prompt.split("\n", 1)[1])

    assert payload["task"] == "review_and_revise_screenplay_scene_batch"
    assert [scene["sceneId"] for scene in payload["draftScenes"]] == ["s05"]
    assert "连续性审阅节点" in prompt


def _valid_batch_response(scene_id="s05"):
    return json.dumps({
        "assistantResponse": "已完成 s05 正文，并让林月进入下一场。",
        "scenes": [{
            "sceneId": scene_id,
            "sceneText": "EXT. 犬域 - 日\n\n@林月 抬头看向紫色天空。",
            "execution": {
                "objectiveResult": "完成场景目标",
                "conflictResult": "推进场景冲突",
                "turnResult": "完成场景转折",
                "continuityState": "人物进入下一场",
                "unresolvedNotes": [],
            },
            "continuitySummary": "林月继续深入犬域。",
        }],
    }, ensure_ascii=False)


def test_batch_response_contract_rejects_unclosed_json_before_run_success():
    validator = ScreenplayDraftBatchResponseValidator(({
        "id": "s05",
        "heading": "第五场",
    },))

    rejected = validator.validate(
        content=_valid_batch_response()[:-2],
        messages=(),
    )
    accepted = validator.validate(
        content=_valid_batch_response(),
        messages=(),
    )

    assert rejected.violation_code == "screenplay.batch.invalid_json"
    assert rejected.repair_guidance is not None
    assert accepted.accepted is True


def test_batch_response_contract_does_not_retry_valid_scenes_for_missing_ui_summary():
    validator = ScreenplayDraftBatchResponseValidator(({
        "id": "s05",
        "heading": "第五场",
    },))
    payload = json.loads(_valid_batch_response())
    payload.pop("assistantResponse")

    result = validator.validate(
        content=json.dumps(payload, ensure_ascii=False),
        messages=(),
    )

    assert result.accepted is True


@pytest.mark.asyncio
async def test_missing_ui_summary_is_derived_after_scene_validation():
    previous = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.CLAIMED,
        metadata={
            "unitKind": "scene_generation",
            "sceneIds": ["s05"],
            "sceneHeadings": ["第五场"],
        },
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=1,
        total_units=2,
        completed_units=0,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )
    payload = json.loads(_valid_batch_response())
    payload.pop("assistantResponse")
    execution = ScreenplayLongTaskExecution(
        composition=type("Composition", (), {"database": _Database()})(),
        repository=_Repository(previous),
        work_items=object(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
        delegation_service=_DelegationService(),
    )

    scenes, response = await execution._validate_batch_result(
        task,
        previous,
        json.dumps(payload, ensure_ascii=False),
    )

    assert [scene["sceneId"] for scene in scenes] == ["s05"]
    assert response == (
        "已完成 第五场 共 1 场的正文创作，"
        "并记录了各场的目标、冲突、转折和连续性状态。"
    )


@pytest.mark.asyncio
async def test_structured_candidate_delta_is_not_emitted_to_parent_chat():
    observed = []

    async def observer(update):
        observed.append(update.event)

    execution = ScreenplayLongTaskExecution(
        composition=type("Composition", (), {"database": _Database()})(),
        repository=_Repository(None),
        work_items=object(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
        observer=observer,
        parent_run_id="run-parent",
        delegation_service=_DelegationService(),
    )
    task = type("Task", (), {
        "id": "task-1",
        "created_by_run_id": "run-parent",
    })()
    unit = type("Unit", (), {"id": "writer-a", "attempt": 1})()
    view = {
        "delegationId": "delegation-a",
        "parentRunId": "run-parent",
        "rootRunId": "run-parent",
        "agentRole": "screenplay_writer",
    }

    await execution._emit_child_event(
        task,
        unit,
        view,
        AgentEvent(
            type=CoreEventType.MODEL_DELTA,
            run_id="run-child",
            payload={"delta": '{"scenes":['},
        ),
    )
    await execution._emit_child_event(
        task,
        unit,
        view,
        AgentEvent(
            type=CoreEventType.RUN_COMPLETED,
            run_id="run-child",
            payload={
                "status": "done",
                "final_response": _valid_batch_response(),
            },
        ),
    )
    await execution._emit_child_event(
        task,
        unit,
        view,
        AgentEvent(
            type="screenplay.long_task.response",
            run_id="run-child",
            payload={"content": "已完成本批正文。"},
        ),
    )

    assert len(observed) == 1
    assert observed[0].payload["event"]["type"] == (
        "screenplay.long_task.response"
    )


@pytest.mark.asyncio
async def test_batch_child_run_receives_contract_and_parent_lineage(monkeypatch):
    captured = {}

    async def _run(_service, **kwargs):
        captured.update(kwargs)
        yield AgentEvent(type="run.started", run_id="run-child", payload={})
        yield AgentEvent(
            type="model.thinking_delta",
            run_id="run-child",
            payload={"delta": "先确认上一场连续性。"},
        )
        yield AgentRunResult(
            run_id="run-child",
            status=RunStatus.DONE,
            final_response=_valid_batch_response(),
        )

    monkeypatch.setattr(
        "application.agent_run_service.AgentRunService.run",
        _run,
    )

    class _Body:
        def model_copy(self, *, update):
            captured["bodyUpdate"] = update
            return self

    class _ExecutionRepository:
        def __init__(self):
            self.binds = []

        async def list_units(self, task_id):
            assert task_id == "task-1"
            return ()

        async def bind_unit_run(
            self,
            task_id,
            unit_id,
            *,
            worker_id,
            run_id,
        ):
            self.binds.append((task_id, unit_id, worker_id, run_id))

    repository = _ExecutionRepository()
    class _Composition:
        database = _Database()
        execution_owner_id = "worker-1"

        def __init__(self):
            self.events = []

        async def append_run_event(self, run_id, event_type, payload):
            self.events.append((run_id, event_type, payload))

    composition = _Composition()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=repository,
        work_items=object(),
        body=_Body(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
        delegation_service=_DelegationService(),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=2,
        completed_units=0,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )
    unit = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.CLAIMED,
        attempt=1,
        max_attempts=3,
        worker_id="worker-1",
        metadata={"sceneIds": ["s05"]},
    )

    result = await execution.run_unit(task, unit)

    validators = captured["response_validators"]
    assert len(validators) == 1
    assert isinstance(validators[0], ScreenplayDraftBatchResponseValidator)
    assert captured["lineage"].parent_run_id == "run-parent"
    assert captured["lineage"].delegation_id == "delegation-writer-1"
    assert captured["lineage"].agent_role == "screenplay_writer"
    assert repository.binds == [
        ("task-1", "batch-0001", "worker-1", "run-child")
    ]
    assert composition.events == [
        (
            "run-child",
            "screenplay.long_task.thinking_snapshot",
            {
                "taskId": "task-1",
                "unitId": "batch-0001",
                "attempt": 1,
                "content": "先确认上一场连续性。",
            },
        ),
        (
            "run-child",
            "screenplay.long_task.response",
            {
                "taskId": "task-1",
                "unitId": "batch-0001",
                "attempt": 1,
                "content": "已完成 s05 正文，并让林月进入下一场。",
            },
        ),
    ]
    assert result.run_id == "run-child"
    assert result.metadata["sceneIds"] == ["s05"]


@pytest.mark.asyncio
async def test_canceled_batch_does_not_publish_completed_response(monkeypatch):
    signal = asyncio.Event()

    async def _run(_service, **kwargs):
        del kwargs
        yield AgentEvent(type="run.started", run_id="run-child", payload={})
        yield AgentEvent(
            type="model.thinking_delta",
            run_id="run-child",
            payload={"delta": "正在创作。"},
        )
        signal.set()
        yield AgentRunResult(
            run_id="run-child",
            status=RunStatus.DONE,
            final_response=_valid_batch_response(),
        )

    monkeypatch.setattr(
        "application.agent_run_service.AgentRunService.run",
        _run,
    )

    class _Body:
        def model_copy(self, *, update):
            del update
            return self

    class _Repository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return ()

        async def bind_unit_run(self, *args, **kwargs):
            del args, kwargs

    class _Composition:
        database = _Database()
        execution_owner_id = "worker-1"

        def __init__(self):
            self.events = []

        async def append_run_event(self, run_id, event_type, payload):
            self.events.append((run_id, event_type, payload))

    composition = _Composition()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_Repository(),
        work_items=object(),
        body=_Body(),
        api_key="key",
        provider_options={},
        signal=signal,
        delegation_service=_DelegationService(),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=2,
        completed_units=0,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )
    unit = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.CLAIMED,
        worker_id="worker-1",
        metadata={"sceneIds": ["s05"]},
    )

    with pytest.raises(RuntimeError, match="long_task_execution_canceled"):
        await execution.run_unit(task, unit)

    assert [event_type for _, event_type, _ in composition.events] == [
        "screenplay.long_task.thinking_snapshot"
    ]


@pytest.mark.asyncio
async def test_finalize_builds_scene_headings_and_publishes_proposal():
    scene = json.loads(_valid_batch_response())["scenes"][0]
    scene["execution"] = {
        "sceneId": scene["sceneId"],
        "structureUnitIds": [],
        **scene["execution"],
    }
    completed_unit = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.COMPLETED,
        metadata={"unitKind": "scene_generation", "scenes": [scene]},
    )

    class _FinalizeRepository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return (completed_unit,)

    class _WorkItems:
        def __init__(self):
            self.command = None

        async def get(self, work_item_id):
            return type("WorkItem", (), {"id": work_item_id, "revision": 4})()

        async def complete(self, command):
            self.command = command

    class _Composition:
        database = _Database()

        def __init__(self):
            self.events = []

        async def append_run_event(self, run_id, event_type, payload):
            self.events.append((run_id, event_type, payload))

    composition = _Composition()
    work_items = _WorkItems()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_FinalizeRepository(),
        work_items=work_items,
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=2,
        completed_units=1,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
            "scope": "count",
            "targetSceneIds": ["s05"],
        },
    )

    result = await execution._finalize(task)

    proposal = result.metadata["proposal"]
    assert proposal["contentJson"]["newSceneHeadings"] == ["第五场"]
    assert proposal["contentJson"]["completedSceneIds"] == ["s05"]
    # Finalization returns the proposal to the root execution stream; it no
    # longer writes around Agent Core through a detached parent Run.
    assert composition.events == []
    assert work_items.command is not None


@pytest.mark.asyncio
async def test_finalize_deterministically_applies_planner_review_revision():
    writer_scene = json.loads(_valid_batch_response())["scenes"][0]
    writer_scene["execution"] = {
        "sceneId": "s05",
        "structureUnitIds": [],
        **writer_scene["execution"],
    }
    reviewer_scene = json.loads(_valid_batch_response())["scenes"][0]
    reviewer_scene["sceneText"] = "EXT. 犬域 - 日\n\n@林月 接住上一场的道具。"
    reviewer_scene["execution"] = {
        "sceneId": "s05",
        "structureUnitIds": [],
        **reviewer_scene["execution"],
    }
    units = (
        LongTaskUnitRecord(
            task_id="task-1",
            id="writer",
            position=0,
            status=LongTaskUnitStatus.COMPLETED,
            metadata={"unitKind": "scene_generation", "scenes": [writer_scene]},
        ),
        LongTaskUnitRecord(
            task_id="task-1",
            id="reviewer",
            position=1,
            status=LongTaskUnitStatus.COMPLETED,
            dependencies=("writer",),
            metadata={"unitKind": "continuity_review", "scenes": [reviewer_scene]},
        ),
    )

    class _FinalizeRepository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return units

    class _WorkItems:
        async def get(self, work_item_id):
            return type("WorkItem", (), {"id": work_item_id, "revision": 1})()

        async def complete(self, command):
            del command

    composition = type("Composition", (), {"database": _Database()})()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_FinalizeRepository(),
        work_items=_WorkItems(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=3,
        total_units=3,
        completed_units=2,
        failed_units=0,
        max_parallelism=2,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
            "scope": "count",
            "targetSceneIds": ["s05"],
        },
    )

    result = await execution._finalize(task)

    proposal = result.metadata["proposal"]
    assert proposal["contentText"].endswith(reviewer_scene["sceneText"])
    assert proposal["contentJson"]["continuityReviewedSceneIds"] == ["s05"]


@pytest.mark.asyncio
async def test_finalize_rejects_incomplete_episode_scope():
    scene = json.loads(_valid_batch_response())["scenes"][0]
    completed_unit = LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=LongTaskUnitStatus.COMPLETED,
        metadata={"unitKind": "scene_generation", "scenes": [scene]},
    )

    class _FinalizeRepository:
        async def list_units(self, task_id):
            return (completed_unit,)

    composition = type("Composition", (), {"database": _Database()})()
    execution = ScreenplayLongTaskExecution(
        composition=composition,
        repository=_FinalizeRepository(),
        work_items=object(),
        body=object(),
        api_key="key",
        provider_options={},
        signal=asyncio.Event(),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.RUNNING,
        revision=2,
        total_units=2,
        completed_units=1,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
            "scope": "next_episodes",
            "targetSceneIds": ["s05"],
        },
    )

    with pytest.raises(RuntimeError, match="long_task_final_scope_mismatch"):
        await execution._finalize(task)
