import json
from unittest.mock import AsyncMock

import pytest

from agents.novel_analysis.sessions import NovelAnalysisSessions
from database.connection import DatabaseConnection
from tests.support.novel_source_fixtures import seed_novel_source


async def seed(db, edited_kind="follow_up"):
    revision = (await seed_novel_source(db))['id']
    sessions = NovelAnalysisSessions(db)
    identity = (await sessions.list(revision))[0]['id']
    other = (await sessions.create(revision))['id']
    for command, session in [('first', identity), ('edited', identity), ('later', identity), ('other', other)]:
        await sessions.bind(revision, session, command)
        await db.execute('INSERT INTO ai_agent_runs(id,status,prompt,final_response,binding_namespace,binding_aggregate_id,binding_command_id,binding_attributes_json) VALUES (?,?,?,?,?,?,?,?)',
            [command, 'done', command, 'answer-' + command, 'novel_source_analysis', revision, command,
             json.dumps({'interactionKind': edited_kind if command == 'edited' else 'follow_up'})])
    await sessions.bind(revision, identity, 'replacement')
    return revision, sessions, identity, other










async def test_edit_http_contract_never_sends_replacement_run_to_frozen_service(
    tmp_path,
    monkeypatch,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from dependencies import set_db, clear_db
    from agents.shared.implementation import (
        AgentKind,
        replacement_implementation,
    )
    from agents.shared.implementation_store import SqliteAgentImplementationStore
    import routers.novel_sources as routes

    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        revision, _, identity, _ = await seed(db)
        await SqliteAgentImplementationStore(db).bind(
            "edited",
            replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
        )
        control = type("Control", (), {})()
        control.replace_turn = AsyncMock(return_value={"status": "accepted"})
        monkeypatch.setattr(routes, "_analysis_control_service", lambda: control)
        app = FastAPI()
        app.include_router(routes.router)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"{routes.router.prefix}/novel-source-revisions/"
                f"{revision}/analysis-follow-ups",
                headers={"Idempotency-Key": "http-replacement-edit"},
                json={
                    "conversationId": identity,
                    "replaceRunId": "edited",
                    "prompt": "changed",
                    "runtime": {
                        "apiKey": "fixture",
                        "modelConfigId": "fixture-config",
                        "options": {
                            "model": "model",
                            "model_profile": "deepseek:deepseek-v4-flash",
                            "profile_binding": "compatible",
                        },
                    },
                },
            )
        assert response.status_code == 202
        control.replace_turn.assert_awaited_once()
        assert control.replace_turn.await_args.kwargs["target_run_id"] == "edited"
    finally:
        clear_db(db)
        await db.close()

async def test_follow_up_http_contract_never_sends_replacement_artifact_to_frozen_service(
    tmp_path,
    monkeypatch,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from dependencies import set_db, clear_db
    from agents.novel_analysis.attempt_artifact import (
        NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
    )
    import routers.novel_sources as routes

    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        revision, _, identity, _ = await seed(db)
        await db.execute(
            "INSERT INTO ai_agent_artifacts "
            "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
            "created_by_run_id, schema_version, expected_item_count) "
            "VALUES (?, ?, 'unit_attempt_result', ?, 'run', ?, ?, 1, 1)",
            [
                "replacement-follow-up-artifact",
                NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
                revision,
                "first",
                "first",
            ],
        )
        control = type("Control", (), {})()
        control.follow_up = AsyncMock(return_value={"status": "accepted"})
        monkeypatch.setattr(routes, "_analysis_control_service", lambda: control)
        app = FastAPI()
        app.include_router(routes.router)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"{routes.router.prefix}/novel-source-revisions/"
                f"{revision}/analysis-follow-ups",
                headers={"Idempotency-Key": "http-replacement-follow-up"},
                json={
                    "conversationId": identity,
                    "artifactId": "replacement-follow-up-artifact",
                    "prompt": "继续分析",
                    "runtime": {
                        "apiKey": "fixture",
                        "modelConfigId": "fixture-config",
                        "options": {
                            "model": "model",
                            "model_profile": "deepseek:deepseek-v4-flash",
                            "profile_binding": "compatible",
                        },
                    },
                },
            )
        assert response.status_code == 202
        control.follow_up.assert_awaited_once()
        assert control.follow_up.await_args.kwargs["artifact_id"] == (
            "replacement-follow-up-artifact"
        )
    finally:
        clear_db(db)
        await db.close()
