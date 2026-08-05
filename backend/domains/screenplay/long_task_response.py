"""Typed response contract for one durable screenplay draft batch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_core.contracts import ResponseValidationResult
from agent_core.structured_output import parse_json_object
from domains.screenplay.payload_limits import SCENE_DRAFT_PAYLOAD_LIMITS
from domains.screenplay.scene_execution import normalize_scene_execution


@dataclass(frozen=True, slots=True)
class ScreenplayDraftBatchResponseValidator:
    """Reject malformed batch output before its child Run can succeed."""

    scenes: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        scenes = tuple(self.scenes)
        ids = [str(scene.get("id") or "").strip() for scene in scenes]
        if not scenes or any(not scene_id for scene_id in ids):
            raise ValueError("draft batch validator requires bound scenes")
        if len(ids) != len(set(ids)):
            raise ValueError("draft batch validator scene ids must be unique")
        object.__setattr__(self, "scenes", scenes)

    def validate(
        self,
        *,
        content: str,
        messages: Sequence,
    ) -> ResponseValidationResult:
        del messages
        try:
            payload = parse_json_object(content)
        except Exception:
            return _rejection(
                "screenplay.batch.invalid_json",
                "返回且只返回一个完整 JSON 对象；必须闭合 scenes 数组和根对象，"
                "不要使用 Markdown 代码围栏或解释文字。",
            )
        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list):
            return _rejection(
                "screenplay.batch.scenes_missing",
                "根对象必须包含 scenes 数组。",
            )
        # assistantResponse is presentation metadata, not screenplay data.
        # Missing or overlong copy is repaired deterministically after the
        # structured scene payload has passed validation.  Never regenerate a
        # multi-thousand-token scene batch solely because its UI summary is
        # absent.
        expected_ids = tuple(str(scene["id"]) for scene in self.scenes)
        actual_ids = tuple(
            str(item.get("sceneId") or "")
            for item in raw_scenes
            if isinstance(item, Mapping)
        )
        if (
            len(raw_scenes) != len(expected_ids)
            or actual_ids != expected_ids
        ):
            return _rejection(
                "screenplay.batch.scene_coverage_mismatch",
                "scenes 必须严格按照 requiredSceneIds 的顺序逐项返回，"
                "不得遗漏、增加或改写 sceneId。",
                expectedSceneIds=list(expected_ids),
                actualSceneIds=list(actual_ids),
            )
        for scene, item in zip(self.scenes, raw_scenes, strict=True):
            if not isinstance(item, Mapping):
                return _rejection(
                    "screenplay.batch.scene_invalid",
                    "scenes 中的每一项都必须是对象。",
                )
            scene_text = str(item.get("sceneText") or "").strip()
            if not scene_text or (
                len(scene_text)
                > SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars
            ):
                return _rejection(
                    "screenplay.batch.scene_text_invalid",
                    "每一场必须包含非空且长度合规的 Fountain sceneText。",
                    sceneId=str(scene["id"]),
                )
            try:
                normalize_scene_execution(
                    scene=scene,
                    execution=item.get("execution"),
                )
            except ValueError as error:
                return _rejection(
                    "screenplay.batch.execution_invalid",
                    "每一场的 execution 必须完整填写 objectiveResult、"
                    "conflictResult、turnResult、continuityState 和"
                    " unresolvedNotes 数组。",
                    sceneId=str(scene["id"]),
                    validationReason=str(error)[:240],
                )
            continuity_summary = str(
                item.get("continuitySummary") or ""
            ).strip()
            if not continuity_summary or len(continuity_summary) > 6_000:
                return _rejection(
                    "screenplay.batch.continuity_summary_invalid",
                    "每一场必须包含不超过 6000 字符的非空 continuitySummary。",
                    sceneId=str(scene["id"]),
                )
        return ResponseValidationResult()


def _rejection(
    code: str,
    guidance: str,
    **details: Any,
) -> ResponseValidationResult:
    return ResponseValidationResult(
        violation_code=code,
        repair_guidance=guidance,
        details=details,
    )


__all__ = ["ScreenplayDraftBatchResponseValidator"]
