"""Evaluate a candidate planner output against deterministic Agent cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from services.agent_eval_cases import PLANNER_EVAL_CASES, PlannerEvalCase
from services.task_planner import normalize_model_task_plan
from services.tool_policy import TOOL_POLICIES


def evaluate_planner_case(
    case: PlannerEvalCase,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    plan = normalize_model_task_plan(
        candidate,
        available_tool_names=set(case.available_tools),
    )
    actual_valid = plan is not None
    checks: list[dict[str, Any]] = [{
        "name": "planValidity",
        "status": "pass" if actual_valid == case.expect_valid_plan else "fail",
        "detail": {"expected": case.expect_valid_plan, "actual": actual_valid},
    }]

    if plan is not None and case.expect_valid_plan:
        tool_steps = [
            (index, step, str(tool))
            for index, step in enumerate(plan["steps"])
            for tool in (step.get("suggestedTools") or [])
        ]
        actual_tools = {tool for _, _, tool in tool_steps}
        missing = sorted(case.required_tools - actual_tools)
        forbidden = sorted(case.forbidden_tools & actual_tools)
        checks.extend([
            {
                "name": "requiredTools",
                "status": "pass" if not missing else "fail",
                "detail": {"missing": missing},
            },
            {
                "name": "forbiddenTools",
                "status": "pass" if not forbidden else "fail",
                "detail": {"present": forbidden},
            },
        ])
        if case.destructive_tools_need_prior_read:
            ordered = _destructive_tools_have_prior_read(plan["steps"])
            checks.append({
                "name": "destructiveOrder",
                "status": "pass" if ordered else "fail",
                "detail": "destructive tools require an earlier read step",
            })

    verdict = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "caseId": case.case_id,
        "title": case.title,
        "verdict": verdict,
        "checks": checks,
        "normalizedPlan": plan,
    }


def summarize_planner_suite(results: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(1 for result in results if result.get("verdict") == "pass")
    return {"total": len(results), "passed": passed, "failed": len(results) - passed}


async def run_planner_eval_suite(
    generate_candidate: Callable[[PlannerEvalCase], Awaitable[dict[str, Any] | None]],
    *,
    cases: Iterable[PlannerEvalCase] = PLANNER_EVAL_CASES,
) -> dict[str, Any]:
    """Run the same cases against a real model adapter or a deterministic fake.

    The harness owns scoring; the caller owns how a candidate plan is generated.
    This keeps CI deterministic while still allowing controlled model experiments.
    """
    results: list[dict[str, Any]] = []
    for case in cases:
        candidate = await generate_candidate(case)
        results.append(evaluate_planner_case(case, candidate))
    return {"summary": summarize_planner_suite(results), "results": results}


def _destructive_tools_have_prior_read(steps: list[dict[str, Any]]) -> bool:
    has_prior_read = False
    for step in steps:
        tools = [str(tool) for tool in (step.get("suggestedTools") or [])]
        if any(
            TOOL_POLICIES.get(tool) and TOOL_POLICIES[tool].risk_level == "destructive"
            for tool in tools
        ) and not has_prior_read:
            return False
        if step.get("type") == "read" and tools:
            has_prior_read = True
    return True
