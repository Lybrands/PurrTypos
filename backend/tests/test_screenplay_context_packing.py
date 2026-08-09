from __future__ import annotations

import json

import pytest

from agent_core.errors import ContextOverflowError
from domains.screenplay.context_packing import (
    SCREENPLAY_PROJECT_PREAMBLE,
    pack_screenplay_project_context,
)


def _document(
    document_id: str,
    kind: str,
    version: int,
    status: str,
    *,
    content_json=None,
    content_text: str = "",
):
    return {
        "id": document_id,
        "kind": kind,
        "title": document_id,
        "version": version,
        "status": status,
        "content_json": json.dumps(content_json or {}, ensure_ascii=False),
        "content_text": content_text,
        "derived_from_ids": "[]",
        "update_time": "2026-08-02",
    }


def _project():
    return {
        "id": "project-1",
        "title": "项目",
        "activeStage": "scenes",
    }


def test_scene_stage_packs_only_latest_accepted_relevant_documents():
    documents = (
        _document(
            "outline-accepted",
            "episode_outline",
            2,
            "accepted",
            content_json={"episodes": [{"id": "ep-1"}]},
            content_text="不应重复携带这份 Markdown",
        ),
        _document(
            "outline-old",
            "episode_outline",
            1,
            "draft",
            content_text="旧分集草稿",
        ),
        _document(
            "brief-accepted",
            "creative_brief",
            3,
            "accepted",
            content_json={"brief": {"episodeCount": 10}},
        ),
        _document(
            "analysis-accepted",
            "source_analysis",
            1,
            "accepted",
            content_text="场景阶段不需要完整原作分析",
        ),
    )

    pack = pack_screenplay_project_context(
        project=_project(),
        active_document_id=None,
        documents=documents,
        stage="scenes",
    )

    assert pack.selected_document_ids == (
        "outline-accepted",
        "brief-accepted",
    )
    assert "旧分集草稿" not in pack.content
    assert "场景阶段不需要完整原作分析" not in pack.content
    assert "不应重复携带这份 Markdown" not in pack.content
    payload = json.loads(pack.content.removeprefix(SCREENPLAY_PROJECT_PREAMBLE))
    assert [item["id"] for item in payload["documents"]] == [
        "outline-accepted",
        "brief-accepted",
    ]
    assert payload["documents"][0]["contentFormat"] == "json"
    assert [item["id"] for item in payload["documentIndex"]] == [
        "outline-accepted",
        "brief-accepted",
        "analysis-accepted",
    ]


def test_pack_counts_the_security_preamble_inside_the_allocation():
    documents = (
        _document(
            "outline",
            "episode_outline",
            1,
            "accepted",
            content_json={"episodes": [{"summary": "剧情" * 500}]},
        ),
        _document(
            "brief",
            "creative_brief",
            1,
            "accepted",
            content_json={"brief": {"theme": "主题" * 500}},
        ),
    )
    demand = pack_screenplay_project_context(
        project=_project(),
        active_document_id=None,
        documents=documents,
        stage="scenes",
    )

    fitted = pack_screenplay_project_context(
        project=_project(),
        active_document_id=None,
        documents=documents,
        stage="scenes",
        allocation_tokens=demand.desired_tokens,
    )

    assert fitted.actual_tokens == demand.desired_tokens
    assert fitted.included_document_ids == demand.selected_document_ids


def test_episode_manifest_never_reinjects_hydrated_episode_payloads():
    document = _document(
        "scene-list",
        "scene_list",
        1,
        "accepted",
        content_json={
            "schemaVersion": 2,
            "storageMode": "episode_documents",
            "episodeCount": 2,
            "episodeDocuments": [
                {"episodeNumber": 1, "itemIds": ["s1"]},
                {"episodeNumber": 2, "itemIds": ["s2"]},
            ],
            # Context state may hydrate these for host planning. The model pack
            # must still expose only the manifest and use the episode read tool.
            "scenes": [
                {"id": "s1", "synopsis": "SHOULD_NOT_ENTER_MODEL_CONTEXT"},
                {"id": "s2", "synopsis": "SHOULD_NOT_ENTER_MODEL_CONTEXT"},
            ],
        },
    )

    pack = pack_screenplay_project_context(
        project=_project(),
        active_document_id=None,
        documents=(document,),
        stage="draft",
    )

    assert "SHOULD_NOT_ENTER_MODEL_CONTEXT" not in pack.content
    payload = json.loads(pack.content.removeprefix(SCREENPLAY_PROJECT_PREAMBLE))
    manifest = payload["documents"][0]["content"]
    assert manifest["episodeCount"] == 2
    assert "scenes" not in manifest


def test_pack_fails_when_even_the_minimum_complete_unit_cannot_fit():
    document = _document(
        "outline",
        "episode_outline",
        1,
        "accepted",
        content_json={"episodes": [{"summary": "剧情" * 200}]},
    )
    demand = pack_screenplay_project_context(
        project=_project(),
        active_document_id=None,
        documents=(document,),
        stage="scenes",
    )

    with pytest.raises(ContextOverflowError, match="minimum context"):
        pack_screenplay_project_context(
            project=_project(),
            active_document_id=None,
            documents=(document,),
            stage="scenes",
            allocation_tokens=demand.minimum_tokens - 1,
        )
