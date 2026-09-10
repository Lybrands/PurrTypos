"""Conversation-scoped durable analysis progress and saved-result reads."""
import json
from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from domains.novel_analysis import NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX
from exceptions import AppError


def requests_resume(text):
    return text.strip().rstrip('。！!').lower() in {'继续', '继续分析', '继续执行', '接着分析', '恢复分析', 'continue', 'resume'}


async def latest_conversation_task(db, revision, command):
    return await db.fetch_one(
        "SELECT t.* FROM ai_agent_long_tasks t JOIN ai_agent_runs r ON r.id=t.created_by_run_id "
        "WHERE t.namespace='purrtypos.novel_analysis' AND t.owner_id=? "
        "AND COALESCE((SELECT session_id FROM novel_analysis_session_commands WHERE command_id=r.binding_command_id), 'legacy:' || r.binding_aggregate_id) "
        "= COALESCE((SELECT session_id FROM novel_analysis_session_commands WHERE command_id=?), 'legacy:' || r.binding_aggregate_id) "
        "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs WHERE run_id=r.id) "
        "ORDER BY t.rowid DESC LIMIT 1", [revision, command])


async def progress_context(db, revision, command):
    task = await latest_conversation_task(db, revision, command)
    if not task: return {'status': 'no_previous_analysis'}
    return {'taskId': task['id'], 'status': task['status'], 'originalRequest': json.loads(task['metadata_json'] or '{}').get('prompt', ''), 'completedUnits': task['completed_units'],
        'totalUnits': task['total_units'], 'failedUnits': task['failed_units'],
        'savedResults': '调用 listAnalysisSavedResults 和 readAnalysisSavedResult 按需读取已保存成果；任务失败不表示成果已删除。'}


async def saved_results(db, revision, command):
    task = await latest_conversation_task(db, revision, command)
    if not task: return []
    return await db.fetch_all(
        "SELECT a.id,a.kind,a.rowid AS result_number FROM ai_agent_artifacts a JOIN ai_agent_runs r ON r.id=a.created_by_run_id "
        "WHERE r.binding_namespace='novel_source_analysis.unit' AND r.binding_aggregate_id=? "
        "AND substr(r.binding_command_id,1,?)=? "
        "AND a.kind IN ('analysis_fact_page','analysis_observation_page','novel_analysis_model_result') ORDER BY a.rowid",
        [revision, len(task['id']) + 1, task['id'] + ':'])


async def read_saved_result(db, revision, command, number, offset=0):
    rows = await saved_results(db, revision, command)
    row = next((item for item in rows if item['result_number'] == number), None)
    if row is None: raise AppError('成果编号不属于当前对话任务，请重新查询目录', 422)
    content = await NovelAnalysisArtifactStore(db).require(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + row['id'])
    text = json.dumps(content.get('result', content), ensure_ascii=False)
    if offset < 0 or offset > len(text): raise AppError('读取位置无效', 422)
    return {'resultNumber': number, 'content': text[offset:offset + 8000],
        'nextOffset': offset + 8000 if offset + 8000 < len(text) else None}
