from __future__ import annotations

import hashlib
import json

import pytest

from agents.writing.technique_usage import (
    project_replacement_writing_technique_usage,
)
from database.connection import DatabaseConnection


@pytest.mark.asyncio
async def test_projects_frozen_auto_selection_without_technique_body(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute(
            "INSERT INTO books(id,title) VALUES ('book','Usage Book')"
        )
        await db.execute(
            "INSERT INTO ai_sessions(id,book_id,scope) "
            "VALUES (1,'book','setting')"
        )
        ref = {
            "kind": "technique",
            "id": "technique-one",
            "versionId": "a" * 64,
        }
        candidate = {
            "ref": ref,
            "members": [ref],
            "metadata": {"name": "信息控制", "description": "认知差异"},
        }
        snapshot_json = json.dumps({
            "schemaVersion": 1,
            "bookId": "book",
            "mode": "auto",
            "manual": [],
            "candidates": [candidate],
        })
        snapshot_digest = (
            "sha256:" + hashlib.sha256(snapshot_json.encode()).hexdigest()
        )
        await db.execute(
            "INSERT INTO writing_technique_request_inputs"
            "(id,book_id,session_id,request_digest,snapshot_json) "
            "VALUES ('replacement-input','book','1','request-digest',?)",
            [snapshot_json],
        )
        attributes = {
            "agentImplementation": {
                "agentKind": "writing",
                "implementationId": "purra-native",
            },
            "contextSelection": {
                "writingTechniqueInputId": "replacement-input"
            },
            "writingContextSnapshot": {"writingTechniqueInput": {
                "inputId": "replacement-input",
                "requestDigest": "request-digest",
                "snapshotDigest": snapshot_digest,
            }},
        }
        await db.execute(
            "INSERT INTO ai_agent_runs(id,status,binding_attributes_json) "
            "VALUES ('replacement-run','done',?)",
            [json.dumps(attributes)],
        )
        entry = {
            "ref": ref,
            "sources": [ref],
            "path": "SKILL.md",
            "content": "private technique instructions",
            "sha256": "entry-hash",
        }
        await db.execute(
            "INSERT INTO ai_agent_run_events"
            "(run_id,event_type,payload_json) "
            "VALUES ('replacement-run','tool.results',?)",
            [json.dumps({"results": [{
                "tool_name": "readWritingTechniqueContext",
                "content": json.dumps({"entries": [entry]}),
            }]})],
        )

        usage = await project_replacement_writing_technique_usage(
            db, "replacement-run"
        )

        assert usage == [{
            "ref": ref,
            "name": "信息控制",
            "source": "auto",
            "files": [{
                "ref": ref,
                "path": "SKILL.md",
                "sha256": "entry-hash",
            }],
        }]
        assert "private technique instructions" not in json.dumps(usage)

        await db.execute(
            "UPDATE writing_technique_request_inputs SET snapshot_json='{}' "
            "WHERE id='replacement-input'"
        )
        assert await project_replacement_writing_technique_usage(
            db, "replacement-run"
        ) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_non_replacement_run_is_left_for_legacy_projection(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute(
            "INSERT INTO ai_agent_runs(id,status,binding_attributes_json) "
            "VALUES ('legacy-run','done','{\"agentProfile\":\"writing\"}')"
        )
        assert await project_replacement_writing_technique_usage(
            db, "legacy-run"
        ) is None
    finally:
        await db.close()
