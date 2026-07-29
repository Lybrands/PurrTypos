from __future__ import annotations

import pytest

from domains.screenplay.adaptation_brief import normalize_creative_brief


def _analysis(*, limitations: list[str] | None = None) -> dict:
    return {
        "analysis": {
            "coverage": {
                "limitations": limitations or [],
            },
            "evidence": [{
                "sourceType": "chapter",
                "sourceId": "chapter-1",
                "claim": "广播触发调查。",
            }],
        },
    }


def _brief() -> dict:
    return {
        "logline": "维修员循着异常广播寻找失踪哥哥。",
        "coreConflict": "她必须在真相与执念之间选择。",
        "formatPlan": {
            "targetFormat": "电影",
            "targetDurationMinutes": 110,
            "scopeStrategy": "压缩调查支线。",
            "narrativeEndpoint": "主角确认广播真相。",
        },
        "adaptationDecisions": [{
            "id": "decision-1",
            "action": "preserve",
            "subject": "异常广播",
            "rationale": "它是清晰的主线启动事件。",
            "screenIntent": "作为第一幕激励事件。",
            "sourceAnchors": [{
                "sourceType": "chapter",
                "sourceId": "chapter-1",
            }],
        }],
        "acknowledgedSourceLimitations": [],
    }


def test_book_brief_normalizes_traceable_adaptation_decisions():
    normalized = normalize_creative_brief(
        _brief(),
        project_format="电影",
        source_analysis=_analysis(),
    )

    assert normalized["formatPlan"]["targetDurationMinutes"] == 110
    assert normalized["adaptationDecisions"][0]["sourceAnchors"] == [{
        "sourceType": "chapter",
        "sourceId": "chapter-1",
    }]


def test_book_brief_rejects_anchor_missing_from_accepted_analysis():
    brief = _brief()
    brief["adaptationDecisions"][0]["sourceAnchors"][0]["sourceId"] = "missing"

    with pytest.raises(ValueError, match="不存在的证据"):
        normalize_creative_brief(
            brief,
            project_format="电影",
            source_analysis=_analysis(),
        )


def test_book_brief_must_acknowledge_source_analysis_limitations():
    with pytest.raises(ValueError, match="全部阅读局限"):
        normalize_creative_brief(
            _brief(),
            project_format="电影",
            source_analysis=_analysis(limitations=["第十章仅做首尾抽样。"]),
        )


def test_series_brief_requires_episode_scale():
    brief = _brief()
    brief["formatPlan"] = {
        "targetFormat": "连续剧",
        "scopeStrategy": "按案件拆分单集。",
        "narrativeEndpoint": "季终揭示广播来源。",
    }

    with pytest.raises(ValueError, match="必须确定集数"):
        normalize_creative_brief(
            brief,
            project_format="连续剧",
            source_analysis=_analysis(),
        )
