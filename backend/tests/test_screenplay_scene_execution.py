from __future__ import annotations

import pytest

from domains.screenplay.scene_execution import (
    normalize_scene_execution,
    validate_scene_execution_history,
)


def _scene(scene_id: str) -> dict:
    return {
        "id": scene_id,
        "structureUnitIds": ["beat-1"],
    }


def _execution(scene_id: str) -> dict:
    return {
        "sceneId": scene_id,
        "structureUnitIds": ["beat-1"],
        "objectiveResult": "主角拿到了进入港区的许可。",
        "conflictResult": "守卫提出了更危险的交换条件。",
        "turnResult": "许可来自失踪哥哥的旧身份。",
        "continuityState": "主角带着许可前往港区。",
        "unresolvedNotes": [],
    }


def test_scene_execution_requires_goal_conflict_turn_and_continuity():
    with pytest.raises(ValueError, match="目标、冲突、转折"):
        normalize_scene_execution(
            scene=_scene("scene-1"),
            execution={
                "objectiveResult": "",
                "conflictResult": "阻力升级。",
                "turnResult": "出现转折。",
                "continuityState": "继续调查。",
            },
        )


def test_execution_history_matches_completed_scene_order():
    executions = [_execution("scene-1"), _execution("scene-2")]
    normalized = validate_scene_execution_history(
        scene_list_content={
            "scenes": [_scene("scene-1"), _scene("scene-2")],
        },
        completed_scene_ids=["scene-1", "scene-2"],
        scene_executions=executions,
        previous_executions=executions[:1],
    )

    assert [item["sceneId"] for item in normalized] == [
        "scene-1",
        "scene-2",
    ]


def test_execution_history_cannot_rewrite_previous_scene():
    previous = _execution("scene-1")
    rewritten = {
        **previous,
        "turnResult": "把上一场转折改成另一件事。",
    }

    with pytest.raises(ValueError, match="不能改写"):
        validate_scene_execution_history(
            scene_list_content={
                "scenes": [_scene("scene-1"), _scene("scene-2")],
            },
            completed_scene_ids=["scene-1", "scene-2"],
            scene_executions=[rewritten, _execution("scene-2")],
            previous_executions=[previous],
        )


def test_execution_history_rejects_missing_completed_scene_report():
    with pytest.raises(ValueError, match="一一对应"):
        validate_scene_execution_history(
            scene_list_content={
                "scenes": [_scene("scene-1"), _scene("scene-2")],
            },
            completed_scene_ids=["scene-1", "scene-2"],
            scene_executions=[_execution("scene-1")],
        )
