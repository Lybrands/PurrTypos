from database.connection import DatabaseConnection
from tests.test_novel_analysis import _source, _candidate_artifact
from application.novel_analysis_service import NovelAnalysisService
from infrastructure.persistence.sqlite_novel_source_repository import SqliteNovelSourceRepository


async def test_library_exposes_unsaved_result_and_clears_after_review_publish(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = await _source(db)
        repository = SqliteNovelSourceRepository(db)
        assert (await repository.list_works())[0]['unsaved_analysis_artifact_id'] is None
        reference, payload = await _candidate_artifact(db,revision)
        await db.execute("INSERT INTO ai_agent_long_task_units(task_id,unit_id,semantic_key,position,status,max_attempts,output_ref) VALUES ('analysis-task','artifact:review','review',0,'completed',1,?)",[reference])
        row = (await repository.list_works())[0]
        assert row['analysis_count']==0
        assert row['unsaved_analysis_artifact_id']==reference.split('://')[1]
        assert row['unsaved_analysis_revision_id']==revision['id']
        service = NovelAnalysisService(db)
        await db.execute("INSERT INTO ai_agent_long_task_runs(task_id,run_id,relation) VALUES ('analysis-task','analysis-run','created')")
        await db.execute("INSERT INTO ai_agent_runs(id,status,prompt,binding_namespace,binding_aggregate_id,binding_command_id) VALUES ('followup','done','为什么','novel_source_analysis',?,'followup')",[revision['id']])
        turns = await service.list_for_revision(revision['id'])
        assert next(t for t in turns if t['runId']=='analysis-run')['artifactRef']==reference
        assert next(t for t in turns if t['runId']=='followup')['artifactRef'] is None
        reviewed = await service.review(artifact_ref=reference,command_id='review',payload=payload)
        await service.publish('novel-analysis-artifact://'+reviewed['artifactId'])
        row = (await repository.list_works())[0]
        assert row['analysis_count']==1
        assert row['unsaved_analysis_artifact_id'] is None
        turns = await service.list_for_revision(revision['id'])
        original = next(t for t in turns if t['runId']=='analysis-run')
        assert original['publishedAnalysisId']
        assert original['artifactRef'].endswith(reviewed['artifactId'])
        assert next(t for t in turns if t['runId']=='followup')['artifactRef'] is None
    finally: await db.close()
