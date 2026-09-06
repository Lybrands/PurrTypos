"""Rebuild screenplay tool-call labels for historical read models.

The canonical journal is immutable.  This query uses only durable execution
scope and the original structured call arguments to repair presentation from
older projector versions without changing tool IO or exposing those arguments
through the public conversation stream.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from domains.screenplay_agent.tools.catalog import (
    screenplay_operation_display_params,
)


ToolCallKey = tuple[str, str]


_ARGUMENT_SENSITIVE_READ_TOOLS = frozenset({
    "getScreenplaySceneContext",
    "readScreenplayTaskDependencies",
    "readScreenplayDeliverable",
    "searchScreenplayDeliverables",
    "inspectSourceStructure",
    "readSourceChapters",
    "searchSourceText",
    "listSourceCharacters",
    "readSourceCharacters",
    "listSourceWorldEntities",
    "readSourceWorldEntities",
    "querySourceStoryFacts",
    "readSourceOutline",
})


class ScreenplayToolPresentationReadModel:
    """Resolve current semantic labels for persisted screenplay tool calls."""

    def __init__(self, db) -> None:
        self._db = db

    async def project(
        self,
        calls: Mapping[ToolCallKey, str],
    ) -> dict[ToolCallKey, dict[str, Any]]:
        targets = {
            (str(run_id), str(call_id)): str(tool_name)
            for (run_id, call_id), tool_name in calls.items()
            if run_id
            and call_id
            and str(tool_name) in _ARGUMENT_SENSITIVE_READ_TOOLS
        }
        if not targets:
            return {}
        domains = await self._run_domains(
            tuple(dict.fromkeys(run_id for run_id, _call_id in targets))
        )
        scoped_targets = {
            key: name for key, name in targets.items() if key[0] in domains
        }
        if not scoped_targets:
            return {}
        arguments = await self._call_arguments(scoped_targets)
        return {
            key: screenplay_operation_display_params(
                domains[key[0]],
                call_arguments,
                scoped_targets[key],
            )
            for key, call_arguments in arguments.items()
        }

    async def _run_domains(
        self,
        run_ids: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        marks = ",".join("?" for _ in run_ids)
        rows = await self._db.fetch_all(
            "SELECT r.id, u.task_id, "
            "json_extract(r.binding_attributes_json, "
            "'$.candidateCompletionProjection.scope') AS scope_json, "
            "json_extract(u.metadata_json, '$.input.sceneIds') AS scene_ids_json "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN ai_agent_long_task_units AS u ON u.run_id = r.id "
            "WHERE r.binding_namespace = 'screenplay.agent.task' "
            f"AND r.id IN ({marks})",
            list(run_ids),
        )
        domains: dict[str, dict[str, Any]] = {}
        run_tasks: dict[str, str] = {}
        for row in rows:
            run_id = str(row.get("id") or "")
            scope = _json_object(row.get("scope_json"))
            if not run_id or not scope:
                continue
            scene_ids = _json_array(row.get("scene_ids_json"))
            if scene_ids:
                scope["sceneIds"] = scene_ids
            domains[run_id] = scope
            task_id = str(row.get("task_id") or "").strip()
            if task_id:
                run_tasks[run_id] = task_id
        task_ids = tuple(dict.fromkeys(run_tasks.values()))
        if not task_ids:
            return domains
        task_marks = ",".join("?" for _ in task_ids)
        scene_rows = await self._db.fetch_all(
            "SELECT task_id, "
            "json_extract(metadata_json, '$.input.episodeNumber') "
            "AS episode_number, "
            "json_extract(metadata_json, '$.input.sceneIds') AS scene_ids_json "
            "FROM ai_agent_long_task_units "
            f"WHERE task_id IN ({task_marks}) "
            "AND json_type(metadata_json, '$.input.sceneIds') = 'array'",
            list(task_ids),
        )
        task_scenes: dict[str, dict[str, list[Any]]] = {}
        for row in scene_rows:
            number = row.get("episode_number")
            scene_ids = _json_array(row.get("scene_ids_json"))
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number <= 0
                or not scene_ids
            ):
                continue
            task_scenes.setdefault(str(row.get("task_id") or ""), {})[
                str(number)
            ] = scene_ids
        for run_id, task_id in run_tasks.items():
            if task_id in task_scenes:
                domains[run_id]["sceneIdsByEpisode"] = task_scenes[task_id]
        return domains

    async def _call_arguments(
        self,
        targets: Mapping[ToolCallKey, str],
    ) -> dict[ToolCallKey, dict[str, Any]]:
        run_ids = tuple(dict.fromkeys(run_id for run_id, _call_id in targets))
        tool_names = tuple(dict.fromkeys(targets.values()))
        run_marks = ",".join("?" for _ in run_ids)
        tool_marks = ",".join("?" for _ in tool_names)
        rows = await self._db.fetch_all(
            "SELECT e.run_id, json_extract(call.value, '$.id') AS call_id, "
            "json_extract(call.value, '$.name') AS tool_name, "
            "json_extract(call.value, '$.arguments_json') AS arguments_json "
            "FROM ai_agent_run_events AS e "
            "JOIN json_each(CASE WHEN e.event_type = 'runtime.event' "
            "THEN json_extract(e.payload_json, '$.data.calls') "
            "ELSE json_extract(e.payload_json, '$.calls') END) AS call "
            "WHERE e.run_id IN (" + run_marks + ") "
            "AND json_valid(e.payload_json) "
            "AND (e.event_type = 'tool.calls_started' OR "
            "(e.event_type = 'runtime.event' AND "
            "json_extract(e.payload_json, '$.eventType') = "
            "'tool.calls_started')) "
            "AND json_extract(call.value, '$.name') IN (" + tool_marks + ") "
            "ORDER BY e.id",
            [*run_ids, *tool_names],
        )
        result: dict[ToolCallKey, dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("run_id") or ""),
                str(row.get("call_id") or ""),
            )
            if key not in targets or row.get("tool_name") != targets[key]:
                continue
            parsed = _json_object(row.get("arguments_json"))
            result[key] = parsed
        return result


def operation_tool_call_key(
    run_id: object,
    chunk: Mapping[str, Any],
) -> ToolCallKey | None:
    if chunk.get("kind") != "operation.started":
        return None
    payload = chunk.get("payload")
    if not isinstance(payload, Mapping) or payload.get("kind") != "tool":
        return None
    display = payload.get("display")
    if not isinstance(display, Mapping):
        return None
    params = display.get("labelParams")
    if not isinstance(params, Mapping):
        return None
    normalized_run_id = str(run_id or "").strip()
    call_id = str(params.get("toolCallId") or "").strip()
    if not normalized_run_id or not call_id:
        return None
    return normalized_run_id, call_id


def operation_tool_name(chunk: Mapping[str, Any]) -> str | None:
    payload = chunk.get("payload")
    if not isinstance(payload, Mapping):
        return None
    display = payload.get("display")
    if not isinstance(display, Mapping):
        return None
    params = display.get("labelParams")
    if not isinstance(params, Mapping):
        return None
    name = str(params.get("toolName") or "").strip()
    return name or None


async def reproject_screenplay_tool_diagnostics(
    db,
    page: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the same read model to paged developer diagnostics."""

    raw_calls = page.get("calls")
    if not isinstance(raw_calls, list):
        return dict(page)
    targets: dict[ToolCallKey, str] = {}
    for call in raw_calls:
        if not isinstance(call, Mapping):
            continue
        run_id = str(call.get("runId") or page.get("runId") or "").strip()
        call_id = str(call.get("toolCallId") or "").strip()
        tool_name = str(call.get("name") or "").strip()
        if run_id and call_id and tool_name:
            targets[(run_id, call_id)] = tool_name
    presentations = await ScreenplayToolPresentationReadModel(db).project(targets)
    if not presentations:
        return dict(page)
    calls = []
    for call in raw_calls:
        if not isinstance(call, Mapping):
            calls.append(call)
            continue
        run_id = str(call.get("runId") or page.get("runId") or "").strip()
        call_id = str(call.get("toolCallId") or "").strip()
        params = presentations.get((run_id, call_id))
        names = params.get("displayNames") if params is not None else None
        label = names.get("zh-CN") if isinstance(names, Mapping) else None
        calls.append({
            **call,
            **(
                {"displayName": label.strip()}
                if isinstance(label, str) and label.strip()
                else {}
            ),
        })
    return {**page, "calls": calls}


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_array(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


__all__ = [
    "ScreenplayToolPresentationReadModel",
    "operation_tool_call_key",
    "operation_tool_name",
    "reproject_screenplay_tool_diagnostics",
]
