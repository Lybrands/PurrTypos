from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


def valid_scores(**overrides: int) -> dict[str, int]:
    scores = {
        "grounding": 4,
        "task_completion": 3,
        "actionability": 3,
        "calibration": 4,
        "writing_fit": 2,
    }
    scores.update(overrides)
    return scores


async def test_review_endpoint_persists_human_score_for_a_specific_run(
    temp_db: DatabaseConnection,
):
    from routers.ai import (
        create_agent_run_review,
        get_agent_run_diagnostics,
        get_agent_run_review_rubric,
        list_agent_run_reviews,
    )
    from schemas.ai import CreateAgentRunReviewRequest
    from services.agent_run_store import create_run

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    created = await create_agent_run_review(
        run_id,
        CreateAgentRunReviewRequest(
            scores=valid_scores(), notes="Answer is useful, but the style needs work.",
        ),
    )

    assert created["success"] is True
    assert created["data"]["runId"] == run_id
    assert created["data"]["summary"] == {
        "total": 16,
        "maximum": 20,
        "normalized": 0.8,
    }

    listed = await list_agent_run_reviews(run_id)
    assert listed["success"] is True
    assert len(listed["data"]) == 1
    assert listed["data"][0]["notes"] == "Answer is useful, but the style needs work."

    rubric = await get_agent_run_review_rubric()
    assert rubric["success"] is True
    assert rubric["data"]["maxTotal"] == 20
    assert len(rubric["data"]["dimensions"]) == 5

    diagnostics = await get_agent_run_diagnostics(run_id)
    assert diagnostics["success"] is True
    assert diagnostics["data"]["humanReviews"][0]["id"] == created["data"]["id"]


async def test_review_endpoint_rejects_incomplete_or_out_of_range_scores(
    temp_db: DatabaseConnection,
):
    from routers.ai import create_agent_run_review
    from schemas.ai import CreateAgentRunReviewRequest
    from services.agent_run_store import create_run

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")

    incomplete = await create_agent_run_review(
        run_id,
        CreateAgentRunReviewRequest(scores={"grounding": 4}),
    )
    assert incomplete["success"] is False
    assert "must match the rubric" in incomplete["error"]

    out_of_range = await create_agent_run_review(
        run_id,
        CreateAgentRunReviewRequest(scores=valid_scores(grounding=5)),
    )
    assert out_of_range["success"] is False
    assert "between 0 and 4" in out_of_range["error"]


async def test_review_endpoint_rejects_an_unknown_run(temp_db: DatabaseConnection):
    from routers.ai import create_agent_run_review, list_agent_run_reviews
    from schemas.ai import CreateAgentRunReviewRequest

    created = await create_agent_run_review(
        "run_missing",
        CreateAgentRunReviewRequest(scores=valid_scores()),
    )
    assert created == {"success": False, "error": "Agent Run 不存在"}

    listed = await list_agent_run_reviews("run_missing")
    assert listed == {"success": False, "error": "Agent Run 不存在"}
