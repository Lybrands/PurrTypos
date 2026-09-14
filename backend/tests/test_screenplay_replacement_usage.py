from __future__ import annotations

import json

import pytest
import pytest_asyncio

from agents.screenplay.contracts import ScreenplayPartOperationScope
from agents.screenplay.usage import ScreenplayOperationUsageStore
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from purra.long_tasks import (
    LongTaskBudgetLimits,
    LongTaskCreateCommand,
    LongTaskUnitSpec,
)


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    for run_id in ("root-a", "root-b"):
        await create_run(
            db,
            run_id=run_id,
            session_id=None,
            prompt="usage fixture",
            mode="screenplay",
        )
    try:
        yield db
    finally:
        await db.close()


def _scope(unit_id: str, attempt: int) -> ScreenplayPartOperationScope:
    return ScreenplayPartOperationScope(
        project_id="project-usage",
        task_id="task-usage",
        unit_id=unit_id,
        attempt=attempt,
        part_kind="document_section",
        part_key=f"section:{unit_id}",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )


async def _event(db, root_run_id: str, event_type: str, scope, payload):
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, ?, ?)",
        [
            root_run_id,
            event_type,
            json.dumps({**payload, "executionScopeId": scope.operation_scope_id}),
        ],
    )


@pytest.mark.asyncio
async def test_operation_attempt_usage_aggregates_once_per_root(temp_db) -> None:
    repository = SqliteLongTaskRepository(temp_db)
    task = await repository.create("task-usage", LongTaskCreateCommand(
        namespace="purrtypos.screenplay",
        kind="screenplay.purra-native",
        owner_id="project-usage",
        created_by_run_id="root-a",
        units=(LongTaskUnitSpec(id="host", position=0),),
        budget_limits=LongTaskBudgetLimits(max_invocation_attempts=10),
    ))
    await repository.start(task.id, expected_revision=task.revision)
    first = _scope("part-a", 1)
    retry = _scope("part-a", 2)
    parallel = _scope("part-b", 1)
    await _event(temp_db, "root-a", "model.call_recorded", first, {"count": 1})
    await _event(temp_db, "root-a", "context.usage_recorded", first, {
        "actualInputTokens": 10,
        "actualGenerationTokens": 4,
        "reasoningTokens": 2,
    })
    await _event(temp_db, "root-a", "model.call_recorded", retry, {"count": 2})
    await _event(temp_db, "root-a", "context.usage_recorded", retry, {
        "actualInputTokens": 20,
        "actualGenerationTokens": 8,
        "reasoningTokens": 3,
    })
    await _event(temp_db, "root-b", "model.call_recorded", parallel, {"count": 1})
    await _event(temp_db, "root-b", "context.usage_recorded", parallel, {
        "actualInputTokens": 7,
        "actualGenerationTokens": 5,
        "reasoningTokens": 1,
    })

    store = ScreenplayOperationUsageStore(temp_db)
    first_receipt = await store.record_from_events(
        scope=first,
        root_run_id="root-a",
    )
    retry_receipt = await store.record_from_events(
        scope=retry,
        root_run_id="root-a",
    )
    await store.record_from_events(scope=parallel, root_run_id="root-b")
    settlement = await store.settle_task_roots(task.id, repository)
    replay = await store.settle_task_roots(task.id, repository)

    assert first_receipt.usage.to_mapping() == {
        "invocationCount": 1,
        "unreportedUsageAttempts": 0,
        "inputTokens": 10,
        "generationTokens": 4,
        "reasoningTokens": 2,
    }
    assert retry_receipt.usage.unreported_usage_attempts == 1
    assert retry_receipt.usage.reasoning_tokens is None
    assert settlement == replay
    assert settlement["total"] == {
        "invocationCount": 4,
        "unreportedUsageAttempts": 1,
        "inputTokens": 37,
        "generationTokens": 17,
        "reasoningTokens": None,
    }
    assert await temp_db.fetch_all(
        "SELECT run_id, invocation_count, unreported_usage_attempts "
        "FROM ai_agent_long_task_usage WHERE task_id = ? ORDER BY run_id",
        [task.id],
    ) == [
        {
            "run_id": "root-a",
            "invocation_count": 3,
            "unreported_usage_attempts": 1,
        },
        {
            "run_id": "root-b",
            "invocation_count": 1,
            "unreported_usage_attempts": 0,
        },
    ]


@pytest.mark.asyncio
async def test_operation_usage_is_immutable_after_observation(temp_db) -> None:
    scope = _scope("part-a", 1)
    await _event(temp_db, "root-a", "model.call_recorded", scope, {"count": 1})
    store = ScreenplayOperationUsageStore(temp_db)
    await store.record_from_events(scope=scope, root_run_id="root-a")
    await _event(temp_db, "root-a", "model.call_recorded", scope, {"count": 1})

    with pytest.raises(
        ValueError,
        match="Screenplay Operation usage receipt conflicts",
    ):
        await store.record_from_events(scope=scope, root_run_id="root-a")
