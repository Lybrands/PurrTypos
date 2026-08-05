"""Validate the mapping from accepted structure units into screenplay scenes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def normalize_scene_trace(
    *,
    scenes: object,
    structure_kind: str,
    structure_content: Mapping[str, Any],
) -> list[dict[str, Any]]:
    units = _structure_units(structure_kind, structure_content)
    if not units:
        raise ValueError("当前结构版本缺少稳定单元 ID，请先接受新版结构")
    if not _is_list(scenes) or not scenes:
        raise ValueError("场景表必须包含至少一个场景")
    normalized: list[dict[str, Any]] = []
    seen_scene_ids: set[str] = set()
    covered_unit_ids: set[str] = set()
    is_series = structure_kind == "episode_outline"
    previous_episode_number: int | None = None
    for expected_order, scene in enumerate(scenes, start=1):
        if not isinstance(scene, Mapping):
            raise ValueError("每个场景都必须是结构化对象")
        scene_id = str(scene.get("id") or "").strip()
        if not scene_id or scene_id in seen_scene_ids:
            raise ValueError("每个场景必须具有唯一且非空的稳定 ID")
        seen_scene_ids.add(scene_id)
        try:
            order = int(scene.get("order"))
        except (TypeError, ValueError):
            raise ValueError("场景 order 必须从 1 开始连续递增") from None
        if order != expected_order:
            raise ValueError("场景 order 必须从 1 开始连续递增")
        required_text = ("heading", "objective", "conflict", "turn", "synopsis")
        if any(not str(scene.get(key) or "").strip() for key in required_text):
            raise ValueError("每个场景必须包含标题、目标、冲突、转折和梗概")
        structure_unit_ids = _string_list(scene.get("structureUnitIds"))
        if not structure_unit_ids:
            raise ValueError("每个场景必须映射至少一个结构单元")
        if not set(structure_unit_ids).issubset(units):
            raise ValueError("场景引用了当前结构版本中不存在的结构单元")
        normalized_scene = dict(scene)
        normalized_scene["id"] = scene_id
        normalized_scene["order"] = order
        normalized_scene["structureUnitIds"] = structure_unit_ids
        for key in required_text:
            normalized_scene[key] = str(scene.get(key) or "").strip()
        if is_series:
            if len(structure_unit_ids) != 1:
                raise ValueError("连续剧中的每个场景必须且只能归属一个分集")
            expected_episode = units[structure_unit_ids[0]]
            declared_episode = scene.get("episodeNumber")
            if declared_episode is None:
                # structureUnitIds is the authoritative scene-to-episode
                # mapping. Deriving the redundant display field keeps legacy
                # and provider-generated batches finalizable while preserving
                # the strict one-scene-to-one-episode invariant above.
                episode_number = expected_episode
            else:
                try:
                    episode_number = int(declared_episode)
                except (TypeError, ValueError):
                    raise ValueError(
                        "连续剧场景 episodeNumber 必须是有效集数"
                    ) from None
            if episode_number != expected_episode:
                raise ValueError("场景 episodeNumber 与所映射分集不一致")
            if (
                previous_episode_number is not None
                and episode_number < previous_episode_number
            ):
                raise ValueError("连续剧场景必须按分集顺序连续排列")
            previous_episode_number = episode_number
            normalized_scene["episodeNumber"] = episode_number
        covered_unit_ids.update(structure_unit_ids)
        normalized.append(normalized_scene)
    missing = set(units).difference(covered_unit_ids)
    if missing:
        raise ValueError("场景表必须至少用一个场景承接每个结构单元")
    return normalized


def _structure_units(
    kind: str,
    content: Mapping[str, Any],
) -> dict[str, int | None]:
    if kind == "beat_sheet":
        raw_units = content.get("beats")
        position_key = "order"
    elif kind == "episode_outline":
        raw_units = content.get("episodes")
        position_key = "number"
    else:
        return {}
    if not _is_list(raw_units):
        return {}
    result: dict[str, int | None] = {}
    for unit in raw_units:
        if not isinstance(unit, Mapping):
            continue
        unit_id = str(unit.get("id") or "").strip()
        if not unit_id or unit_id in result:
            return {}
        try:
            position = int(unit.get(position_key))
        except (TypeError, ValueError):
            return {}
        result[unit_id] = position if kind == "episode_outline" else None
    return result


def _string_list(value: object) -> list[str]:
    if not _is_list(value):
        raise ValueError("structureUnitIds 必须是数组")
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
