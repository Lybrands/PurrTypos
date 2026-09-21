"""Application orchestration for scoped Agent Run stability trends."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from purra.observability import (
    DEFAULT_STABILITY_TREND_POLICY,
    StabilityRegressionGatePolicy,
    StabilityTrendPolicy,
    evaluate_agent_run_stability,
    evaluate_agent_run_stability_trend,
    evaluate_stability_regression_gate,
)


StabilityScope = Literal[
    "auto",
    "session",
    "book",
    "screenplay_project",
    "global",
]


class StabilityEvidenceGateway(Protocol):
    async def get_reference_scope(
        self,
        run_id: str,
    ) -> dict[str, Any] | None: ...

    async def list_recent(
        self,
        *,
        session_id: int | None = None,
        book_id: str | None = None,
        screenplay_project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def get_novel_analysis_reliability(
        self,
        *,
        session_id: int | None = None,
        book_id: str | None = None,
        screenplay_project_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]: ...

    async def list_novel_analysis_reliability_baselines(
        self,
        *,
        limit: int = 24,
    ) -> list[dict[str, Any]]: ...


async def get_scoped_agent_stability_trend(
    gateway: StabilityEvidenceGateway,
    run_id: str,
    *,
    scope: StabilityScope = "auto",
    limit: int = 20,
    policy: StabilityTrendPolicy | None = None,
    regression_policy: StabilityRegressionGatePolicy | None = None,
) -> dict[str, Any] | None:
    """Resolve the reference Run's domain scope and evaluate recent Runs."""

    reference = await gateway.get_reference_scope(str(run_id or "").strip())
    if reference is None:
        return None
    if scope not in {
        "auto",
        "session",
        "book",
        "screenplay_project",
        "global",
    }:
        raise ValueError("unsupported stability trend scope")

    session_id = reference.get("sessionId")
    resolved_scope = _resolve_scope(
        scope,
        session_id=session_id,
        reference=reference,
    )
    query_scope: dict[str, Any] = {}
    scope_id: str | int | None = None
    if resolved_scope == "session":
        scope_id = int(session_id)
        query_scope["session_id"] = scope_id
    elif resolved_scope == "book":
        scope_id = str(reference.get("bookId") or "").strip()
        query_scope["book_id"] = scope_id
    elif resolved_scope == "screenplay_project":
        scope_id = str(
            reference.get("screenplayProjectId") or ""
        ).strip()
        query_scope["screenplay_project_id"] = scope_id

    evidence = await gateway.list_recent(
        limit=limit,
        **query_scope,
    )
    samples = [
        {
            "runId": item["runId"],
            "runStatus": item["runStatus"],
            "createTime": item["createTime"],
            "stability": evaluate_agent_run_stability(item["events"]),
        }
        for item in evidence
    ]
    selected_policy = policy or DEFAULT_STABILITY_TREND_POLICY
    report = evaluate_agent_run_stability_trend(
        samples,
        policy=selected_policy,
    )
    comparison_window_size = len(samples) // 2
    candidate_samples = samples[:comparison_window_size]
    baseline_samples = samples[
        comparison_window_size:comparison_window_size * 2
    ]
    candidate = evaluate_agent_run_stability_trend(
        candidate_samples,
        policy=selected_policy,
    )
    baseline = evaluate_agent_run_stability_trend(
        baseline_samples,
        policy=selected_policy,
    )
    report["regressionGate"] = evaluate_stability_regression_gate(
        candidate,
        baseline,
        policy=regression_policy,
    )
    report["comparisonWindowSize"] = comparison_window_size
    report["scope"] = {"type": resolved_scope, "id": scope_id}
    report["windowLimit"] = int(limit)
    reliability = await gateway.get_novel_analysis_reliability(
        limit=limit,
        **query_scope,
    )
    reliability["baselines"] = {
        "scope": "global",
        "availableForScope": resolved_scope == "global",
        "snapshots": (
            await gateway.list_novel_analysis_reliability_baselines()
            if resolved_scope == "global"
            else []
        ),
    }
    report["novelAnalysisReliability"] = reliability
    return report


def _resolve_scope(
    requested: StabilityScope,
    *,
    session_id: Any,
    reference: dict[str, Any],
) -> str:
    project_id = str(reference.get("screenplayProjectId") or "").strip()
    book_id = str(reference.get("bookId") or "").strip()
    if requested == "auto":
        if project_id:
            return "screenplay_project"
        if book_id:
            return "book"
        if session_id is not None:
            return "session"
        return "global"
    if requested == "screenplay_project" and not project_id:
        raise ValueError("reference Run has no screenplay project scope")
    if requested == "book" and not book_id:
        raise ValueError("reference Run has no book scope")
    if requested == "session" and session_id is None:
        raise ValueError("reference Run has no session scope")
    return requested


__all__ = [
    "StabilityEvidenceGateway",
    "StabilityScope",
    "get_scoped_agent_stability_trend",
]
