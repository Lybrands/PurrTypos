import pytest
from application.novel_analysis_sessions import NovelAnalysisSessions
from application.novel_analysis_service import NovelAnalysisService
from infrastructure.persistence.agent_conversation_history import analysis_history
from database.connection import DatabaseConnection
from tests.test_novel_analysis import _source
from exceptions import AppError


async def test_session_history_isolation_and_legacy_history_survive_reopen(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = (await _source(db))['id']
        sessions = NovelAnalysisSessions(db)
        legacy = (await sessions.list(revision))[0]['id']
        fresh = (await sessions.create(revision))['id']
        for command, identity in [('old', None), ('one', legacy), ('two', fresh), ('current', fresh)]:
            if identity: await sessions.bind(revision, identity, command)
            await db.execute('INSERT INTO ai_agent_runs(id,status,prompt,final_response,binding_namespace,binding_aggregate_id,binding_command_id) VALUES (?,?,?,?,?,?,?)',
                [command, 'done', command, 'answer-' + command, 'novel_source_analysis', revision, command])
        assert [m.content for m in await analysis_history(db, revision, 'current')] == ['two', 'answer-two']
        await sessions.bind(revision, legacy, 'legacy-current')
        assert [m.content for m in await analysis_history(db, revision, 'legacy-current')] == ['old', 'answer-old', 'one', 'answer-one']
        runs = await NovelAnalysisService(db).list_for_revision(revision)
        assert len(runs) == 4
        assert next(r for r in runs if r['runId'] == 'old')['conversationId'] == legacy
        await sessions.update(revision, fresh, title='追问', closed=True)
        with pytest.raises(AppError): await sessions.bind(revision, fresh, 'closed-command')
        await sessions.update(revision, fresh, closed=False)
        assert next(s for s in await sessions.list(revision) if s['id'] == fresh)['title'] == '追问'
        with pytest.raises(AppError): await sessions.require('other', fresh)
        with pytest.raises(AppError): await sessions.bind(revision, fresh, 'one')
    finally:
        await db.close()


async def test_session_routes_create_rename_close_and_reopen(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from dependencies import set_db, clear_db
    from routers.novel_sources import router
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        revision = (await _source(db))['id']
        app = FastAPI()
        app.include_router(router)
        prefix = router.prefix
        path = f'{prefix}/novel-source-revisions/{revision}/conversations'
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            listed = await client.get(path)
            assert listed.status_code == 200
            created = await client.post(path)
            assert created.status_code == 200
            identity = created.json()['data']['id']
            changed = await client.patch(f'{path}/{identity}', json={'title': '人物追问', 'closed': True})
            assert changed.status_code == 200
            await client.patch(f'{path}/{identity}', json={'closed': False})
            row = next(item for item in (await client.get(path)).json()['data'] if item['id'] == identity)
            assert row['title'] == '人物追问' and not row['closed']
    finally:
        clear_db(db)
        await db.close()
