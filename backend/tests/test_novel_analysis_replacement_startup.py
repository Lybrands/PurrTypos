from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from agents.novel_analysis.automatic_recovery import (
    NovelAnalysisReplacementAutomaticRecovery,
    retire_misclassified_failure_pauses,
)
from agents.novel_analysis.reliability_baseline import (
    NovelAnalysisReliabilityBaselineService,
)
from agents.novel_analysis.recovery_service import (
    NovelAnalysisReplacementContinuationLifecycle,
)
from agents.novel_analysis.scalable_profile import (
    scalable_novel_analysis_implementation,
)
from agents.shared.implementation import (
    AgentKind,
    legacy_implementation,
)
from agents.shared.implementation_store import SqliteAgentImplementationStore
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.run_store import create_run


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


class _Composition:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def track_background_run(self, task: asyncio.Task[None]) -> None:
        self.tasks.append(task)


class _Recovery:
    def __init__(self, *, blocker: asyncio.Event | None = None) -> None:
        self.blocker = blocker
        self.resolved: list[str] = []
        self.calls: list[dict[str, object]] = []

    async def resolve_automatic_runtime(self, task_id: str):
        self.resolved.append(task_id)
        return object()

    async def resume(self, **kwargs):
        self.calls.append(kwargs)
        if self.blocker is not None:
            await self.blocker.wait()
        yield {"status": "continued"}


class _LifecycleRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def resume(
        self,
        task_id: str,
        *,
        additional_attempts: int,
        recovery_source: str,
    ) -> None:
        self.calls.append((task_id, additional_attempts, recovery_source))


@pytest.mark.asyncio
async def test_automatic_recovery_filters_task_kind_and_persisted_identity(
    temp_db,
) -> None:
    replacement_run = await _run(
        temp_db,
        "replacement-run",
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    legacy_run = await _run(
        temp_db,
        "legacy-run",
        legacy_implementation(AgentKind.NOVEL_ANALYSIS),
    )
    await _paused_task(
        temp_db,
        task_id="replacement-task",
        run_id=replacement_run,
        kind="novel_analysis.scalable.v2",
        owner_id="revision-replacement",
    )
    await _paused_task(
        temp_db,
        task_id="legacy-kind-task",
        run_id=legacy_run,
        kind="novel_source_analysis",
        owner_id="revision-legacy-kind",
    )
    await _paused_task(
        temp_db,
        task_id="legacy-owner-task",
        run_id=legacy_run,
        kind="novel_analysis.scalable.v2",
        owner_id="revision-legacy-owner",
    )
    composition = _Composition()
    recovery = _Recovery()
    coordinator = NovelAnalysisReplacementAutomaticRecovery(
        temp_db,
        composition,
        recovery_factory=lambda: recovery,
    )

    dispatched = await coordinator.recover_due(timestamp_ms=101)
    await asyncio.gather(*composition.tasks)

    assert dispatched == ("replacement-task",)
    assert recovery.resolved == ["replacement-task"]
    assert len(recovery.calls) == 1
    call = recovery.calls[0]
    assert call["recovery_source"] == "automatic"
    assert call["run_command_id"] == (
        "novel-analysis-auto-recovery:replacement-task:2"
    )
    events = await temp_db.fetch_all(
        "SELECT task_id, event_type, reason_scope FROM ai_agent_long_task_events "
        "WHERE event_type = 'automatic_recovery_dispatched'"
    )
    assert events == [{
        "task_id": "replacement-task",
        "event_type": "automatic_recovery_dispatched",
        "reason_scope": "system",
    }]


@pytest.mark.asyncio
async def test_automatic_recovery_does_not_double_dispatch_an_inflight_task(
    temp_db,
) -> None:
    run_id = await _run(
        temp_db,
        "replacement-run",
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    await _paused_task(
        temp_db,
        task_id="replacement-task",
        run_id=run_id,
        kind="novel_analysis.scalable.v2",
        owner_id="revision-replacement",
    )
    blocker = asyncio.Event()
    composition = _Composition()
    recovery = _Recovery(blocker=blocker)
    coordinator = NovelAnalysisReplacementAutomaticRecovery(
        temp_db,
        composition,
        recovery_factory=lambda: recovery,
    )

    assert await coordinator.recover_due(timestamp_ms=101) == (
        "replacement-task",
    )
    assert await coordinator.recover_due(timestamp_ms=101) == ()
    blocker.set()
    await asyncio.gather(*composition.tasks)

    assert len(recovery.calls) == 1


@pytest.mark.asyncio
async def test_replacement_reliability_baseline_keeps_existing_storage_contract(
    temp_db,
) -> None:
    run_id = await _run(
        temp_db,
        "replacement-run",
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES (?, ?, ?, ?, ?, 'completed', 1)",
        [
            "completed-task",
            "purrtypos.novel_analysis",
            "novel_analysis.scalable.v2",
            "revision-1",
            run_id,
        ],
    )
    baseline = NovelAnalysisReliabilityBaselineService(temp_db)

    assert await baseline.capture_due(timestamp_ms=21_600_001) is True
    assert await baseline.capture_due(timestamp_ms=21_600_002) is False
    row = await temp_db.fetch_one(
        "SELECT bucket_started_at_ms, window_limit, metrics_json "
        "FROM ai_novel_analysis_reliability_snapshots"
    )

    assert row["bucket_started_at_ms"] == 21_600_000
    assert row["window_limit"] == 100
    assert json.loads(row["metrics_json"])["sample"]["taskCount"] == 1


@pytest.mark.asyncio
async def test_automatic_continuation_records_automatic_recovery_source() -> None:
    repository = _LifecycleRepository()
    lifecycle = NovelAnalysisReplacementContinuationLifecycle(
        repository,
        task_id="replacement-task",
        recovery_source="automatic",
    )

    await lifecycle.before_submit()

    assert repository.calls == [("replacement-task", 0, "automatic")]


@pytest.mark.asyncio
async def test_legacy_failure_pause_is_retired_as_terminal_failure(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units, failed_units) VALUES "
        "('legacy-output-task', 'purrtypos.novel_analysis', "
        "'novel_analysis.scalable.v2', 'revision-1', 'root-run', 'paused', 2, 0, 0)"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, error_code, disposition, failure_json) "
        "VALUES "
        "('legacy-output-task', 'skill:create', 'skill:create', 0, 'blocked', "
        "'novel_analysis_skill_output_invalid', 'pause_recoverable', "
        "'{\"category\":\"transient_provider\"}'), "
        "('legacy-output-task', 'review:artifact', 'review:artifact', 1, 'pending', NULL, NULL, '{}')"
    )

    assert await retire_misclassified_failure_pauses(temp_db) == (
        "legacy-output-task",
    )
    assert await retire_misclassified_failure_pauses(temp_db) == ()
    task = await temp_db.fetch_one(
        "SELECT status, failed_units FROM ai_agent_long_tasks "
        "WHERE id = 'legacy-output-task'"
    )
    units = await temp_db.fetch_all(
        "SELECT unit_id, status, disposition, failure_json "
        "FROM ai_agent_long_task_units WHERE task_id = 'legacy-output-task' "
        "ORDER BY position"
    )

    assert task == {"status": "failed", "failed_units": 1}
    assert units[0]["status"] == "failed"
    assert units[0]["disposition"] == "fail_permanent"
    assert json.loads(units[0]["failure_json"])["category"] == "tool_execution"
    assert units[1]["status"] == "canceled"


