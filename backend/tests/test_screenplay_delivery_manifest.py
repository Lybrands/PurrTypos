import pytest

from domains.screenplay.delivery_manifest import build_delivery_manifest


def _document(
    document_id: str,
    kind: str,
    content_json: dict,
    *,
    version: int = 1,
) -> dict:
    return {
        "id": document_id,
        "kind": kind,
        "title": kind,
        "version": version,
        "content_json": content_json,
        "content_text": f"{kind} content",
    }


def _delivery_inputs() -> dict:
    brief = _document("brief-1", "creative_brief", {})
    structure = _document(
        "structure-1",
        "beat_sheet",
        {"creativeBriefId": "brief-1"},
    )
    scene_list = _document(
        "scenes-1",
        "scene_list",
        {"structureId": "structure-1"},
    )
    draft = _document(
        "draft-1",
        "scene_draft",
        {
            "sceneListId": "scenes-1",
            "isComplete": True,
            "completedSceneIds": ["scene-1"],
            "sceneExecutions": [{"sceneId": "scene-1"}],
        },
    )
    review = _document(
        "review-1",
        "review",
        {
            "reviewedDraftId": "draft-1",
            "verdict": "ready",
            "issues": [],
        },
    )
    return {
        "project": {
            "id": "project-1",
            "title": "测试剧本",
            "source_kind": "original",
            "source_book_id": None,
            "source_scope": {"schemaVersion": 1, "mode": "whole_book"},
            "format": "电影",
            "approach": "先找人物",
            "premise": "一个选择。",
        },
        "source_analysis": None,
        "creative_brief": brief,
        "structure": structure,
        "scene_list": scene_list,
        "final_draft": draft,
        "final_review": review,
        "source_ref_counts": {"draft-1": 2},
        "generated_at": "2026-07-29 12:00:00",
    }


def test_delivery_manifest_binds_canonical_lineage_and_digests():
    manifest = build_delivery_manifest(**_delivery_inputs())

    assert manifest["lineage"] == {
        "sourceAnalysisId": None,
        "creativeBriefId": "brief-1",
        "structureId": "structure-1",
        "sceneListId": "scenes-1",
        "finalDraftId": "draft-1",
        "finalReviewId": "review-1",
    }
    assert [item["role"] for item in manifest["documents"]] == [
        "creative_brief",
        "structure",
        "scene_list",
        "final_draft",
        "final_review",
    ]
    assert all(len(item["contentDigest"]) == 64 for item in manifest["documents"])
    assert len(manifest["packageDigest"]) == 64
    assert manifest["qualityGate"]["sceneCount"] == 1
    assert manifest["sourceTrace"]["totalSourceRefCount"] == 2


def test_delivery_manifest_rejects_review_for_another_draft():
    inputs = _delivery_inputs()
    inputs["final_review"]["content_json"]["reviewedDraftId"] = "draft-other"

    with pytest.raises(ValueError, match="最终审阅"):
        build_delivery_manifest(**inputs)
