import json
from unittest.mock import AsyncMock

import pytest

from application.novel_analysis_service import NovelAnalysisService
from application.novel_analysis_progress import latest_conversation_task
from application.novel_analysis_stream import NovelAnalysisStreamQuery
from application.novel_analysis_sessions import NovelAnalysisSessions
from database.connection import DatabaseConnection
from exceptions import AppError
from infrastructure.persistence.agent_conversation_history import analysis_history
from tests.test_novel_analysis import _source


async def seed(db, edited_kind="follow_up"):
    revision = (await _source(db))['id']
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


async def test_edit_replaces_suffix_in_ui_and_model_history_but_keeps_archive(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision, sessions, identity, other = await seed(db)
        service = NovelAnalysisService(db)
        stream = NovelAnalysisStreamQuery(db, analysis_service=service,
            output_repository=type('Output', (), {'list_bound_events': AsyncMock(return_value=[])})())
        await stream.read_page(revision)
        await db.execute("INSERT INTO ai_agent_long_tasks(id,namespace,kind,owner_id,created_by_run_id,status,total_units,completed_units,max_parallelism,metadata_json) VALUES ('old-task','purrtypos.novel_analysis','novel_source_analysis',?,'edited','failed',3,2,1,'{}')", [revision])
        assert await latest_conversation_task(db, revision, 'replacement') is not None
        async def accept(**kwargs):
            assert [m.content for m in await analysis_history(db, revision, 'replacement')] == ['first', 'answer-first']
            assert kwargs['allow_resume'] is False
            assert kwargs['artifact_ref'] is None
            assert await latest_conversation_task(db, revision, 'replacement') is None
            archived_page = await stream.read_page(revision)
            assert {r['runId'] for r in archived_page['runs']} == {'first', 'other'}
            await db.execute('INSERT INTO ai_agent_runs(id,status,prompt,final_response,binding_namespace,binding_aggregate_id,binding_command_id,binding_attributes_json) VALUES (?,?,?,?,?,?,?,?)',
                ['replacement-run', 'done', kwargs['prompt'], 'new-answer', 'novel_source_analysis', revision, 'replacement', '{"interactionKind":"follow_up"}'])
            return {'status': 'accepted'}
        service.follow_up = AsyncMock(side_effect=accept)
        await service.replace_turn(source_revision_id=revision, command_id='replacement', target_run_id='edited', prompt='changed', runtime=None)
        stream_page = await stream.read_page(revision)
        assert {r['runId'] for r in stream_page['runs']} == {'replacement-run', 'first', 'other'}
        runs = await service.list_for_revision(revision)
        assert [r['runId'] for r in runs if r['conversationId'] == identity] == ['replacement-run', 'first']
        assert [r['runId'] for r in runs if r['conversationId'] == other] == ['other']
        assert (await db.fetch_one('SELECT COUNT(*) AS n FROM ai_agent_runs'))['n'] == 5
        await sessions.bind(revision, identity, 'next')
        assert [m.content for m in await analysis_history(db, revision, 'next')] == ['first', 'answer-first', 'changed', 'new-answer']
        await service.replace_turn(source_revision_id=revision, command_id='replacement', target_run_id='edited', prompt='changed', runtime=None)
        assert service.follow_up.await_count == 1
        with pytest.raises(AppError):
            await service.replace_turn(source_revision_id=revision, command_id='next', target_run_id='edited', prompt='stale edit', runtime=None)
        # Projection is persisted, independent of an in-memory page/session.
        await db.close()
        db = DatabaseConnection(tmp_path)
        await db.init()
        assert [r['runId'] for r in await NovelAnalysisService(db).list_for_revision(revision) if r['conversationId'] == identity] == ['replacement-run', 'first']
    finally:
        await db.close()


async def test_rejection_rolls_back_and_cross_session_or_running_target_is_rejected(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision, sessions, identity, _ = await seed(db)
        service = NovelAnalysisService(db)
        service.follow_up = AsyncMock(side_effect=AppError('dispatch unavailable', 409))
        for target in ['other', 'edited']:
            with pytest.raises(AppError):
                await service.replace_turn(source_revision_id=revision, command_id='replacement', target_run_id=target, prompt='changed', runtime=None)
            assert not await db.fetch_all('SELECT * FROM novel_analysis_superseded_runs')
        assert service.follow_up.await_count == 1
        with pytest.raises(AppError):
            await service.replace_turn(source_revision_id=revision, command_id='first', target_run_id='edited', prompt='changed', runtime=None)
        assert not await db.fetch_all('SELECT * FROM novel_analysis_superseded_runs')
        await db.execute("UPDATE ai_agent_runs SET status='running' WHERE id='later'")
        with pytest.raises(AppError):
            await service.replace_turn(source_revision_id=revision, command_id='replacement', target_run_id='edited', prompt='changed', runtime=None)
        assert service.follow_up.await_count == 1
        assert len(await service.list_for_revision(revision)) == 4
    finally:
        await db.close()


async def test_editing_analysis_regenerates_analysis_not_resume_or_followup(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision, _, _, _ = await seed(db, edited_kind='analysis')
        service = NovelAnalysisService(db)
        service.start = AsyncMock(return_value={'status': 'accepted'})
        service.follow_up = AsyncMock()
        await service.replace_turn(source_revision_id=revision, command_id='replacement', target_run_id='edited', prompt='new analysis goal', runtime=None)
        service.start.assert_awaited_once_with(source_revision_id=revision, command_id='replacement', prompt='new analysis goal', runtime=None)
        service.follow_up.assert_not_awaited()
    finally:
        await db.close()


async def test_edit_http_contract_routes_replacement_instead_of_followup(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from dependencies import set_db, clear_db
    import routers.novel_sources as routes
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        revision, _, identity, _ = await seed(db)
        service = NovelAnalysisService(db)
        service.replace_turn = AsyncMock(return_value={'status': 'accepted'})
        service.follow_up = AsyncMock()
        monkeypatch.setattr(routes, '_analysis_service', lambda: service)
        app = FastAPI()
        app.include_router(routes.router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'{routes.router.prefix}/novel-source-revisions/{revision}/analysis-follow-ups',
                headers={'Idempotency-Key': 'http-edit'}, json={'conversationId': identity,
                'replaceRunId': 'edited', 'prompt': 'changed', 'runtime': {'apiKey': 'fixture', 'options': {'model': 'model', 'model_profile': 'deepseek:deepseek-v4-flash', 'profile_binding': 'compatible'}}})
        assert response.status_code == 202, response.text
        assert service.replace_turn.await_args.kwargs['target_run_id'] == 'edited'
        assert service.replace_turn.await_args.kwargs['command_id'] == 'http-edit'
        service.follow_up.assert_not_awaited()
    finally:
        clear_db(db)
        await db.close()


async def test_stream_skips_archived_outputs_while_advancing_cursor(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import application.novel_analysis_stream as stream_module
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision, sessions, _, _ = await seed(db)
        output = SimpleNamespace(list_bound_events=AsyncMock(return_value=[
            (i, SimpleNamespace(run_id=run, emitted_at=datetime.now(timezone.utc)))
            for i, run in enumerate(['first', 'edited', 'later', 'other'], 1)]))
        monkeypatch.setattr(stream_module, 'canonical_output_to_sse_chunk', lambda event: {'type': 'fixture'})
        query = NovelAnalysisStreamQuery(db, output_repository=output, analysis_service=NovelAnalysisService(db))
        assert len((await query.read_page(revision))['chunks']) == 4
        async with db.transaction():
            await sessions.replace_suffix(revision, 'replacement', 'edited')
        page = await query.read_page(revision)
        assert [chunk['runId'] for chunk in page['chunks']] == ['first', 'other']
        assert page['nextCursor'] == 4
    finally:
        await db.close()
