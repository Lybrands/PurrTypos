"""Deterministic release gates and staged-rollout comparisons for the Agent."""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Any, Iterable

from services.agent_run_evaluation import evaluate_agent_run
from services.agent_run_performance import evaluate_agent_run_performance
from services.agent_run_review import get_run_reviews
from services.agent_run_store import get_run, get_run_events
from services.agent_runtime_regression import run_runtime_regression_suite
from services.agent_security_redteam import run_agent_security_redteam_suite

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


RELEASE_POLICY = {
    "minimumPilotRuns": 5,
    "minimumReviewedRuns": 5,
    "minimumHumanQuality": 0.75,
    "maximumFailureRateIncrease": 0.02,
    "maximumLatencyIncrease": 0.25,
    "maximumQualityDrop": 0.05,
}


async def build_release_snapshot(
    db: "DatabaseConnection",
    run_id: str,
) -> dict[str, Any] | None:
    run = await get_run(db, run_id)
    if run is None:
        return None
    events = await get_run_events(db, run_id)
    operational = evaluate_agent_run(run, events)
    performance = evaluate_agent_run_performance(events)
    reviews = await get_run_reviews(db, run_id)
    review_scores = [
        float((review.get("summary") or {}).get("normalized") or 0)
        for review in reviews
    ]
    return {
        "runId": run_id,
        "release": operational["release"],
        "runStatus": operational["runStatus"],
        "operationalVerdict": operational["verdict"],
        "toolGovernance": _check_status(operational, "toolGovernance"),
        "toolReliability": _check_status(operational, "toolReliability"),
        "recordedTotalMs": int(performance["metrics"]["recordedTotalMs"]),
        "humanQuality": (
            round(sum(review_scores) / len(review_scores), 4)
            if review_scores
            else None
        ),
        "reviewCount": len(review_scores),
    }


