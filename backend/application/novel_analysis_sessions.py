"""Durable source-revision conversations, independent of individual analysis Runs."""
import uuid
from exceptions import AppError


class NovelAnalysisSessions:
    def __init__(self, db):
        self.db = db

    async def list(self, revision):
        if not await self.db.fetch_one('SELECT id FROM novel_source_revisions WHERE id=?', [revision]):
            raise AppError('来源版本不存在', 404)
        await self.db.execute('INSERT OR IGNORE INTO novel_analysis_sessions(id, revision_id, title) VALUES (?, ?, ?)',
            [f'legacy:{revision}', revision, '来源对话'])
        return await self.db.fetch_all('SELECT id,title,closed,created_at AS createdAt FROM novel_analysis_sessions WHERE revision_id=? ORDER BY created_at DESC, rowid DESC', [revision])

    async def create(self, revision):
        await self.list(revision)
        identity = uuid.uuid4().hex
        await self.db.execute('INSERT INTO novel_analysis_sessions(id,revision_id,title) VALUES (?,?,?)', [identity, revision, '新对话'])
        return {'id': identity, 'title': '新对话'}

    async def update(self, revision, identity, title=None, closed=None):
        await self.require(revision, identity)
        if title is not None:
            title = title.strip()
            if not title: raise AppError('对话名称不能为空', 422)
            await self.db.execute('UPDATE novel_analysis_sessions SET title=? WHERE id=?', [title[:200], identity])
        if closed is not None:
            await self.db.execute('UPDATE novel_analysis_sessions SET closed=? WHERE id=?', [int(closed), identity])

    async def require(self, revision, identity):
        row = await self.db.fetch_one('SELECT * FROM novel_analysis_sessions WHERE id=? AND revision_id=?', [identity, revision])
        if row is None: raise AppError('对话不属于当前来源版本', 409)
        return row

    async def bind(self, revision, identity, command):
        async with self.db.transaction():
            await self.list(revision)
            identity = identity or f'legacy:{revision}'
            row = await self.require(revision, identity)
            if row['closed']: raise AppError('请先重新打开该对话', 409)
            existing = await self.db.fetch_one('SELECT session_id FROM novel_analysis_session_commands WHERE command_id=?', [command])
            if existing and existing['session_id'] != identity: raise AppError('请求已绑定其他对话', 409)
            await self.db.execute('INSERT OR IGNORE INTO novel_analysis_session_commands(command_id,session_id,revision_id) VALUES (?,?,?)', [command, identity, revision])

    async def replace_suffix(self, revision, command, target_run_id):
        """Archive the active branch from a user turn; caller owns acceptance transaction."""
        if not self.db.current_task_owns_transaction():
            raise RuntimeError('turn replacement requires an acceptance transaction')
        session = await self.db.fetch_one(
            'SELECT session_id FROM novel_analysis_session_commands WHERE command_id=? AND revision_id=?',
            [command, revision])
        if not session:
            raise AppError('编辑请求尚未绑定对话', 409)
        target = await self.db.fetch_one(
            "SELECT r.*, r.rowid AS position FROM ai_agent_runs r "
            "WHERE r.id=? AND r.binding_namespace='novel_source_analysis' AND r.binding_aggregate_id=? "
            "AND COALESCE((SELECT session_id FROM novel_analysis_session_commands WHERE command_id=r.binding_command_id), 'legacy:' || r.binding_aggregate_id)=?",
            [target_run_id, revision, session['session_id']])
        if not target:
            raise AppError('编辑的消息不属于当前对话', 409)
        previous = await self.db.fetch_one(
            'SELECT target_run_id FROM novel_analysis_superseded_runs WHERE replacement_command_id=? LIMIT 1', [command])
        if previous:
            if previous['target_run_id'] != target_run_id:
                raise AppError('请求已用于编辑另一条消息', 409)
            return target
        if await self.db.fetch_one('SELECT id FROM ai_agent_runs WHERE binding_command_id=?', [command]):
            raise AppError('请求已用于其他执行，请重新发送编辑内容', 409)
        if await self.db.fetch_one('SELECT run_id FROM novel_analysis_superseded_runs WHERE run_id=?', [target_run_id]):
            raise AppError('这条消息已被替换，请刷新对话后重试', 409)
        rows = await self.db.fetch_all(
            "SELECT r.id,r.status FROM ai_agent_runs r WHERE r.binding_namespace='novel_source_analysis' "
            "AND r.binding_aggregate_id=? AND r.rowid>=? "
            "AND COALESCE((SELECT session_id FROM novel_analysis_session_commands WHERE command_id=r.binding_command_id), 'legacy:' || r.binding_aggregate_id)=? "
            "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs WHERE run_id=r.id)",
            [revision, target['position'], session['session_id']])
        if any(row['status'] not in {'done', 'failed', 'canceled'} for row in rows):
            raise AppError('请等待当前执行结束或终止后再编辑消息', 409)
        for row in rows:
            await self.db.execute(
                'INSERT INTO novel_analysis_superseded_runs(run_id,replacement_command_id,target_run_id) VALUES (?,?,?)',
                [row['id'], command, target_run_id])
        return target
