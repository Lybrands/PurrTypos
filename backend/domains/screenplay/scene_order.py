"""Canonical scene ordering shared by draft planning and execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def ordered_scene_mappings(
    scene_list_content: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Return scenes in their authoritative episode and local order."""

    raw_scenes = scene_list_content.get("scenes")
    if not isinstance(raw_scenes, Sequence) or isinstance(
        raw_scenes,
        (str, bytes, bytearray),
    ):
        return ()
    indexed = [
        (index, scene)
        for index, scene in enumerate(raw_scenes)
        if isinstance(scene, Mapping)
    ]
    if not indexed:
        return ()
    episode_numbers = [
        _positive_int(scene.get("episodeNumber"))
        for _, scene in indexed
    ]
    episodic = all(number is not None for number in episode_numbers)

    def _key(row: tuple[int, Mapping[str, Any]]) -> tuple[int, int, int]:
        index, scene = row
        order = _positive_int(scene.get("order")) or index + 1
        if episodic:
            episode = _positive_int(scene.get("episodeNumber")) or 1_000_000
            return episode, order, index
        return order, index, index

    return tuple(scene for _, scene in sorted(indexed, key=_key))


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = ["ordered_scene_mappings"]