async def load_release_snapshots(
    db: "DatabaseConnection",
    run_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    snapshots: list[dict[str, Any]] = []
    missing: list[str] = []
    for run_id in _unique_ids(run_ids):
        snapshot = await build_release_snapshot(db, run_id)
        if snapshot is None:
            missing.append(run_id)
        else:
            snapshots.append(snapshot)
    return snapshots, missing


def evaluate_release_gate(
    runtime_suite: dict[str, Any],
    pilot_snapshots: list[dict[str, Any]],
    *,
    missing_run_ids: Iterable[str] = (),
    security_suite: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing = list(missing_run_ids)
    regression_failures = int((runtime_suite.get("summary") or {}).get("failed") or 0)
    security_report = security_suite or run_agent_security_redteam_suite()
    security_failures = int((security_report.get("summary") or {}).get("failed") or 0)
    operational_failures = sum(
        1 for snapshot in pilot_snapshots if snapshot.get("operationalVerdict") == "fail"
    )
    governance_failures = sum(
        1 for snapshot in pilot_snapshots if snapshot.get("toolGovernance") == "fail"
    )
    reviewed = [
        float(snapshot["humanQuality"])
        for snapshot in pilot_snapshots
        if snapshot.get("humanQuality") is not None
    ]
    mean_quality = round(sum(reviewed) / len(reviewed), 4) if reviewed else None

    checks = [
        _gate_check("runtimeRegressions", regression_failures == 0, {
            "failed": regression_failures,
        }),
        _gate_check("securityRedTeam", security_failures == 0, {
            "failed": security_failures,
        }),
        _gate_check("knownRuns", not missing, {"missingRunIds": missing}),
        _gate_check("operationalSafety", operational_failures == 0, {
            "failedRuns": operational_failures,
        }),
        _gate_check("toolGovernance", governance_failures == 0, {
            "failedRuns": governance_failures,
        }),
        _sample_check(
            "pilotSample",
            len(pilot_snapshots),
            RELEASE_POLICY["minimumPilotRuns"],
        ),
        _sample_check(
            "humanReviewSample",
            len(reviewed),
            RELEASE_POLICY["minimumReviewedRuns"],
        ),
        {
            "name": "humanQuality",
            "status": (
                "pass"
                if len(reviewed) >= RELEASE_POLICY["minimumReviewedRuns"]
                and mean_quality is not None
                and mean_quality >= RELEASE_POLICY["minimumHumanQuality"]
                else "fail"
                if len(reviewed) >= RELEASE_POLICY["minimumReviewedRuns"]
                else "hold"
            ),
            "detail": {
                "actual": mean_quality,
                "minimum": RELEASE_POLICY["minimumHumanQuality"],
            },
        },
    ]
    hard_failure = any(check["status"] == "fail" for check in checks)
    if hard_failure:
        decision = "reject"
    elif any(check["status"] == "hold" for check in checks):
        decision = "hold"
    else:
        decision = "approve_canary"
    return {
        "decision": decision,
        "policy": dict(RELEASE_POLICY),
        "metrics": {
            "pilotRuns": len(pilot_snapshots),
            "reviewedRuns": len(reviewed),
            "meanHumanQuality": mean_quality,
            "operationalFailures": operational_failures,
            "governanceFailures": governance_failures,
            "securityRedTeamFailures": security_failures,
        },
        "checks": checks,
    }


def evaluate_rollout(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    missing_run_ids: Iterable[str] = (),
) -> dict[str, Any]:
    missing = list(missing_run_ids)
    base = _aggregate(baseline)
    cand = _aggregate(candidate)
    enough_samples = (
        base["runs"] >= RELEASE_POLICY["minimumPilotRuns"]
        and cand["runs"] >= RELEASE_POLICY["minimumPilotRuns"]
    )
    failure_increase = round(cand["failureRate"] - base["failureRate"], 4)
    latency_increase = _relative_increase(base["p95RecordedTotalMs"], cand["p95RecordedTotalMs"])
    quality_drop = _quality_drop(base["meanHumanQuality"], cand["meanHumanQuality"])

    rollback_reasons: list[str] = []
    hold_reasons: list[str] = []
    if missing:
        rollback_reasons.append("unknown_run_ids")
    if cand["governanceFailures"]:
        rollback_reasons.append("tool_governance_regression")
    if enough_samples and failure_increase > RELEASE_POLICY["maximumFailureRateIncrease"]:
        rollback_reasons.append("failure_rate_regression")
    if quality_drop is not None and quality_drop > RELEASE_POLICY["maximumQualityDrop"]:
        rollback_reasons.append("human_quality_regression")
    if not enough_samples:
        hold_reasons.append("insufficient_sample")
    if base["reviewedRuns"] < RELEASE_POLICY["minimumReviewedRuns"] or cand["reviewedRuns"] < RELEASE_POLICY["minimumReviewedRuns"]:
        hold_reasons.append("insufficient_human_reviews")
    if latency_increase is not None and latency_increase > RELEASE_POLICY["maximumLatencyIncrease"]:
        hold_reasons.append("latency_regression")

    decision = (
        "rollback" if rollback_reasons
        else "hold" if hold_reasons
        else "promote"
    )
    return {
        "decision": decision,
        "policy": dict(RELEASE_POLICY),
        "baseline": base,
        "candidate": cand,
        "deltas": {
            "failureRate": failure_increase,
            "p95LatencyRatio": latency_increase,
            "humanQualityDrop": quality_drop,
        },
        "reasons": rollback_reasons or hold_reasons,
        "missingRunIds": missing,
    }


async def evaluate_release_runs(
    db: "DatabaseConnection",
    run_ids: Iterable[str],
) -> dict[str, Any]:
    snapshots, missing = await load_release_snapshots(db, run_ids)
    return evaluate_release_gate(
        run_runtime_regression_suite(),
        snapshots,
        missing_run_ids=missing,
    )


async def evaluate_rollout_runs(
    db: "DatabaseConnection",
    baseline_run_ids: Iterable[str],
    candidate_run_ids: Iterable[str],
) -> dict[str, Any]:
    baseline, missing_baseline = await load_release_snapshots(db, baseline_run_ids)
    candidate, missing_candidate = await load_release_snapshots(db, candidate_run_ids)
    return evaluate_rollout(
        baseline,
        candidate,
        missing_run_ids=[*missing_baseline, *missing_candidate],
    )


def _aggregate(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    failures = sum(1 for item in snapshots if item.get("operationalVerdict") == "fail")
    governance = sum(1 for item in snapshots if item.get("toolGovernance") == "fail")
    reviewed = [
        float(item["humanQuality"])
        for item in snapshots
        if item.get("humanQuality") is not None
    ]
    latencies = sorted(max(0, int(item.get("recordedTotalMs") or 0)) for item in snapshots)
    return {
        "runs": len(snapshots),
        "failures": failures,
        "failureRate": round(failures / len(snapshots), 4) if snapshots else 0.0,
        "governanceFailures": governance,
        "reviewedRuns": len(reviewed),
        "meanHumanQuality": round(sum(reviewed) / len(reviewed), 4) if reviewed else None,
        "p95RecordedTotalMs": _percentile95(latencies),
    }


def _percentile95(values: list[int]) -> int:
    if not values:
        return 0
    return values[max(0, ceil(len(values) * 0.95) - 1)]


def _relative_increase(baseline: int, candidate: int) -> float | None:
    if baseline <= 0:
        return None
    return round((candidate - baseline) / baseline, 4)


def _quality_drop(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    return round(baseline - candidate, 4)


def _check_status(report: dict[str, Any], name: str) -> str:
    for check in report.get("checks") or []:
        if check.get("name") == name:
            return str(check.get("status") or "missing")
    return "missing"


def _gate_check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


def _sample_check(name: str, actual: int, minimum: int) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if actual >= minimum else "hold",
        "detail": {"actual": actual, "minimum": minimum},
    }


def _unique_ids(run_ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(run_id).strip() for run_id in run_ids if str(run_id).strip()))
