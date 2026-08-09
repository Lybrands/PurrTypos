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
from agent_core.contracts import (
    AgentRunResult,
    RunLineage,
    RunStatus,
)
from agent_core.events import AgentEvent, CoreEventType
from application.screenplay_long_task_execution import ScreenplayLongTaskExecution
from domains.screenplay.long_task_response import (
    ScreenplayDraftBatchResponseValidator,
    ScreenplayReviewReportValidator,
)


class _Database:
    async def fetch_one(self, query, params):
        if "FROM screenplay_projects" in query:
            return {
                "id": "project-1",
                "format": "series",
                "source_snapshot_json": '{"type":"original"}',
            }
        if "FROM screenplay_revisions AS r" in query:
            revision_id = str(params[0])
            if revision_id == "scene-list-1":
                return {
                    "id": revision_id,
                    "project_id": "project-1",
                    "revision_no": 1,
                    "role": "sceneList",
                    "payload_json": {
                        "schemaVersion": 2,
                        "documentKind": "scene_list",
                        "scenes": [
                            {"id": "s05", "heading": "第五场", "episodeNumber": 1},
                            {"id": "s06", "heading": "第六场", "episodeNumber": 1},
                        ],
                    },
                    "document_payload": {
                        "schemaVersion": 2,
                        "documentKind": "scene_list",
                        "scenes": [
                            {"id": "s05", "heading": "第五场", "episodeNumber": 1},
                            {"id": "s06", "heading": "第六场", "episodeNumber": 1},
                        ],
                    },
                    "summary_json": {"title": "场景表"},
                    "part_content_text": "",
                    "is_head": 1,
                }
            if revision_id == "draft-1":
                return {
                    "id": revision_id,
                    "project_id": "project-1",
                    "revision_no": 1,
                    "role": "screenplayDraft",
                    "payload_json": {
                        "schemaVersion": 2,
                        "documentKind": "scene_draft",
                    },
                    "document_payload": {
                        "schemaVersion": 2,
                        "documentKind": "scene_draft",
                    },
                    "summary_json": {"title": "正文"},
                    "part_content_text": "",
                    "is_head": 1,
                }
            return None
        if "SELECT project_id FROM screenplay_revisions" in query:
            return {"project_id": "project-1"}
        if "kind = 'scene_list'" in query:
            return {
                "id": "scene-list-1",
                "kind": "scene_list",
                "content_json": {
                    "schemaVersion": 2,
                    "documentKind": "scene_list_manifest",
                    "storageMode": "episode_documents",
                    "episodeCount": 1,
                },
            }
        if "kind = 'scene_draft'" in query:
            return {
                "id": "draft-1",
                "project_id": "project-1",
                "kind": "scene_draft",
                "content_json": {
                    "schemaVersion": 2,
                    "documentKind": "scene_draft_manifest",
                },
                "content_text": "",
            }
        raise AssertionError((query, params))

    async def fetch_all(self, query, params):
        if "FROM screenplay_project_heads AS h" in query:
            return [{
                "id": "scene-list-1",
                "project_id": "project-1",
                "deliverable_id": "scene-list-deliverable",
                "role": "sceneList",
                "revision_no": 1,
                "payload_json": {
                    "schemaVersion": 2,
                    "documentKind": "scene_list",
                    "scenes": [
                        {"id": "s05", "heading": "第五场", "episodeNumber": 1},
                        {"id": "s06", "heading": "第六场", "episodeNumber": 1},
                    ],
                },
                "summary_json": {"title": "场景表"},
                "part_content_text": "",
            }, {
                "id": "draft-1",
                "project_id": "project-1",
                "deliverable_id": "draft-deliverable",
                "role": "screenplayDraft",
                "revision_no": 1,
                "payload_json": {
                    "schemaVersion": 2,
                    "documentKind": "scene_draft",
                },
                "summary_json": {"title": "正文"},
                "part_content_text": "",
            }]
        if "FROM screenplay_revision_inputs" in query:
            return []
        if (
            "FROM screenplay_revision_parts" in query
            and "part_type = 'episode'" in query
        ):
            revision_id = str(params[0])
            if revision_id == "scene-list-1":
                return [{
                    "part_key": "1",
                    "position": 1,
                    "payload_json": {"episodeNumber": 1, "scenes": [
                    {"id": "s05", "heading": "第五场", "episodeNumber": 1},
                    {"id": "s06", "heading": "第六场", "episodeNumber": 1},
                    ]},
                    "content_text": "",
                }]
            if revision_id == "draft-1":
                return [{
                    "part_key": "1",
                    "position": 1,
                    "payload_json": {
                        "episodeNumber": 1,
                        "scenes": [],
                        "sceneExecutions": [],
                        "contentText": "已接受正文",
                    },
                    "content_text": "已接受正文",
                }]
            return []
        if "screenplay_document_episodes" in query:
            raise AssertionError("legacy episode table must not be queried")
        if "screenplay_draft_episodes" in query:
            raise AssertionError("legacy draft table must not be queried")
        if "FROM screenplay_documents" in query:
            raise AssertionError("legacy document table must not be queried")
        if "FROM screenplay_revision_parts" in query:
            return [{
                "part_key": "1",
                "position": 1,
                "payload_json": {},
                "content_text": "",
            }]
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

    assert payload["task"] == "review_screenplay_continuity"
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


