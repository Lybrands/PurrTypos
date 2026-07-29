from __future__ import annotations

import pytest

from domains.screenplay.structure_trace import normalize_structure_trace


def _brief(*, episode_count: int | None = None) -> dict:
    format_plan = {
        "targetFormat": "电影",
        "targetDurationMinutes": 110,
    }
    if episode_count is not None:
        format_plan = {
            "targetFormat": "连续剧",
            "episodeCount": episode_count,
            "episodeDurationMinutes": 45,
        }
    return {
        "brief": {
            "formatPlan": format_plan,
            "adaptationDecisions": [
                {"id": "keep-radio", "action": "preserve"},
                {"id": "drop-cousin", "action": "omit"},
            ],
        },
    }


def _beats() -> list[dict]:
    return [{
        "id": "beat-1",
        "order": 1,
        "label": "异常广播",
        "summary": "广播触发主角调查。",
    }]


def _coverage() -> list[dict]:
    return [
        {
            "decisionId": "keep-radio",
            "structureUnitIds": ["beat-1"],
            "implementation": "作为第一幕激励事件。",
        },
        {
            "decisionId": "drop-cousin",
            "structureUnitIds": [],
            "implementation": "删除不影响主线的表亲支线。",
        },
    ]


def test_structure_trace_covers_kept_and_omitted_decisions():
    units, coverage = normalize_structure_trace(
        kind="beat_sheet",
        units=_beats(),
        decision_coverage=_coverage(),
        creative_brief=_brief(),
    )

    assert units[0]["id"] == "beat-1"
    assert coverage[1]["structureUnitIds"] == []


def test_structure_trace_rejects_uncovered_brief_decision():
    with pytest.raises(ValueError, match="完整覆盖"):
        normalize_structure_trace(
            kind="beat_sheet",
            units=_beats(),
            decision_coverage=_coverage()[:1],
            creative_brief=_brief(),
        )


def test_omitted_decision_cannot_point_to_structure_unit():
    coverage = _coverage()
    coverage[1]["structureUnitIds"] = ["beat-1"]

    with pytest.raises(ValueError, match="删减决策"):
        normalize_structure_trace(
            kind="beat_sheet",
            units=_beats(),
            decision_coverage=coverage,
            creative_brief=_brief(),
        )


def test_episode_outline_must_match_brief_episode_count():
    with pytest.raises(ValueError, match="集数"):
        normalize_structure_trace(
            kind="episode_outline",
            units=[{
                "id": "episode-1",
                "number": 1,
                "title": "雾中来信",
                "summary": "广播第一次出现。",
            }],
            decision_coverage=[
                {
                    "decisionId": "keep-radio",
                    "structureUnitIds": ["episode-1"],
                    "implementation": "作为首集钩子。",
                },
                {
                    "decisionId": "drop-cousin",
                    "structureUnitIds": [],
                    "implementation": "删除表亲支线。",
                },
            ],
            creative_brief=_brief(episode_count=2),
        )


def test_book_structure_rejects_legacy_brief_without_decisions():
    with pytest.raises(ValueError, match="新版创作简报"):
        normalize_structure_trace(
            kind="beat_sheet",
            units=_beats(),
            decision_coverage=[],
            creative_brief={"brief": {"logline": "旧版简报"}},
            require_adaptation_decisions=True,
        )
