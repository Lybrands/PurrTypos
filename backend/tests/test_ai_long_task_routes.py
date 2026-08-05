from __future__ import annotations

import pytest

from types import SimpleNamespace

from agent_core.long_tasks import LongTaskStatus
from routers.ai import cancel_long_task, pause_long_task


@pytest.mark.asyncio
async def test_cancel_long_task_uses_composition_cancellation_boundary(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        work_item_id="work-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.CANCELED,
        revision=2,
        total_units=2,
        completed_units=0,
        failed_units=0,
        max_parallelism=1,
        metadata={},
    )
    canceled = []

    class _Composition:
        async def cancel_screenplay_long_task(self, task_id):
            canceled.append(task_id)
            return task

    monkeypatch.setattr(
        "application.agent_composition.get_agent_composition",
        lambda: _Composition(),
    )

    response = await cancel_long_task(task.id)

    assert response["success"] is True
    assert canceled == [task.id]


@pytest.mark.asyncio
async def test_pause_long_task_uses_composition_checkpoint_boundary(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        work_item_id="work-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        created_by_run_id="run-parent",
        status=LongTaskStatus.PAUSED,
        revision=1,
        total_units=2,
        completed_units=0,
        failed_units=1,
        max_parallelism=1,
        metadata={},
    )

    paused = []

    class _Composition:
        async def pause_screenplay_long_task(self, task_id):
            paused.append(task_id)
            return task

    monkeypatch.setattr(
        "application.agent_composition.get_agent_composition",
        lambda: _Composition(),
    )
    response = await pause_long_task(task.id)

    assert response["success"] is True
    assert paused == [task.id]
