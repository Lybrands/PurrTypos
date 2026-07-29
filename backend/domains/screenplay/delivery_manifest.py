"""Build the immutable delivery snapshot for a completed screenplay project."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any


def build_delivery_manifest(
    *,
    project: Mapping[str, Any],
    source_analysis: Mapping[str, Any] | None,
    creative_brief: Mapping[str, Any],
    structure: Mapping[str, Any],
    scene_list: Mapping[str, Any],
    final_draft: Mapping[str, Any],
    final_review: Mapping[str, Any],
    source_ref_counts: Mapping[str, int],
    generated_at: str,
) -> dict[str, Any]:
    source_kind = str(project.get("source_kind") or "")
    if source_kind == "book" and source_analysis is None:
        raise ValueError("书架改编交付缺少原作范围分析")
    if source_kind != "book" and source_analysis is not None:
        raise ValueError("原创剧本交付不应绑定原作范围分析")

    analysis_id = _document_id(source_analysis) if source_analysis else None
    brief_id = _document_id(creative_brief)
    structure_id = _document_id(structure)
    scene_list_id = _document_id(scene_list)
    draft_id = _document_id(final_draft)
    review_id = _document_id(final_review)
    expected_structure_kind = (
        "episode_outline"
        if str(project.get("format") or "") in {"连续剧", "竖屏短剧"}
        else "beat_sheet"
    )
    if str(structure.get("kind") or "") != expected_structure_kind:
        raise ValueError("交付结构类型与项目形态不匹配")

    brief_json = _content_json(creative_brief)
    structure_json = _content_json(structure)
    scene_list_json = _content_json(scene_list)
    draft_json = _content_json(final_draft)
    review_json = _content_json(final_review)
    if source_kind == "book" and str(
        brief_json.get("sourceAnalysisId") or ""
    ) != analysis_id:
        raise ValueError("交付创作简报没有绑定当前原作范围分析")
    if str(structure_json.get("creativeBriefId") or "") != brief_id:
        raise ValueError("交付结构没有绑定当前创作简报")
    if str(scene_list_json.get("structureId") or "") != structure_id:
        raise ValueError("交付场景表没有绑定当前结构版本")
    if (
        str(draft_json.get("sceneListId") or "") != scene_list_id
        or draft_json.get("isComplete") is not True
    ):
        raise ValueError("交付剧本不是当前场景表的完整整稿")
    if (
        str(review_json.get("reviewedDraftId") or "") != draft_id
        or str(review_json.get("verdict") or "") != "ready"
        or review_json.get("issues") != []
    ):
        raise ValueError("最终审阅没有确认当前完整剧本可交付")
    verification_results = review_json.get("verificationResults")
    if isinstance(verification_results, list) and any(
        not isinstance(item, Mapping)
        or str(item.get("status") or "") != "verified"
        for item in verification_results
    ):
        raise ValueError("最终复审仍包含未通过的历史问题")

    completed_scene_ids = draft_json.get("completedSceneIds")
    scene_executions = draft_json.get("sceneExecutions")
    if (
        not isinstance(completed_scene_ids, list)
        or not isinstance(scene_executions, list)
        or len(completed_scene_ids) != len(scene_executions)
    ):
        raise ValueError("最终剧本的场景执行追踪不完整")

    document_roles: list[tuple[str, Mapping[str, Any]]] = []
    if source_analysis is not None:
        document_roles.append(("source_analysis", source_analysis))
    document_roles.extend((
        ("creative_brief", creative_brief),
        ("structure", structure),
        ("scene_list", scene_list),
        ("final_draft", final_draft),
        ("final_review", final_review),
    ))
    documents = [
        {
            "role": role,
            "documentId": _document_id(document),
            "kind": str(document.get("kind") or ""),
            "title": str(document.get("title") or ""),
            "version": int(document.get("version") or 0),
            "contentDigest": document_content_digest(document),
            "sourceRefCount": int(
                source_ref_counts.get(_document_id(document), 0)
            ),
        }
        for role, document in document_roles
    ]
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "projectSnapshot": {
            "projectId": str(project.get("id") or ""),
            "title": str(project.get("title") or ""),
            "sourceKind": source_kind,
            "sourceBookId": project.get("source_book_id"),
            "sourceScope": project.get("source_scope"),
            "format": str(project.get("format") or ""),
            "approach": str(project.get("approach") or ""),
            "premise": str(project.get("premise") or ""),
        },
        "lineage": {
            "sourceAnalysisId": analysis_id,
            "creativeBriefId": brief_id,
            "structureId": structure_id,
            "sceneListId": scene_list_id,
            "finalDraftId": draft_id,
            "finalReviewId": review_id,
        },
        "documents": documents,
        "qualityGate": {
            "verdict": "ready",
            "openIssueCount": 0,
            "verifiedPriorIssueCount": (
                len(verification_results)
                if isinstance(verification_results, list)
                else 0
            ),
            "sceneCount": len(completed_scene_ids),
            "sceneExecutionCount": len(scene_executions),
        },
        "sourceTrace": {
            "totalSourceRefCount": sum(
                int(item["sourceRefCount"]) for item in documents
            ),
        },
    }
    manifest["packageDigest"] = sha256(_canonical_json(manifest)).hexdigest()
    manifest["generatedAt"] = generated_at
    return manifest


def document_content_digest(document: Mapping[str, Any]) -> str:
    payload = {
        "contentJson": _content_json(document),
        "contentText": str(document.get("content_text") or ""),
    }
    return sha256(_canonical_json(payload)).hexdigest()


def _document_id(document: Mapping[str, Any] | None) -> str:
    document_id = str((document or {}).get("id") or "").strip()
    if not document_id:
        raise ValueError("交付清单包含无效文档")
    return document_id


def _content_json(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("content_json")
    if not isinstance(value, Mapping):
        raise ValueError("交付文档缺少结构化内容")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
