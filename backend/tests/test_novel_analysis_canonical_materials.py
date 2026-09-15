import pytest

from agents.novel_analysis.canonical_materials import (
    CanonicalAnalysisMaterialError,
    validate_canonical_materials,
)


def _payload():
    return {
        "summaryMarkdown": "整书总结",
        "facts": [{
            "id": "fact-thread", "claimNature": "unresolved",
            "factKind": "unresolved_plot", "subjectKey": "失踪案",
            "predicate": "未决情节", "value": "真相尚未揭晓",
            "lifecycleStatus": "active",
        }, {
            "id": "fact-background", "claimNature": "summary",
            "factKind": "background", "subjectKey": "故事背景",
            "predicate": "背景归纳", "value": {"content": "故事发生在潮湿旧城。"},
            "lifecycleStatus": "active",
        }],
        "craftCards": [{
            "id": "craft-delay", "cardKind": "technique",
            "title": "延迟揭示", "bodyMarkdown": "分阶段释放线索。",
            "observationIds": ["map-observation-1"],
        }],
    }


def test_materials_are_normalized_to_existing_continuation_canon():
    result = validate_canonical_materials(_payload())
    assert result["facts"][0]["factKind"] == "unresolved_plot"
    assert result["facts"][0]["claimNature"] == "fact"
    assert "observationIds" not in result["craftCards"][0]


def test_material_lifecycle_is_owned_by_the_host():
    report = _payload()
    report["facts"][0].pop("lifecycleStatus")
    report["facts"][1]["lifecycleStatus"] = "superseded"
    report["facts"][1]["value"]["lifecycleStatus"] = "resolved"

    result = validate_canonical_materials(report)

    assert [item["lifecycleStatus"] for item in result["facts"]] == [
        "active", "active",
    ]
    assert "lifecycleStatus" not in result["facts"][1]["value"]


def test_materials_ignore_additional_analysis_fields():
    report = _payload()
    report["sections"] = []
    report["facts"][0]["sourceNotes"] = "仅供分析阶段使用"
    report["craftCards"][0]["confidence"] = 0.8
    normalized = validate_canonical_materials(report)
    assert "sections" not in normalized
    assert "sourceNotes" not in normalized["facts"][0]
    assert "confidence" not in normalized["craftCards"][0]

def test_character_detail_subject_does_not_need_to_duplicate_character_name():
    payload = _payload()
    payload["facts"].extend([{
        "id": "fact-suwen",
        "claimNature": "summary",
        "factKind": "character_summary",
        "subjectKey": "苏文",
        "predicate": "人物归纳",
        "value": {"name": "苏文", "tags": "镜眠", "profile_md": "梦境引导者"},
        "lifecycleStatus": "active",
    }, {
        "id": "fact-suwen-dream",
        "claimNature": "fact",
        "factKind": "character_knowledge",
        "subjectKey": "苏文的梦境预知",
        "predicate": "能力知识",
        "value": "能够在梦中预知片段",
        "lifecycleStatus": "active",
    }])
    result = validate_canonical_materials(payload)
    assert result["facts"][-1]["subjectKey"] == "苏文的梦境预知"


def test_materials_require_story_background_and_creation_material_shapes():
    missing = _payload()
    missing["facts"] = [item for item in missing["facts"] if item["factKind"] != "background"]
    with pytest.raises(CanonicalAnalysisMaterialError, match="background is missing"):
        validate_canonical_materials(missing)

    invalid = _payload()
    invalid["facts"][-1]["value"] = "旧的分析专用字符串"
    with pytest.raises(CanonicalAnalysisMaterialError, match="must be an object"):
        validate_canonical_materials(invalid)


def test_setting_kind_uses_explicit_creation_entity_type():
    payload = _payload()
    payload["facts"].append({
        "id": "fact-location",
        "claimNature": "summary",
        "factKind": "setting",
        "subjectKey": "犬域",
        "predicate": "场景设定",
        "value": {
            "entity_type": "location",
            "name": "犬域",
            "tags": "异空间",
            "profile_md": "独立异空间。",
        },
    })

    result = validate_canonical_materials(payload)

    assert result["facts"][-1]["factKind"] == "location"
    assert result["facts"][-1]["value"]["entity_type"] == "location"
