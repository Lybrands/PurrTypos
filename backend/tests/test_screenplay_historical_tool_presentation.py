from __future__ import annotations

import pytest

from agents.screenplay.legacy_tool_labels import (
    legacy_screenplay_operation_display_params,
)


@pytest.mark.parametrize(
    ("domain", "arguments", "tool_name", "expected"),
    (
        (
            {"boundEpisodeNumber": 1, "sceneIds": ["s1", "s2"]},
            {"partKeys": ["draft:1:s2"]},
            "readScreenplayTaskDependencies",
            "读取第 1 集第 2 场已完成剧本",
        ),
        (
            {"boundEpisodeNumber": 3},
            {"role": "structure", "representation": "text"},
            "readScreenplayDeliverable",
            "为第 3 集读取分集结构正文",
        ),
        (
            {
                "boundEpisodeNumber": 2,
                "sourceScope": {
                    "chapters": [{"id": "c1", "title": "第一章"}],
                },
            },
            {"chapterIds": ["c1"]},
            "readSourceChapters",
            "为第 2 集读取第一章的原文",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"query": "月蚀", "roles": ["creativeBrief", "structure"]},
            "searchScreenplayDeliverables",
            "为第 2 集在创作简报、分集结构中查找“月蚀”",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"query": "失踪", "kinds": ["character_state", "plot_thread"]},
            "querySourceStoryFacts",
            "为第 2 集在人物状态、情节线索中查找与“失踪”相关的记录",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"query": "港口", "types": ["location", "faction"]},
            "listSourceWorldEntities",
            "为第 2 集在地点、势力中筛选与“港口”相关的条目",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"characterIds": ["a", "b"]},
            "readSourceCharacters",
            "为第 2 集读取所选 2 位原作人物的资料",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"entityIds": ["a", "b"]},
            "readSourceWorldEntities",
            "为第 2 集读取所选 2 条世界设定",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"outlineIds": ["a", "b"]},
            "readSourceOutline",
            "为第 2 集读取所选 2 条原作大纲",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"cursor": 4},
            "inspectSourceStructure",
            "为第 2 集查看原作后续章节目录",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"query": "暗门"},
            "searchSourceText",
            "为第 2 集在原文中查找“暗门”",
        ),
        (
            {"boundEpisodeNumber": 2},
            {"query": "主角"},
            "listSourceCharacters",
            "为第 2 集筛选与“主角”相关的原作人物",
        ),
        (
            {
                "boundEpisodeNumber": 1,
                "sceneIds": ["s1", "s2"],
                "expectedPartKey": "s2",
            },
            {},
            "getScreenplaySceneContext",
            "为第 1 集读取第 2 场的场景计划、衔接与改编依据",
        ),
    ),
)
def test_legacy_read_tool_labels_remain_stable(
    domain: dict[str, object],
    arguments: dict[str, object],
    tool_name: str,
    expected: str,
) -> None:
    params = legacy_screenplay_operation_display_params(
        domain,
        arguments,
        tool_name,
    )

    assert params["displayNames"] == {"zh-CN": expected}
