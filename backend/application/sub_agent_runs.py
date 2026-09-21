"""Generic child-run (sub-agent) projections shared by every agent kind.

小说分析页曾以页面专属投影（agents/novel_analysis/run_projection.py）拼出
子 Run 列表；这里把「根 Run 的子 Run 查询 + 委派视图」下沉为通用能力，
供 Run 快照等读路径复用——写作 Agent 的委派展示由此获得与分析一致的
数据基础。
"""

from __future__ import annotations

from typing import Any

DELEGATION_STATUSES = ("queued", "claimed", "running", "done", "failed", "canceled")


def delegation_status(run_status: object) -> str:
    """Map an ai_agent_runs status onto the delegation status vocabulary."""

    value = str(run_status or "").strip().lower()
    if value in {"pending", "queued"}:
        return "queued"
    if value == "claimed":
        return "claimed"
    if value in {"running", "waiting"}:
        return "running"
    if value == "done":
        return "done"
    if value == "canceled":
        return "canceled"
    return "failed"


async def related_runs_for_root(
    db, run_tree_repository, root_run_id: str
) -> list[dict[str, Any]]:
    """Child runs directly spawned by a root run, enriched from the run tree."""

    from purra.errors import ContractViolationError

    rows = await db.fetch_all(
        "SELECT child.id, child.status, child.agent_id, child.create_time, "
        "(SELECT event.occurred_at FROM ai_agent_run_events event "
        "WHERE event.run_id = child.id AND event.event_type = 'run.lifecycle' "
        "ORDER BY event.id LIMIT 1) AS precise_start_time "
        "FROM ai_agent_runs child "
        "WHERE child.root_run_id = ? AND child.parent_run_id = ? "
        "ORDER BY child.rowid",
        [root_run_id, root_run_id],
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "runId": str(row["id"]),
            "status": str(row["status"]),
            "role": "child",
            "createTime": row.get("precise_start_time") or row.get("create_time"),
        }
        try:
            tree_run = await run_tree_repository.get_run(str(row["id"]))
            agent = await run_tree_repository.get_agent(tree_run.agent_id)
        except ContractViolationError:
            # Run tree records are best-effort enrichment; raw child rows are
            # still presentable without them.
            pass
        else:
            item.update({
                "agentId": agent.agent_id,
                "agentName": agent.name,
                "agentTitle": agent.title,
                "objective": tree_run.objective,
                "previousRunId": tree_run.previous_run_id,
                "unitId": tree_run.input_payload.get("unitId"),
                "attempt": tree_run.input_payload.get("attempt"),
            })
        result.append(item)
    return result


def delegation_projection(related_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the snapshot `delegations` view from related child runs."""

    items: list[dict[str, Any]] = []
    for run in related_runs:
        if run.get("role") != "child":
            continue
        item: dict[str, Any] = {
            "delegationId": f"run:{run['runId']}",
            "runId": str(run["runId"]),
            "agentId": run.get("agentId") or None,
            "previousRunId": run.get("previousRunId") or None,
            "agentName": str(
                run.get("agentName") or run.get("agentTitle") or "子 Agent"
            ),
            "agentTitle": run.get("agentTitle") or None,
            "objective": str(run.get("objective") or ""),
            "unitId": run.get("unitId"),
            "attempt": run.get("attempt"),
            "status": delegation_status(run.get("status")),
            "required": True,
            "priority": 0,
        }
        if run.get("createTime"):
            item["startedAt"] = run["createTime"]
        items.append(item)
    counts = {status: 0 for status in DELEGATION_STATUSES}
    for item in items:
        counts[str(item["status"])] += 1
    return {
        "items": items,
        "aggregate": {
            "state": "ready",
            "counts": counts,
            "requiredFailures": [],
            "results": [],
        },
    }


__all__ = [
    "DELEGATION_STATUSES",
    "delegation_projection",
    "delegation_status",
    "related_runs_for_root",
]
