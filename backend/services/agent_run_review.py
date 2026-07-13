"""Human quality-review rubric for completed Agent runs.

This module validates and stores a review; it deliberately does not try to
automatically decide whether a model answer is good.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


RUBRIC = (
    {
        "key": "grounding",
        "label": "上下文与事实一致性",
        "description": "是否正确使用提供的上下文，并避免无依据的编造。",
    },
    {
        "key": "task_completion",
        "label": "任务完成度",
        "description": "是否覆盖用户目标、约束和需要交付的内容。",
    },
    {
        "key": "actionability",
        "label": "可执行性",
        "description": "建议、步骤或产物是否具体到用户能够继续行动。",
    },
    {
        "key": "calibration",
        "label": "边界与安全校准",
        "description": "不确定处是否如实说明；高风险行为是否遵守确认与权限边界。",
    },
    {
        "key": "writing_fit",
        "label": "表达与场景契合度",
        "description": "表达是否清晰，并符合当前创作/协作场景的风格需要。",
    },
)
RUBRIC_KEYS = tuple(item["key"] for item in RUBRIC)
MIN_SCORE = 0
MAX_SCORE = 4


def get_rubric() -> dict[str, Any]:
    return {
        "dimensions": list(RUBRIC),
        "scoreRange": {"min": MIN_SCORE, "max": MAX_SCORE},
        "maxTotal": len(RUBRIC_KEYS) * MAX_SCORE,
    }


def validate_scores(scores: dict[str, int]) -> dict[str, int]:
    expected, received = set(RUBRIC_KEYS), set(scores)
    if received != expected:
        missing, unknown = sorted(expected - received), sorted(received - expected)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ValueError("Review scores must match the rubric exactly (" + "; ".join(details) + ").")

    normalized: dict[str, int] = {}
    for key in RUBRIC_KEYS:
        value = scores[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Review score '{key}' must be an integer.")
        if not MIN_SCORE <= value <= MAX_SCORE:
            raise ValueError(f"Review score '{key}' must be between {MIN_SCORE} and {MAX_SCORE}.")
        normalized[key] = value
    return normalized


def summarize_scores(scores: dict[str, int]) -> dict[str, Any]:
    total = sum(scores.values())
    maximum = len(RUBRIC_KEYS) * MAX_SCORE
    return {
        "total": total,
        "maximum": maximum,
        "normalized": round(total / maximum, 4) if maximum else 0,
    }


async def create_review(
    db: "DatabaseConnection",
    run_id: str,
    scores: dict[str, int],
    *,
    notes: str | None = None,
    evaluator: str = "human",
) -> dict[str, Any]:
    normalized_scores = validate_scores(scores)
    evaluator = (evaluator or "human").strip() or "human"
    review_id = await db.execute_and_get_id(
        "INSERT INTO ai_agent_run_reviews (run_id, evaluator, scores_json, notes) VALUES (?, ?, ?, ?)",
        [run_id, evaluator, json.dumps(normalized_scores, ensure_ascii=False), notes],
    )
    if review_id is None:
        raise RuntimeError("Failed to create Agent Run review.")
    review = await get_review(db, int(review_id))
    if review is None:
        raise RuntimeError("Created Agent Run review could not be read back.")
    return review


async def get_review(
    db: "DatabaseConnection",
    review_id: int,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, run_id, evaluator, scores_json, notes, create_time FROM ai_agent_run_reviews WHERE id = ?",
        [review_id],
    )
    return _serialize_review(row) if row else None


async def get_run_reviews(
    db: "DatabaseConnection",
    run_id: str,
) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, run_id, evaluator, scores_json, notes, create_time "
        "FROM ai_agent_run_reviews WHERE run_id = ? ORDER BY id ASC",
        [run_id],
    )
    return [_serialize_review(row) for row in rows]


def _serialize_review(row: dict[str, Any]) -> dict[str, Any]:
    scores = json.loads(row["scores_json"])
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "evaluator": row["evaluator"],
        "scores": scores,
        "summary": summarize_scores(scores),
        "notes": row["notes"],
        "createTime": row["create_time"],
    }
