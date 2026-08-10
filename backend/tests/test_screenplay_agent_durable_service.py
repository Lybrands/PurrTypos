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
)
from domains.screenplay_agent.contracts import ScreenplayScopeKind
from infrastructure.persistence.sqlite_screenplay_task_output_store import (
    SqliteScreenplayTaskOutputStore,
)
from purra.long_tasks import LongTaskUnitResult
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
        if context.unit.id == "publish-candidate":
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
            metadata=(
                {
                    "revisionId": output["revisionId"],
                    "finalResponse": (
                        "剧本任务已完成，候选稿已生成。请在下方预览并应用。"
                    ),
                }
                if "revisionId" in output
                else {}
            ),
        )


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
    assert task["status"] == "completed"
    assert [unit["id"] for unit in task["units"]] == [
        "draft-episode-4",
        "draft-episode-5",
        "draft-episode-6",
        "publish-candidate",
    ]
    assert executor.calls == [
        ("draft-episode-4", {}),
        (
            "draft-episode-5",
            {"draft-episode-4": f"screenplay-task-output://{task['id']}/draft-episode-4"},
        ),
        (
            "draft-episode-6",
            {"draft-episode-5": f"screenplay-task-output://{task['id']}/draft-episode-5"},
        ),
        (
            "publish-candidate",
            {
                "draft-episode-4": f"screenplay-task-output://{task['id']}/draft-episode-4",
                "draft-episode-5": f"screenplay-task-output://{task['id']}/draft-episode-5",
                "draft-episode-6": f"screenplay-task-output://{task['id']}/draft-episode-6",
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
    assert progress[-1]["completedUnits"] == 4
    assert [unit["title"] for unit in progress[-1]["units"]] == [
        "创作第 4 集正文",
        "创作第 5 集正文",
        "创作第 6 集正文",
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
