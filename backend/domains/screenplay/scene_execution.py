"""Validate cumulative scene execution reports for rolling screenplay drafts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def normalize_scene_execution(
    *,
    scene: Mapping[str, Any],
    execution: object,
) -> dict[str, Any]:
    if not isinstance(execution, Mapping):
        raise ValueError("当前场景必须包含结构化 execution 执行记录")
    scene_id = str(scene.get("id") or "").strip()
    if not scene_id:
        raise ValueError("场景执行记录无法绑定有效场景")
    fields = (
        "objectiveResult",
        "conflictResult",
        "turnResult",
        "continuityState",
    )
    normalized_fields = {
        key: str(execution.get(key) or "").strip()
        for key in fields
    }
    if any(not value for value in normalized_fields.values()):
        raise ValueError("场景执行记录必须说明目标、冲突、转折和场尾连续性")
    unresolved_notes = _string_list(execution.get("unresolvedNotes"))
    structure_unit_ids = _string_list(scene.get("structureUnitIds"))
    return {
        "sceneId": scene_id,
        "structureUnitIds": structure_unit_ids,
        **normalized_fields,
        "unresolvedNotes": unresolved_notes,
    }


def validate_scene_execution_history(
    *,
    scene_list_content: Mapping[str, Any],
    completed_scene_ids: object,
    scene_executions: object,
    previous_executions: object = (),
) -> list[dict[str, Any]]:
    raw_scenes = scene_list_content.get("scenes")
    if not _is_list(raw_scenes):
        raise ValueError("当前场景表缺少结构化场景")
    scenes = {
        str(scene.get("id") or "").strip(): scene
        for scene in raw_scenes
        if isinstance(scene, Mapping)
        and str(scene.get("id") or "").strip()
    }
    completed_ids = _string_list(completed_scene_ids)
    if not _is_list(scene_executions):
        raise ValueError("正文必须包含累计 sceneExecutions")
    if len(scene_executions) != len(completed_ids):
        raise ValueError("sceneExecutions 必须与已完成场景一一对应")
    normalized: list[dict[str, Any]] = []
    for expected_id, execution in zip(
        completed_ids,
        scene_executions,
        strict=True,
    ):
        scene = scenes.get(expected_id)
        if scene is None:
            raise ValueError("sceneExecutions 引用了当前场景表之外的场景")
        if (
            not isinstance(execution, Mapping)
            or str(execution.get("sceneId") or "").strip() != expected_id
        ):
            raise ValueError("sceneExecutions 顺序必须与 completedSceneIds 一致")
        normalized_execution = normalize_scene_execution(
            scene=scene,
            execution=execution,
        )
        normalized.append(normalized_execution)
    if not _is_list(previous_executions):
        raise ValueError("上一版正文缺少有效的 sceneExecutions")
    if len(previous_executions) > len(normalized):
        raise ValueError("滚动正文不能丢失既有场景执行记录")
    previous_normalized: list[dict[str, Any]] = []
    for item in previous_executions:
        if not isinstance(item, Mapping):
            raise ValueError("上一版正文包含无效的场景执行记录")
        previous_scene_id = str(item.get("sceneId") or "").strip()
        previous_scene = scenes.get(previous_scene_id)
        if previous_scene is None:
            raise ValueError("上一版执行记录不属于当前场景表")
        previous_normalized.append(normalize_scene_execution(
            scene=previous_scene,
            execution=item,
        ))
    if normalized[:len(previous_normalized)] != previous_normalized:
        raise ValueError("滚动正文不能改写既有场景执行记录")
    return normalized


def _string_list(value: object) -> list[str]:
    if not _is_list(value):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
