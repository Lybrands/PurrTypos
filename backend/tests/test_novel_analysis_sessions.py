import pytest
from agents.novel_analysis.sessions import NovelAnalysisSessions
from infrastructure.persistence.agent_conversation_history import analysis_history
from database.connection import DatabaseConnection
from tests.support.novel_source_fixtures import seed_novel_source
from exceptions import AppError


async def test_session_history_isolated_by_explicit_conversation(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = (await seed_novel_source(db))['id']
        sessions = NovelAnalysisSessions(db)
        first = (await sessions.create(revision))['id']
        second = (await sessions.create(revision))['id']
        for command, identity in [('one', first), ('two', second), ('current', second)]:
            await sessions.bind(revision, identity, command)
            await db.execute('INSERT INTO ai_agent_runs(id,status,prompt,final_response,binding_namespace,binding_aggregate_id,binding_command_id) VALUES (?,?,?,?,?,?,?)',
                [command, 'done', command, 'answer-' + command, 'purrtypos.novel_analysis', revision, command])
        assert [m.content for m in await analysis_history(db, revision, 'current')] == ['two', 'answer-two']
        assert [m.content for m in await analysis_history(db, revision, 'one')] == []
        with pytest.raises(AppError, match='请选择'):
            await sessions.bind(revision, None, 'unbound')
        await sessions.update(revision, second, title='追问', closed=True)
        with pytest.raises(AppError): await sessions.bind(revision, second, 'closed-command')
        await sessions.update(revision, second, closed=False)
        assert next(s for s in await sessions.list(revision) if s['id'] == second)['title'] == '追问'
        with pytest.raises(AppError): await sessions.require('other', second)
        with pytest.raises(AppError): await sessions.bind(revision, second, 'one')
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
        revision = (await seed_novel_source(db))['id']
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
            deleted = await client.delete(f'{path}/{identity}')
            assert deleted.status_code == 200
            assert all(item['id'] != identity for item in (await client.get(path)).json()['data'])
    finally:
        clear_db(db)
        await db.close()


async def test_session_delete_rejects_active_work_and_keeps_terminal_run_audit(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = (await seed_novel_source(db))['id']
        sessions = NovelAnalysisSessions(db)
        identity = (await sessions.create(revision))['id']
        await sessions.bind(revision, identity, 'command')
        await db.execute(
            'INSERT INTO ai_agent_runs('
            'id,status,prompt,binding_namespace,binding_aggregate_id,binding_command_id'
            ') VALUES (?,?,?,?,?,?)',
            ['run', 'running', '分析', 'purrtypos.novel_analysis', revision, 'command'],
        )

        with pytest.raises(AppError, match='不能删除'):
            await sessions.delete(revision, identity)

        await db.execute("UPDATE ai_agent_runs SET status = 'done' WHERE id = 'run'")
        await sessions.delete(revision, identity)
        assert await db.fetch_one(
            'SELECT id FROM novel_analysis_sessions WHERE id = ?', [identity]
        ) is None
        assert await db.fetch_one(
            'SELECT command_id FROM novel_analysis_session_commands '
            'WHERE command_id = ?', ['command']
        ) is None
        assert await db.fetch_one(
            'SELECT id FROM ai_agent_runs WHERE id = ?', ['run']
        ) is not None
    finally:
        await db.close()
