from types import SimpleNamespace

import pytest

from agents.novel_analysis.stage_output import NovelAnalysisStageOutput
from agents.novel_analysis.stage_output import build_stage_output_text


def test_map_stage_output_contains_findings_not_internal_payload():
    text = build_stage_output_text(
        "map",
        {
            "findings": [
                {
                    "subject": "林月与苏文",
                    "analysis": "两条视角线先错位推进，再通过预知与追踪汇合。",
                },
                {
                    "subject": "能力觉醒",
                    "analysis": "危机把能力展示、人物选择和后续组织任务连成同一条因果链。",
                },
            ],
            "private": "INTERNAL_JSON_MUST_NOT_APPEAR",
        },
        {"dimensions": ["characters", "plot"]},
    )

    assert text.startswith("人物、设定与情节分析已经形成较稳定的判断。")
    assert "两条视角线先错位推进" in text
    assert "危机把能力展示" in text
    assert "INTERNAL_JSON_MUST_NOT_APPEAR" not in text
    assert len(text) <= 270


def test_synthesis_and_review_stage_outputs_explain_actual_conclusions():
    payload = {
        "facts": [
            {
                "subjectKey": "林月",
                "value": "由普通毕业生进入犬域，并在追杀中完成首次能力觉醒。",
            },
        ],
        "craftCards": [
            {
                "title": "章末钩子",
                "bodyMarkdown": "在新信息出现后截断场景，推动读者进入下一章。",
            },
        ],
    }

    synthesis = build_stage_output_text("synthesize", payload, {})
    review = build_stage_output_text("review", payload, {})

    assert "林月" in synthesis and "章末钩子" in synthesis
    assert "接下来核对" in synthesis
    assert "审核已经完成" in review
    assert "人工确认" in review


@pytest.mark.asyncio
async def test_semantic_milestone_persists_and_publishes_public_commentary():
    drafts = []
    published = []

    class Db:
        async def fetch_one(self, query, params):
            if "json_each" in query:
                return {"present": 1}
            return {"turn_id": "turn-analysis"}

    class Output:
        async def append_event(self, draft):
            drafts.append(draft)
            return draft

    class Publisher:
        async def publish_committed(self, event):
            published.append(event)

    reporter = NovelAnalysisStageOutput(
        Db(), output_repository=Output(), publisher=Publisher()
    )
    reporter._artifacts = SimpleNamespace(load_payload=lambda _artifact_id: None)

    async def load_payload(_artifact_id):
        return {
            "findings": [{
                "subject": "双线结构",
                "analysis": "林月的冒险线与苏文的预知线先错位推进，再在组织线汇合。",
            }],
        }

    reporter._artifacts.load_payload = load_payload
    context = SimpleNamespace(
        run_id="root-run",
        task=SimpleNamespace(id="analysis-task"),
        unit=SimpleNamespace(
            id="map:extract:0",
            metadata={
                "unitKind": "map",
                "dimensions": ["characters", "plot"],
            },
        ),
    )
    result = SimpleNamespace(metadata={"artifactId": "artifact-1"})

    await reporter.publish(context, result)

    assert published == drafts
    assert len(drafts) == 1
    assert drafts[0].channel.value == "commentary"
    assert drafts[0].visibility.value == "public"
    assert drafts[0].payload["eventType"] == "novel_analysis.stage_output"
    assert "双线结构" in drafts[0].payload["data"]["text"]
