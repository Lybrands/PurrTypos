from __future__ import annotations

import pytest

from domains.screenplay.scene_trace import normalize_scene_trace


def _scene(**updates) -> dict:
    value = {
        "id": "scene-1",
        "order": 1,
        "heading": "内景·电台·夜",
        "structureUnitIds": ["beat-1"],
        "objective": "确认信号来源",
        "conflict": "信号即将消失",
        "turn": "听见哥哥的声音",
        "synopsis": "林岚修理设备时收到异常广播。",
    }
    value.update(updates)
    return value


def test_film_scene_can_implement_multiple_beats():
    scene = _scene(structureUnitIds=["beat-1", "beat-2"])
    normalized = normalize_scene_trace(
        scenes=[scene],
        structure_kind="beat_sheet",
        structure_content={"beats": [
            {"id": "beat-1", "order": 1},
            {"id": "beat-2", "order": 2},
        ]},
    )

    assert normalized[0]["structureUnitIds"] == ["beat-1", "beat-2"]


def test_scene_list_must_cover_every_structure_unit():
    with pytest.raises(ValueError, match="每个结构单元"):
        normalize_scene_trace(
            scenes=[_scene()],
            structure_kind="beat_sheet",
            structure_content={"beats": [
                {"id": "beat-1", "order": 1},
                {"id": "beat-2", "order": 2},
            ]},
        )


def test_series_scene_must_match_exactly_one_episode():
    with pytest.raises(ValueError, match="只能归属一个分集"):
        normalize_scene_trace(
            scenes=[_scene(
                episodeNumber=1,
                structureUnitIds=["episode-1", "episode-2"],
            )],
            structure_kind="episode_outline",
            structure_content={"episodes": [
                {"id": "episode-1", "number": 1},
                {"id": "episode-2", "number": 2},
            ]},
        )


def test_series_scene_episode_number_must_match_mapping():
    with pytest.raises(ValueError, match="episodeNumber"):
        normalize_scene_trace(
            scenes=[_scene(
                episodeNumber=2,
                structureUnitIds=["episode-1"],
            )],
            structure_kind="episode_outline",
            structure_content={"episodes": [
                {"id": "episode-1", "number": 1},
            ]},
        )


def test_series_scene_episode_number_is_derived_from_mapping_when_omitted():
    normalized = normalize_scene_trace(
        scenes=[_scene(structureUnitIds=["episode-2"])],
        structure_kind="episode_outline",
        structure_content={"episodes": [
            {"id": "episode-2", "number": 2},
        ]},
    )

    assert normalized[0]["episodeNumber"] == 2


def test_series_scenes_cannot_return_to_an_earlier_episode():
    with pytest.raises(ValueError, match="分集顺序"):
        normalize_scene_trace(
            scenes=[
                _scene(
                    id="scene-2",
                    order=1,
                    episodeNumber=2,
                    structureUnitIds=["episode-2"],
                ),
                _scene(
                    id="scene-1",
                    order=2,
                    episodeNumber=1,
                    structureUnitIds=["episode-1"],
                ),
            ],
            structure_kind="episode_outline",
            structure_content={"episodes": [
                {"id": "episode-1", "number": 1},
                {"id": "episode-2", "number": 2},
            ]},
        )


def test_legacy_structure_without_ids_requires_revision():
    with pytest.raises(ValueError, match="新版结构"):
        normalize_scene_trace(
            scenes=[_scene()],
            structure_kind="beat_sheet",
            structure_content={"beats": [{"label": "开场"}]},
        )
