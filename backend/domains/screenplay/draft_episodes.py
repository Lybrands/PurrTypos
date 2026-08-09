"""Episode-scoped projections for rolling screenplay drafts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_episode_draft_batches(
    *,
    scenes_by_id: Mapping[str, Mapping[str, Any]],
    generated_scenes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group one newly generated scene batch into ordered episode payloads."""

    batches: list[dict[str, Any]] = []
    for generated in generated_scenes:
        scene_id = str(generated.get("sceneId") or "").strip()
        scene = scenes_by_id.get(scene_id)
        if scene is None:
            raise ValueError(
                "episode draft batch contains a scene outside the scene list"
            )
        raw_episode = scene.get("episodeNumber")
        if (
            isinstance(raw_episode, bool)
            or not isinstance(raw_episode, int)
            or raw_episode <= 0
        ):
            # A standalone screenplay is persisted as one logical episode so
            # it follows the same storage and retrieval contract.
            raw_episode = 1
        scene_text = str(generated.get("sceneText") or "").strip()
        if not scene_text:
            raise ValueError("episode draft scene text is required")
        execution = generated.get("execution")
        if not isinstance(execution, Mapping):
            raise ValueError("episode draft scene execution is required")
        continuity_summary = str(
            generated.get("continuitySummary")
            or execution.get("continuityState")
            or ""
        ).strip()

        if not batches or batches[-1]["episodeNumber"] != raw_episode:
            batches.append({
                "episodeNumber": raw_episode,
                "sceneIds": [],
                "sceneExecutions": [],
                "sceneTexts": [],
                "continuitySummary": "",
            })
        batch = batches[-1]
        batch["sceneIds"].append(scene_id)
        batch["sceneExecutions"].append(dict(execution))
        batch["sceneTexts"].append({
            "sceneId": scene_id,
            "contentText": scene_text,
        })
        if continuity_summary:
            batch["continuitySummary"] = continuity_summary[:6_000]

    return [
        {
            "episodeNumber": batch["episodeNumber"],
            "sceneIds": batch["sceneIds"],
            "sceneExecutions": batch["sceneExecutions"],
            "sceneTexts": batch["sceneTexts"],
            "contentText": "\n\n".join(
                item["contentText"] for item in batch["sceneTexts"]
            ),
            "continuitySummary": batch["continuitySummary"],
        }
        for batch in batches
    ]


__all__ = ["build_episode_draft_batches"]
