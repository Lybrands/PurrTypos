from unittest.mock import AsyncMock, Mock
import pytest
from database.connection import DatabaseConnection
from tests.test_novel_analysis import _source
from application.novel_analysis_sessions import NovelAnalysisSessions
from application.novel_analysis_progress import latest_conversation_task, progress_context, saved_results, read_saved_result, requests_resume
from application.novel_analysis_service import NovelAnalysisService
from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from infrastructure.persistence.agent_conversation_history import analysis_history
from exceptions import AppError


@pytest.mark.parametrize('status', ['failed', 'paused'])
async def test_continue_routes_existing_task_and_saved_results_are_session_scoped(tmp_path, status):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = (await _source(db))['id']
        sessions = NovelAnalysisSessions(db)
        identity = (await sessions.list(revision))[0]['id']
        other = (await sessions.create(revision))['id']
        for command, session in [('root', identity), ('continue', identity), ('other', other)]:
            await sessions.bind(revision, session, command)
        await db.execute("INSERT INTO ai_agent_runs(id,status,prompt,final_response,error,binding_namespace,binding_aggregate_id,binding_command_id) VALUES ('root','failed','分析人物','invalid_reference','invalid_reference','novel_source_analysis',?,'root')", [revision])
        await db.execute("INSERT INTO ai_agent_long_tasks(id,namespace,kind,owner_id,created_by_run_id,status,total_units,completed_units,max_parallelism,metadata_json) VALUES ('task','purrtypos.novel_analysis','novel_source_analysis',?,'root',?,3,2,1,?)", [revision,status,'{"prompt":"分析人物"}'])
        await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_command_id) VALUES ('unit','done','novel_source_analysis.unit',?,'task:unit:1')", [revision])
        await NovelAnalysisArtifactStore(db).write(namespace='test',kind='novel_analysis_model_result',owner_id=revision,owner_ref_kind='unit',owner_ref_id='unit',run_id='unit',semantic_key='result',payload={'result': {'facts': ['已完成的人物事实']}})
        assert (await progress_context(db,revision,'continue'))['completedUnits'] == 2
        assert await latest_conversation_task(db,revision,'other') is None
        rows = await saved_results(db,revision,'continue')
        assert len(rows) == 1
        result = await read_saved_result(db,revision,'continue',rows[0]['result_number'])
        assert '已完成的人物事实' in result['content']
        with pytest.raises(AppError): await read_saved_result(db,revision,'other',rows[0]['result_number'])
        assert not await analysis_history(db,revision,'continue')
        service = NovelAnalysisService(db)
        service._dispatch_follow_up = Mock(return_value=Mock(done=lambda: False))
        service.resume = AsyncMock()
        await service.follow_up(source_revision_id=revision,artifact_ref=None,prompt='解释已完成的人物分析',command_id='continue',runtime=None)
        service.resume.assert_not_awaited()
        service._dispatch_follow_up.assert_called_once()
        service.resume = AsyncMock(return_value={'taskId':'task'})
        response = await service.follow_up(source_revision_id=revision,artifact_ref=None,prompt='继续',command_id='continue',runtime=None)
        assert response['interactionKind'] == 'resume'
        service.resume.assert_awaited_once_with(task_id='task',run_command_id='continue',runtime=None,retry_failed=status == 'failed',user_prompt='继续')
    finally:
        await db.close()


def test_resume_intent_is_explicit():
    assert requests_resume('继续。')
    assert not requests_resume('继续分析之前，解释一下失败原因')
    assert not requests_resume('这个人物为什么这样做？')


async def test_resume_turn_keeps_previous_execution_separate(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = (await _source(db))['id']
        for identity, namespace, prompt in [('old','novel_source_analysis','分析人物'),('old-unit','novel_source_analysis.unit',''),('new','novel_source_analysis','继续'),('new-unit','novel_source_analysis.unit','')]:
            await db.execute('INSERT INTO ai_agent_runs(id,status,prompt,binding_namespace,binding_aggregate_id,binding_command_id) VALUES (?,?,?,?,?,?)', [identity,'failed' if identity.startswith('old') else 'running',prompt,namespace,revision,identity])
        await db.execute("INSERT INTO ai_agent_long_tasks(id,namespace,kind,owner_id,created_by_run_id,status,total_units,completed_units,max_parallelism,metadata_json) VALUES ('task','purrtypos.novel_analysis','novel_source_analysis',?,'old','running',2,1,1,'{}')",[revision])
        for identity, relation in [('old','created'),('new','resumed')]:
            await db.execute('INSERT INTO ai_agent_long_task_runs(task_id,run_id,relation) VALUES (?,?,?)',['task',identity,relation])
        await db.execute("INSERT INTO ai_agent_long_task_units(task_id,unit_id,semantic_key,position,status,max_attempts,run_id,metadata_json) VALUES ('task','unit','unit',0,'running',3,'new-unit',?)", ['{"runHistory":[{"runId":"old-unit"}]}'])
        runs = await NovelAnalysisService(db).list_for_revision(revision)
        assert len(runs) == 2
        old, new = next(r for r in runs if r['runId']=='old'), next(r for r in runs if r['runId']=='new')
        assert old['prompt'] == '分析人物' and new['prompt'] == '继续'
        assert old['runStatus'] == 'failed' and new['taskStatus'] == 'running'
        assert [r['runId'] for r in old['relatedRuns']] == ['old-unit']
        assert [r['runId'] for r in new['relatedRuns']] == ['new-unit']
        assert old['units'] == []
    finally:
        await db.close()
