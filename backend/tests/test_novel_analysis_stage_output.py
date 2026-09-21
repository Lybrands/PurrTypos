from types import SimpleNamespace

import pytest

from agents.novel_analysis.stage_output import (
    NovelAnalysisStageOutput,
    build_stage_output_facts,
)


def test_map_stage_output_supplies_facts_without_composing_public_prose():
    facts = build_stage_output_facts(
        "map",
        {
            "findings": [
                {
                    "dimension": "characters",
                    "subject": "林月与苏文",
                    "analysis": "两条视角线先错位推进，再通过预知与追踪汇合。",
                },
                {
                    "dimension": "plot",
                    "subject": "能力觉醒",
                    "analysis": "危机把能力展示、人物选择和组织任务连成因果链。",
                },
            ],
            "private": "INTERNAL_JSON_MUST_NOT_APPEAR",
        },
        {"displayTitle": "分析人物与情节", "dimensions": ["characters", "plot"]},
    )

    assert facts["stageTitle"] == "分析人物与情节"
    assert facts["findings"][0]["subject"] == "林月与苏文"
    assert "INTERNAL_JSON_MUST_NOT_APPEAR" not in str(facts)
    assert "…" not in str(facts)


def test_synthesis_stage_output_keeps_committed_summary_as_model_evidence():
    summary = "林月由普通毕业生进入犬域，并在追杀中完成首次能力觉醒。"
    facts = build_stage_output_facts(
        "synthesize",
        {
            "summaryMarkdown": summary,
            "facts": [{
                "factKind": "character_summary",
                "subjectKey": "林月",
                "predicate": "人物归纳",
                "value": {"profile_md": summary},
                "id": "private-id",
            }],
            "craftCards": [{
                "title": "章末钩子",
                "bodyMarkdown": "在新信息出现后截断场景。",
            }],
        },
        {"displayTitle": "形成整书分析总结"},
    )

    assert facts["summaryMarkdown"] == summary
    assert facts["facts"][0]["subjectKey"] == "林月"
    assert "private-id" not in str(facts)


@pytest.mark.asyncio
async def test_semantic_milestone_invokes_root_model_reporter_with_signal():
    reports = []
    runtime = object()
    signal = object()

    class Db:
        async def fetch_one(self, query, params):
            if "json_each" in query:
                return {"present": 1}
            raise AssertionError((query, params))

    async def report(**kwargs):
        reports.append(kwargs)

    reporter = NovelAnalysisStageOutput(
        Db(), reporter=report, runtime=runtime,
    )

    async def load_payload(_artifact_id):
        return {
            "findings": [{
                "dimension": "plot",
                "subject": "双线结构",
                "analysis": "两条视角线先错位推进，再在组织线汇合。",
            }],
        }

    reporter._artifacts = SimpleNamespace(load_payload=load_payload)
    context = SimpleNamespace(
        run_id="root-run",
        task=SimpleNamespace(id="analysis-task"),
        unit=SimpleNamespace(
            id="map:extract:0",
            metadata={
                "unitKind": "map",
                "displayTitle": "分析当前小说分片",
                "dimensions": ["characters", "plot"],
            },
        ),
    )
    result = SimpleNamespace(metadata={"artifactId": "artifact-1"})

    await reporter.publish(context, result, signal)

    assert len(reports) == 1
    assert reports[0]["context"] is context
    assert reports[0]["runtime"] is runtime
    assert reports[0]["signal"] is signal
    assert reports[0]["facts"]["findings"][0]["subject"] == "双线结构"