def _review_and_revision_units(
    scene,
    *,
    writer_id="writer",
    writer_position=0,
    revised_scene=None,
    review_issues=None,
):
    scene_id = str(scene["sceneId"])
    review_id = "review_draft_continuity"
    return (
        LongTaskUnitRecord(
            task_id="task-1",
            id=writer_id,
            position=writer_position,
            status=LongTaskUnitStatus.COMPLETED,
            metadata={"unitKind": "scene_generation", "scenes": [scene]},
        ),
        LongTaskUnitRecord(
            task_id="task-1",
            id=review_id,
            position=writer_position + 1,
            status=LongTaskUnitStatus.COMPLETED,
            dependencies=(writer_id,),
            metadata={
                "unitKind": "continuity_review",
                "reviewReport": {
                    "reviewedSceneIds": [scene_id],
                    "issues": list(review_issues or []),
                    "summary": "连续性检查完成",
                },
            },
        ),
        LongTaskUnitRecord(
            task_id="task-1",
            id=f"revise_{scene_id}",
            position=writer_position + 2,
            status=LongTaskUnitStatus.COMPLETED,
            dependencies=(writer_id, review_id),
            metadata={
                "unitKind": "scene_revision",
                "scenes": [revised_scene or scene],
                "skippedModelCall": revised_scene is None,
            },
        ),
    )


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


def test_review_contract_is_compact_and_rejects_rewritten_scene_payloads():
    validator = ScreenplayReviewReportValidator(("s05", "s06"))
    report = {
        "reviewedSceneIds": ["s05", "s06"],
        "issues": [{
            "id": "timeline-1",
            "sceneIds": ["s06"],
            "severity": "major",
            "category": "timeline",
            "problem": "时间状态与上一场冲突",
            "instruction": "承接上一场的夜间时间",
        }],
        "summary": "发现一处跨场时间问题",
    }

    assert validator.validate(
        content=json.dumps(report, ensure_ascii=False),
        messages=(),
    ).accepted is True
    report["scenes"] = json.loads(_valid_batch_response())["scenes"]
    assert validator.validate(
        content=json.dumps(report, ensure_ascii=False),
        messages=(),
    ).accepted is False


def test_screenplay_unit_retry_policy_never_repeats_truncated_output():
    classifier = ScreenplayLongTaskExecution.is_retryable_unit_error

    assert classifier(RuntimeError("model_output_truncated")) is False
    assert classifier(RuntimeError("long_task_batch_output_invalid_json")) is False
    assert classifier(RuntimeError("upstream_stream_interrupted")) is True
    assert classifier(RuntimeError("provider_unavailable")) is True


