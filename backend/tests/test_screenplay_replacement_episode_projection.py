from __future__ import annotations

import json

import pytest
import pytest_asyncio

from agents.screenplay.revision_projector import (
    ScreenplayReplacementRevisionProjector,
    _assemble_parts,
)
from database.connection import DatabaseConnection
from domains.screenplay.review_adjudication import derive_review_state


def _candidate(number: int, scene_id: str) -> dict[str, object]:
    return {
        "partKey": f"scene-list:episode:{number}",
        "partKind": "document_section",
        "episodeNumber": number,
        "payload": {
            "payload": {
                "episodeNumber": number,
                "title": f"第{number}集",
                "scenes": [{"id": scene_id, "heading": f"场景{number}"}],
            },
        },
    }


def test_scene_list_projection_builds_document_and_episode_parts() -> None:
    parts = _assemble_parts(
        "sceneList",
        (_candidate(2, "ep02-s01"), _candidate(1, "ep01-s01")),
    )

    assert [(part["partType"], part["partKey"]) for part in parts] == [
        ("document", "main"),
        ("episode", "1"),
        ("episode", "2"),
    ]
    assert [scene["id"] for scene in parts[0]["payload"]["scenes"]] == [
        "ep01-s01",
        "ep02-s01",
    ]
    assert parts[1]["payload"]["scenes"][0]["episodeNumber"] == 1


def test_review_projection_builds_authoritative_product_contract() -> None:
    parts = _assemble_parts(
        "review",
        ({
            "partKey": "review:main",
            "partKind": "review_dimension",
            "payload": {
                "payload": {"verdict": "ready", "issues": []},
            },
        },),
        reviewed_draft_id="draft-1",
    )

    assert parts[0]["payload"] == {
        "inputContractVersion": 2,
        "reviewedDraftId": "draft-1",
        "verdict": "ready",
        "issues": [],
    }
    state = derive_review_state(
        draft_revision_id="draft-1",
        draft_content={"isComplete": True},
        review_revision_id="review-1",
        review_content=parts[0]["payload"],
        decisions=(),
        hard_checks=(),
        completion_source=None,
    )
    assert state["phase"] == "readyToFinalize"
    assert state["canFinalize"] is True


def test_review_projection_defaults_omitted_issue_severity() -> None:
    parts = _assemble_parts(
        "review",
        ({
            "partKey": "review:main",
            "partKind": "review_dimension",
            "payload": {"payload": {
                "verdict": "revise",
                "issues": [{
                    "id": "continuity-1",
                    "description": "场景衔接不清楚。",
                    "sceneIds": ["scene-1"],
                }],
            }},
        },),
        reviewed_draft_id="draft-1",
    )

    assert parts[0]["payload"]["issues"][0]["severity"] == "minor"


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_partial_scene_list_projection_inherits_unmodified_episodes(temp_db) -> None:
    await temp_db.execute(
        "INSERT INTO screenplay_projects (id, title) VALUES ('project-episodes', '分集')"
    )
    deliverable = await temp_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = 'project-episodes' AND role = 'sceneList'"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('scene-list-base', 'project-episodes', ?, 1, 'base', 'user')",
        [deliverable["id"]],
    )
    base_episode = {
        "episodeNumber": 1,
        "title": "第一集",
        "scenes": [{"id": "ep01-s01", "episodeNumber": 1}],
    }
    await temp_db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, content_text, "
        "content_digest) VALUES "
        "('scene-list-base', 'episode', '1', 1, ?, '第一集', 'episode-1')",
        [json.dumps(base_episode)],
    )

    current = _assemble_parts("sceneList", (_candidate(2, "ep02-s01"),))
    merged = await ScreenplayReplacementRevisionProjector(
        temp_db
    )._merge_episode_snapshot(
        target_role="sceneList",
        parent_revision_id="scene-list-base",
        current_parts=current,
    )

    assert [part["partKey"] for part in merged[1:]] == ["1", "2"]
    assert [scene["id"] for scene in merged[0]["payload"]["scenes"]] == [
        "ep01-s01",
        "ep02-s01",
    ]
