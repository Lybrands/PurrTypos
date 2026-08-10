from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from application.screenplay_agent_service import (
    PlannedScreenplayIntent,
    ResolvedScreenplayTask,
    ScreenplayAgentService,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from domains.screenplay_agent import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayIntentScope,
    ScreenplayOperationCreateCommand,
)
from domains.screenplay_agent.contracts import ScreenplayScopeKind
from infrastructure.persistence.sqlite_screenplay_task_output_store import (
    SqliteScreenplayTaskOutputStore,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from purra.long_tasks import LongTaskUnitResult
from purra.errors import ModelGatewayError
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest


@pytest_asyncio.fixture
async def screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


class _Planner:
    def __init__(self, intent: ScreenplayIntent) -> None:
        self.intent = intent

    async def plan(self, **_kwargs):
        return PlannedScreenplayIntent(self.intent, "run-screenplay-planner")


class _Resolver:
    async def resolve(self, **_kwargs):
        return ResolvedScreenplayTask(
            target_role="screenplayDraft",
            episode_numbers=(4, 5, 6),
        )


class _UnitExecutor:
    def __init__(self, db) -> None:
        self._outputs = SqliteScreenplayTaskOutputStore(db)
        self.calls = []

    async def execute(self, context, signal=None):
        del signal
        self.calls.append((context.unit.id, dict(context.dependency_outputs)))
        if context.unit.id == "compose-final-response":
            output = {
                "finalResponse": (
                    "第 4 至 6 集候选稿已经完成。"
                    "可以在候选稿区域查看并继续编辑。"
                ),
                "runId": "run-compose-final-response",
            }
        elif context.unit.id == "publish-candidate":
            output = {"revisionId": "sprev-durable-candidate"}
        else:
            output = {"episodeNumber": int(context.unit.id.rsplit("-", 1)[1])}
        output_ref = await self._outputs.put(
            task_id=context.task.id,
            unit_id=context.unit.id,
            output=output,
        )
        return LongTaskUnitResult(
            output_ref=output_ref,
            run_id=str(output.get("runId") or "") or None,
            metadata=(
                {"revisionId": output["revisionId"]}
                if "revisionId" in output else {}
            ),
        )


class _PausedUnitExecutor:
    async def execute(self, context, signal=None):
        del context, signal
        raise ModelGatewayError(
            "selected protocol is incompatible",
            code="provider_bad_request",
            retryable=False,
        )

    def classify_failure(self, error):
        return classify_screenplay_run_failure(error)


@pytest.mark.asyncio
async def test_paused_operation_retains_session_control_until_terminal(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-operation-control-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Operation control",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    project_id = workspace["project"]["id"]
    session = await projects.ensure_current_session(project_id)
    for suffix in ("first", "second"):
        await screenplay_db.execute(
            "INSERT INTO screenplay_agent_turns "
            "(id, project_id, session_id, command_id, status, user_content) "
            "VALUES (?, ?, ?, ?, 'completed', ?)",
            [
                f"operation-turn-{suffix}",
                project_id,
                session["id"],
                f"operation-command-{suffix}",
                "生成剧本",
            ],
        )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    first_command = ScreenplayOperationCreateCommand(
        turn_id="operation-turn-first",
        project_id=project_id,
        session_id=session["id"],
        target_role="screenplayDraft",
        requirements_json={"intent": {"action": "create"}},
        manifest_digest="sha256:first",
    )
    second_command = ScreenplayOperationCreateCommand(
        turn_id="operation-turn-second",
        project_id=project_id,
        session_id=session["id"],
        target_role="screenplayDraft",
        requirements_json={"intent": {"action": "create"}},
        manifest_digest="sha256:second",
    )
    first = await operations.create(first_command)
    await operations.pause(
        first.id,
        code="model_output_truncated",
        message="需要恢复",
        command_id="pause-first-operation",
    )

    with pytest.raises(ValueError, match="active Operation"):
        await operations.create(second_command)

    await operations.fail(
        first.id,
        code="user_abandoned",
        message="不再恢复",
        command_id="fail-first-operation",
    )
    second = await operations.create(second_command)
    assert second.status.value == "queued"


@pytest.mark.asyncio
async def test_screenplay_answer_turn_does_not_create_a_durable_task(screenplay_db):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-answer-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Answer screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-answer-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.ANSWER,
            instruction="解释当前阶段",
            reply="当前处于创作简报阶段。",
        )),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: None,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "现在进行到哪一步了？",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="answer-current-stage",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["tasks"] == []
    assert snapshot["turns"][0]["status"] == "completed"
    assert snapshot["turns"][0]["assistantContent"] == "当前处于创作简报阶段。"
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_screenplay_execution_uses_purra_task_without_job_state(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-durable-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Durable screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="完成接下来三集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=3,
        ),
        requested_deliverable="screenplayDraft",
    )
    executor = _UnitExecutor(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-durable-test",
        planner=_Planner(intent),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "不要只写下一集，连续写完后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="durable-next-three",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    task = snapshot["tasks"][0]
    assert snapshot["turns"][0]["taskId"] == task["id"]
    assert snapshot["turns"][0]["resultRevisionId"] == (
        "sprev-durable-candidate"
    )
    assert snapshot["turns"][0]["assistantContent"] == (
        "第 4 至 6 集候选稿已经完成。可以在候选稿区域查看并继续编辑。"
    )
    operation = await screenplay_db.fetch_one(
        "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    )
    assert operation is not None
    assert operation["status"] == "succeeded"
    assert operation["long_task_id"] == task["id"]
    assert operation["target_role"] == "screenplayDraft"
    assert operation["result_revision_id"] == "sprev-durable-candidate"
    assert str(operation["manifest_digest"]).startswith("sha256:")
    legacy_turn_state = await screenplay_db.fetch_one(
        "SELECT operation_id, task_id, target_role, result_revision_id, error_json "
        "FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert legacy_turn_state == {
        "operation_id": operation["id"],
        "task_id": None,
        "target_role": None,
        "result_revision_id": None,
        "error_json": None,
    }
    assert task["status"] == "completed"
    assert task["resultRevision"] is None
    assert [unit["id"] for unit in task["units"]] == [
        "collect-evidence-episode-4",
        "generate-candidate-episode-4",
        "validate-candidate-episode-4",
        "collect-evidence-episode-5",
        "generate-candidate-episode-5",
        "validate-candidate-episode-5",
        "collect-evidence-episode-6",
        "generate-candidate-episode-6",
        "validate-candidate-episode-6",
        "compose-final-response",
        "publish-candidate",
    ]
    assert executor.calls == [
        ("collect-evidence-episode-4", {}),
        (
            "generate-candidate-episode-4",
            {"collect-evidence-episode-4": f"screenplay-task-output://{task['id']}/collect-evidence-episode-4"},
        ),
        (
            "validate-candidate-episode-4",
            {"generate-candidate-episode-4": f"screenplay-task-output://{task['id']}/generate-candidate-episode-4"},
        ),
        (
            "collect-evidence-episode-5",
            {"validate-candidate-episode-4": f"screenplay-task-output://{task['id']}/validate-candidate-episode-4"},
        ),
        (
            "generate-candidate-episode-5",
            {"collect-evidence-episode-5": f"screenplay-task-output://{task['id']}/collect-evidence-episode-5"},
        ),
        (
            "validate-candidate-episode-5",
            {"generate-candidate-episode-5": f"screenplay-task-output://{task['id']}/generate-candidate-episode-5"},
        ),
        (
            "collect-evidence-episode-6",
            {"validate-candidate-episode-5": f"screenplay-task-output://{task['id']}/validate-candidate-episode-5"},
        ),
        (
            "generate-candidate-episode-6",
            {"collect-evidence-episode-6": f"screenplay-task-output://{task['id']}/collect-evidence-episode-6"},
        ),
        (
            "validate-candidate-episode-6",
            {"generate-candidate-episode-6": f"screenplay-task-output://{task['id']}/generate-candidate-episode-6"},
        ),
        (
            "compose-final-response",
            {
                "validate-candidate-episode-4": f"screenplay-task-output://{task['id']}/validate-candidate-episode-4",
                "validate-candidate-episode-5": f"screenplay-task-output://{task['id']}/validate-candidate-episode-5",
                "validate-candidate-episode-6": f"screenplay-task-output://{task['id']}/validate-candidate-episode-6",
            },
        ),
        (
            "publish-candidate",
            {
                "validate-candidate-episode-4": f"screenplay-task-output://{task['id']}/validate-candidate-episode-4",
                "validate-candidate-episode-5": f"screenplay-task-output://{task['id']}/validate-candidate-episode-5",
                "validate-candidate-episode-6": f"screenplay-task-output://{task['id']}/validate-candidate-episode-6",
                "compose-final-response": f"screenplay-task-output://{task['id']}/compose-final-response",
            },
        ),
    ]
    assert await screenplay_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('screenplay_agent_jobs', 'screenplay_agent_job_steps')"
    ) == []
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 1}
    chunks = [
        json.loads(row["chunk_json"])
        for row in await screenplay_db.fetch_all(
            "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
        )
    ]
    assert not any("commentaryDelta" in chunk for chunk in chunks)
    progress = [
        chunk["longTaskProgress"]
        for chunk in chunks
        if "longTaskProgress" in chunk
    ]
    assert progress
    assert progress[-1]["status"] == "completed"
    assert progress[-1]["completedUnits"] == 11
    assert [unit["title"] for unit in progress[-1]["units"]] == [
        "整理第 4 集创作依据",
        "创作第 4 集候选稿",
        "校验第 4 集候选稿",
        "整理第 5 集创作依据",
        "创作第 5 集候选稿",
        "校验第 5 集候选稿",
        "整理第 6 集创作依据",
        "创作第 6 集候选稿",
        "校验第 6 集候选稿",
        "整理最终答复",
        "整理并发布候选稿",
    ]
    assert any(
        any(unit["status"] == "claimed" for unit in snapshot["units"])
        for snapshot in progress
    )
    assert all(unit["status"] == "completed" for unit in progress[-1]["units"])

    removed = await service.truncate_from_turn(turn["id"])
    assert removed["deletedTaskIds"] == [task["id"]]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_recoverable_exhaustion_pauses_turn_without_formal_assistant_final(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-paused-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Paused screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-paused-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="完成接下来三集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=3,
            ),
            requested_deliverable="screenplayDraft",
        )),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: _PausedUnitExecutor(),
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "连续写完后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="pause-incompatible-protocol",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["turns"][0]["status"] == "paused"
    assert snapshot["turns"][0]["assistantContent"] == ""
    assert snapshot["tasks"][0]["status"] == "paused"
    assert snapshot["tasks"][0]["units"][0]["status"] == "blocked"
    events = await service.list_events(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        after=0,
        limit=100,
    )
    assert any(
        event["type"] == "screenplay.agent.task.paused"
        for event in events["events"]
    )
    assert not any(
        event["type"] == "screenplay.agent.task.failed"
        for event in events["events"]
    )
    chunks = [
        json.loads(row["chunk_json"])
        for row in await screenplay_db.fetch_all(
            "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
        )
    ]
    assert not any(chunk.get("delta") for chunk in chunks)
    assert chunks[-1] == {
        "done": True,
        "finalResponseExpected": False,
    }