@pytest.mark.asyncio
async def test_clean_review_revision_reuses_writer_checkpoint_without_model_call():
    scene = json.loads(_valid_batch_response())["scenes"][0]
    writer, review, _ = _review_and_revision_units(scene)
    revision = LongTaskUnitRecord(
        task_id="task-1",
        id="revise_s05",
        position=2,
        status=LongTaskUnitStatus.CLAIMED,
        dependencies=(writer.id, review.id),
        metadata={"unitKind": "scene_revision", "sceneIds": ["s05"]},
    )

    class _RevisionRepository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return (writer, review, revision)

    execution = ScreenplayLongTaskExecution(
        composition=type("Composition", (), {"database": _Database()})(),
        repository=_RevisionRepository(),
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
        total_units=4,
        completed_units=2,
        failed_units=0,
        max_parallelism=1,
        metadata={
            "projectId": "project-1",
            "sceneListDocumentId": "scene-list-1",
        },
    )

    result = await execution._passthrough_revision_if_clean(task, revision)

    assert result is not None
    assert result.metadata["skippedModelCall"] is True
    assert result.metadata["scenes"][0]["sceneText"] == scene["sceneText"]


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
    assert captured["bodyUpdate"]["enableAgentTools"] is False
    assert captured["bodyUpdate"]["chatAgentMode"] == "ask"
    assert captured["bodyUpdate"]["screenplayOperationId"] is None
    assert captured["allowed_tool_modes"] == frozenset()
    assert "required_tool_names" not in captured
    assert captured["domain_context_overrides"] == {
        "bound_draft_scene_ids": ["s05"],
    }
    assert captured["host_context_only"] is True
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
    units = _review_and_revision_units(scene)

    class _FinalizeRepository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return units

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
async def test_finalize_reads_previous_execution_history_from_episode_rows():
    previous_execution = {
        "sceneId": "s04",
        "structureUnitIds": [],
        "objectiveResult": "完成上一场目标",
        "conflictResult": "推进上一场冲突",
        "turnResult": "完成上一场转折",
        "continuityState": "人物进入第五场",
        "unresolvedNotes": [],
    }

    class _EpisodeNativeDatabase(_Database):
        async def fetch_all(self, query, params):
            if (
                "FROM screenplay_revision_parts" in query
                and "part_type = 'episode'" in query
                and str(params[0]) == "scene-list-1"
            ):
                return [{
                    "part_key": "1",
                    "position": 1,
                    "payload_json": {"episodeNumber": 1, "scenes": [{
                        "id": "s04",
                        "heading": "第四场",
                        "episodeNumber": 1,
                    }, {
                        "id": "s05",
                        "heading": "第五场",
                        "episodeNumber": 1,
                    }]},
                    "content_text": "",
                }]
            if (
                "FROM screenplay_revision_parts" in query
                and "part_type = 'episode'" in query
                and str(params[0]) == "draft-1"
            ):
                return [{
                    "part_key": "1",
                    "position": 1,
                        "payload_json": {
                            "episodeNumber": 1,
                            "sceneIds": ["s04"],
                            "sceneTexts": [{
                                "sceneId": "s04",
                                "contentText": "第四场正文",
                            }],
                        "sceneExecutions": [previous_execution],
                        "contentText": "第四场正文",
                        "continuitySummary": "人物进入第五场",
                    },
                    "content_text": "第四场正文",
                }]
            return await super().fetch_all(query, params)

    scene = json.loads(_valid_batch_response())["scenes"][0]
    scene["execution"] = {
        "sceneId": "s05",
        "structureUnitIds": [],
        **scene["execution"],
    }
    units = _review_and_revision_units(scene)

    class _FinalizeRepository:
        async def list_units(self, task_id):
            assert task_id == "task-1"
            return units

    class _WorkItems:
        async def get(self, work_item_id):
            return type("WorkItem", (), {"id": work_item_id, "revision": 1})()

        async def complete(self, command):
            del command

    composition = type(
        "Composition",
        (),
        {"database": _EpisodeNativeDatabase()},
    )()
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
    assert proposal["contentJson"]["completedSceneIds"] == ["s04", "s05"]
    assert proposal["contentJson"]["episodeDrafts"][0][
        "sceneExecutions"
    ][0]["sceneId"] == "s05"


@pytest.mark.asyncio
async def test_finalize_deterministically_applies_targeted_review_revision():
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
    units = _review_and_revision_units(
        writer_scene,
        revised_scene=reviewer_scene,
        review_issues=[{
            "id": "continuity-1",
            "sceneIds": ["s05"],
            "severity": "major",
            "category": "prop",
            "problem": "上一场道具没有承接",
            "instruction": "让林月接住上一场道具",
        }],
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
    units = _review_and_revision_units(scene)

    class _FinalizeRepository:
        async def list_units(self, task_id):
            return units

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
