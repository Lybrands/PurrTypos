"""
Skill orchestrator — port of electron/skillOrchestrator.js.

Plans tool calls (DAG + context resolution) and executes with repair.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from services.skill_planner import (
    build_dag_from_tool_calls,
    make_tool_call,
    parse_args_safe,
    stringify_args,
)
from utils.writing_chapters import get_writable_chapters_for_agent


# ---------------------------------------------------------------------------
# Context resolution helpers
# ---------------------------------------------------------------------------

def _safe_lower(s: Any) -> str:
    return str(s or "").strip().lower()


def _build_outline_index(tool_ctx: dict) -> list[dict]:
    raw = tool_ctx.get("availableOutlines") if isinstance(tool_ctx, dict) else []
    lst = raw if isinstance(raw, list) else []
    out = []
    for o in lst:
        oid = str(o.get("id", "") if isinstance(o, dict) else "")
        if oid:
            out.append({
                "id": oid,
                "title": str(o.get("title", "")),
                "type": str(o.get("type", "")),
            })
    return out


def resolve_outline_id_from_ctx(
    args: dict, tool_ctx: dict, latest_user_text: str = "",
) -> str | None:
    raw = args.get("outlineId")
    if raw is not None and str(raw).strip():
        return str(raw).strip()

    index = _build_outline_index(tool_ctx)
    if not index:
        return None

    oi = args.get("outlineIndex")
    if oi is not None and oi != "":
        try:
            n = int(float(oi))
            if 1 <= n <= len(index):
                return index[n - 1]["id"]
        except (ValueError, TypeError):
            pass

    explicit = str(args.get("outlineTitle") or args.get("targetOutlineTitle") or "").strip()
    target = explicit or str(latest_user_text or "").strip()
    if not target:
        return None
    lower = _safe_lower(target)
    for o in index:
        t = _safe_lower(o["title"])
        if lower in t or t in lower:
            return o["id"]
    return None


def resolve_chapter_id_from_ctx(args: dict, tool_ctx: dict) -> str | None:
    raw = args.get("chapterId")
    if raw is not None and str(raw).strip():
        return str(raw).strip()

    wc = get_writable_chapters_for_agent(tool_ctx.get("writingChapters") if isinstance(tool_ctx, dict) else [])
    if not wc:
        return None

    explicit_title = str(args.get("chapterTitle") or args.get("targetChapterTitle") or "").strip()
    if explicit_title:
        exact = next((c for c in wc if str(c.get("title") or "").strip() == explicit_title), None)
        if exact:
            return str(exact["id"])
        lower = _safe_lower(explicit_title)
        hit = next(
            (c for c in wc if (
                _safe_lower(c.get("title", "")) == lower
                or lower in _safe_lower(c.get("title", ""))
                or _safe_lower(c.get("title", "")) in lower
            )),
            None,
        )
        if hit:
            return str(hit["id"])

    idx = args.get("chapterIndex")
    if idx is not None and idx != "":
        try:
            n = int(float(idx))
            if 1 <= n <= len(wc):
                return str(wc[n - 1]["id"])
        except (ValueError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# Argument normalisation
# ---------------------------------------------------------------------------

def _normalize_args_by_ctx(
    name: str, args: dict, tool_ctx: dict, latest_user_text: str,
) -> dict:
    nxt = dict(args)
    if nxt.get("bookId") is None and tool_ctx.get("bookId") is not None:
        nxt["bookId"] = tool_ctx["bookId"]

    has_chapter_hint = (
        (nxt.get("chapterTitle") is not None and str(nxt.get("chapterTitle")).strip())
        or (nxt.get("targetChapterTitle") is not None and str(nxt.get("targetChapterTitle")).strip())
        or (nxt.get("chapterIndex") is not None and nxt.get("chapterIndex") != "")
    )
    if has_chapter_hint:
        resolved = resolve_chapter_id_from_ctx(nxt, tool_ctx)
        if resolved:
            nxt["chapterId"] = resolved

    if nxt.get("chapterId") is None and tool_ctx.get("chapterId") is not None:
        nxt["chapterId"] = tool_ctx["chapterId"]

    if name == "updateOutline" and not str(nxt.get("outlineId") or "").strip():
        resolved = resolve_outline_id_from_ctx(nxt, tool_ctx, latest_user_text)
        if resolved:
            nxt["outlineId"] = resolved

    if name == "queryOutline":
        has_list = (
            isinstance(nxt.get("outlineIds"), list)
            and len(nxt["outlineIds"]) > 0
            and any(str(x).strip() for x in nxt["outlineIds"])
        )
        if not has_list:
            if nxt.get("outlineId") is not None and str(nxt["outlineId"]).strip():
                nxt["outlineIds"] = [str(nxt["outlineId"]).strip()]
            else:
                resolved = resolve_outline_id_from_ctx(nxt, tool_ctx, latest_user_text)
                if resolved:
                    nxt["outlineIds"] = [resolved]

    return nxt


def _normalize_tool_calls(
    tool_calls: list[dict], tool_ctx: dict, latest_user_text: str,
) -> list[dict]:
    out: list[dict] = []
    for tc in (tool_calls or []):
        name = (tc.get("function") or {}).get("name", "")
        args = parse_args_safe((tc.get("function") or {}).get("arguments", "{}"))
        next_args = _normalize_args_by_ctx(name, args, tool_ctx, latest_user_text)
        out.append({
            **tc,
            "function": {
                **(tc.get("function") or {}),
                "arguments": stringify_args(next_args),
            },
        })
    return out


def _has_call(lst: list[dict], tool_name: str) -> bool:
    return any(
        (x.get("function") or {}).get("name") == tool_name
        for x in (lst or [])
    )


def _build_executable_calls_from_dag(dag: dict) -> list[dict]:
    by_id = {n["nodeId"]: n for n in (dag.get("nodes") or [])}
    out: list[dict] = []
    for node_id in dag.get("topoOrder", []):
        node = by_id.get(node_id)
        if not node:
            continue
        tc = node.get("toolCall")
        if tc and (tc.get("function") or {}).get("name"):
            out.append(tc)
    return out


def _inject_mvp_prerequisites(
    calls: list[dict], tool_ctx: dict, latest_user_text: str,
) -> list[dict]:
    out = list(calls or [])
    i = 0
    while i < len(out):
        c = out[i]
        if (c.get("function") or {}).get("name") != "updateOutline":
            i += 1
            continue
        args = parse_args_safe((c.get("function") or {}).get("arguments", "{}"))
        n_args = _normalize_args_by_ctx("updateOutline", args, tool_ctx, latest_user_text)
        c["function"]["arguments"] = stringify_args(n_args)
        if not _has_call(out, "listOutlines"):
            out.insert(i, make_tool_call("listOutlines", {"bookId": tool_ctx.get("bookId")}))
            i += 1
        if (
            not _has_call(out, "queryOutline")
            and n_args.get("outlineId") is not None
            and str(n_args["outlineId"]).strip()
        ):
            out.insert(i, make_tool_call("queryOutline", {
                "bookId": tool_ctx.get("bookId"),
                "outlineIds": [str(n_args["outlineId"]).strip()],
                "includeChapters": False,
                "includeText": True,
            }))
            i += 1
        i += 1
    return out


def _parse_json_safe(content: str) -> dict | None:
    try:
        return json.loads(content or "{}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_tool_calls(
    tool_calls: list[dict],
    skill_specs: dict[str, dict],
    tool_ctx: dict,
    latest_user_text: str = "",
) -> dict:
    """Normalise, build DAG, and produce an executable call list.

    Returns ``{ executableCalls, dag, telemetry }``.
    """
    normalized = _normalize_tool_calls(tool_calls or [], tool_ctx or {}, latest_user_text)
    dag = build_dag_from_tool_calls(normalized, skill_specs or {})
    executable = _build_executable_calls_from_dag(dag)
    executable = _inject_mvp_prerequisites(executable, tool_ctx or {}, latest_user_text)
    telemetry = {
        "nodes": len(dag["nodes"]),
        "edges": len(dag["edges"]),
        "insertedByDag": sum(1 for n in dag["nodes"] if n.get("inserted")),
        "insertedSkillNames": [n["skill"] for n in dag["nodes"] if n.get("inserted")],
        "finalCalls": [x["function"]["name"] for x in executable if x.get("function", {}).get("name")],
    }
    return {"executableCalls": executable, "dag": dag, "telemetry": telemetry}


async def execute_with_repair(
    planned_calls: list[dict],
    tool_ctx: dict,
    latest_user_text: str = "",
    max_repair_rounds: int = 1,
    run_tools_fn: Callable[..., Awaitable[list[dict]]] | None = None,
) -> dict:
    """Execute calls, auto-repairing ``updateOutline`` missing-outlineId failures.

    Returns ``{ toolCalls, toolResults, repairedRounds, repairEvents }``.
    """
    if run_tools_fn is None:
        raise ValueError("run_tools_fn is required")

    calls = list(planned_calls or [])
    all_results: list[dict] = []
    all_calls: list[dict] = []
    repaired_rounds = 0
    repair_events: list[dict] = []

    for rnd in range(max_repair_rounds + 1):
        if not calls:
            break
        this_round = calls
        all_calls.extend(this_round)
        res = await run_tools_fn(this_round)
        all_results.extend(res)
        retry_calls: list[dict] = []

        if rnd < max_repair_rounds:
            for call in this_round:
                if (call.get("function") or {}).get("name") != "updateOutline":
                    continue
                hit = next((r for r in res if r.get("tool_call_id") == call["id"]), None)
                payload = _parse_json_safe(hit.get("content", "") if hit else "")
                err = str((payload or {}).get("error", ""))
                if not payload or payload.get("success") is not False:
                    continue
                if not re.search(r"outlineid|缺少有效 outlineId", err, re.IGNORECASE):
                    continue
                args = parse_args_safe((call.get("function") or {}).get("arguments", "{}"))
                resolved = resolve_outline_id_from_ctx(args, tool_ctx, latest_user_text)
                if not resolved:
                    continue
                next_args = {**args, "outlineId": resolved, "bookId": args.get("bookId") or tool_ctx.get("bookId")}
                repair_events.append({
                    "tool": "updateOutline",
                    "reason": "缺少 outlineId，已从上下文自动解析并重试",
                    "resolvedOutlineId": resolved,
                })
                retry_calls.append(make_tool_call("updateOutline", next_args))

        if retry_calls:
            repaired_rounds += 1
            calls = retry_calls
            continue
        break

    return {
        "toolCalls": all_calls,
        "toolResults": all_results,
        "repairedRounds": repaired_rounds,
        "repairEvents": repair_events,
    }