@pytest.mark.asyncio
async def test_legacy_generic_child_failure_recovers_the_child_error_code(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, status, prompt, parent_run_id, error) VALUES "
        "('legacy-root', 'canceled', '', NULL, NULL), "
        "('legacy-child', 'failed', '', 'legacy-root', 'max_model_rounds')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units, failed_units) VALUES "
        "('legacy-child-task', 'purrtypos.novel_analysis', "
        "'novel_analysis.scalable.v2', 'revision-1', 'legacy-root', 'paused', 1, 0, 0)"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) VALUES "
        "('legacy-child-task', 'legacy-root', 'created')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, error_code, disposition, failure_json) "
        "VALUES ('legacy-child-task', 'skill:create', 'skill:create', 0, "
        "'blocked', 'RuntimeError', 'pause_recoverable', "
        "'{\"category\":\"transient_provider\",\"code\":\"RuntimeError\"}')"
    )

    assert await retire_misclassified_failure_pauses(temp_db) == (
        "legacy-child-task",
    )
    unit = await temp_db.fetch_one(
        "SELECT status, error_code, disposition, failure_json "
        "FROM ai_agent_long_task_units WHERE task_id = 'legacy-child-task'"
    )
    assert unit["status"] == "failed"
    assert unit["error_code"] == "max_model_rounds"
    assert unit["disposition"] == "fail_permanent"
    assert json.loads(unit["failure_json"])["code"] == "max_model_rounds"


@pytest.mark.asyncio
async def test_repeated_automatic_pause_preserves_and_increments_backoff(
    temp_db,
) -> None:
    run_id = await _run(
        temp_db,
        "replacement-run",
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, "
        "state_reason_code, state_reason_scope, total_units, metadata_json) "
        "VALUES ('replacement-task', 'purrtypos.novel_analysis', "
        "'novel_analysis.scalable.v2', 'revision-1', ?, 'paused', "
        "'provider_unavailable', 'system', 1, ?)",
        [
            run_id,
            json.dumps({
                "automaticRecoveryAttempt": 2,
                "automaticRecoveryWaitSpentMs": 3_000,
                "autoResumeNotBeforeMs": 100,
                "autoRecoveryReasonCode": "provider_unavailable",
            }),
        ],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status) "
        "VALUES ('replacement-task', 'unit-1', 'unit-1', 0, 'blocked')"
    )
    repository = SqliteLongTaskRepository(temp_db)

    resumed = await repository.resume(
        "replacement-task",
        recovery_source="automatic",
    )
    assert resumed.metadata["automaticRecoveryAttempt"] == 2
    assert resumed.metadata["automaticRecoveryWaitSpentMs"] == 3_000
    assert "autoResumeNotBeforeMs" not in resumed.metadata

    paused = await repository.pause(
        "replacement-task",
        reason_code="provider_unavailable",
    )
    assert paused.metadata["automaticRecoveryAttempt"] == 3
    assert paused.metadata["automaticRecoveryWaitSpentMs"] > 3_000
    assert paused.metadata["autoResumeNotBeforeMs"] > 0


async def _run(temp_db, run_id: str, identity) -> str:
    created = await create_run(
        temp_db,
        run_id=run_id,
        session_id=None,
        prompt="分析",
        mode="novel_analysis",
    )
    await SqliteAgentImplementationStore(temp_db).bind(created, identity)
    return created


async def _paused_task(
    temp_db,
    *,
    task_id: str,
    run_id: str,
    kind: str,
    owner_id: str,
) -> None:
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, "
        "state_reason_code, state_reason_scope, total_units, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, 'paused', 'provider_capacity_limited', "
        "'system', 1, ?)",
        [
            task_id,
            "purrtypos.novel_analysis",
            kind,
            owner_id,
            run_id,
            json.dumps({
                "autoResumeNotBeforeMs": 100,
                "automaticRecoveryAttempt": 2,
                "runtimeBinding": {"schemaVersion": 1},
            }),
        ],
    )
