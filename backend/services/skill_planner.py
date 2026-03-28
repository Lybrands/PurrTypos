"""
Skill DAG planner (MVP) — port of electron/skillPlanner.js.

Builds a linear DAG from tool_calls + SkillSpec, auto-injecting
static dependencies (``requires``) and reusing earlier same-name
nodes when present.
"""

from __future__ import annotations

import json
from typing import Any

from utils.id_utils import short_id8


def parse_args_safe(args_text: Any) -> dict:
    if not isinstance(args_text, str):
        return {}
    try:
        parsed = json.loads(args_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def stringify_args(args: Any) -> str:
    try:
        return json.dumps(args or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def _clone_tool_call(tc: dict | None) -> dict:
    tc = tc or {}
    fn = tc.get("function") or {}
    return {
        "id": str(tc.get("id") or ""),
        "type": tc.get("type", "function"),
        "function": {
            "name": str(fn.get("name") or ""),
            "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str) else "{}",
        },
    }


def make_tool_call(name: str, args: dict | None = None) -> dict:
    return {
        "id": short_id8(),
        "type": "function",
        "function": {
            "name": name,
            "arguments": stringify_args(args),
        },
    }


def build_dag_from_tool_calls(
    tool_calls: list[dict],
    skill_specs: dict[str, dict],
) -> dict:
    """Build a linear DAG with auto-injected prerequisite nodes.

    Returns ``{ nodes, edges, topoOrder, warnings }``.
    """
    tool_calls = tool_calls if isinstance(tool_calls, list) else []
    skill_specs = skill_specs or {}

    nodes: list[dict] = []
    edges: list[dict] = []
    topo_order: list[str] = []
    warnings: list[str] = []
    seen_prereq: set[str] = set()

    prepared: list[dict] = []
    for original in tool_calls:
        cur = _clone_tool_call(original)
        if not cur["id"]:
            cur["id"] = short_id8()
            warnings.append(f"tool_call 缺少 id，已自动生成：{cur['id']}")
        prepared.append(cur)

    for input_idx, cur in enumerate(prepared):
        name = cur["function"]["name"]
        if not name:
            continue
        spec = skill_specs.get(name, {})
        requires = spec.get("requires", []) if isinstance(spec.get("requires"), list) else []

        for dep in requires:
            if not dep or dep == name:
                continue
            key = f"{dep}=>{name}"
            if key in seen_prereq:
                continue
            seen_prereq.add(key)

            from_input_id = ""
            for j in range(input_idx - 1, -1, -1):
                if str(prepared[j]["function"].get("name", "")) == dep:
                    from_input_id = prepared[j]["id"]
                    break

            if from_input_id:
                edges.append({"from": from_input_id, "to": cur["id"], "type": "depends_on"})
                continue

            dep_call = make_tool_call(dep, {})
            nodes.append({
                "nodeId": dep_call["id"],
                "skill": dep,
                "toolCall": dep_call,
                "inserted": True,
            })
            edges.append({"from": dep_call["id"], "to": cur["id"], "type": "depends_on"})
            topo_order.append(dep_call["id"])

        nodes.append({
            "nodeId": cur["id"],
            "skill": name,
            "toolCall": cur,
            "inserted": False,
        })
        topo_order.append(cur["id"])

    return {
        "nodes": nodes,
        "edges": edges,
        "topoOrder": topo_order,
        "warnings": warnings,
    }
