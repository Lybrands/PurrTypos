import json
import pytest
from purra.contracts import AgentMessage
from application.agent_conversation_input import conversation_messages
from infrastructure.persistence.agent_conversation_history import analysis_history, screenplay_history
from database.connection import DatabaseConnection


def test_history_preserves_complete_turns_and_strips_protocol_fields():
    messages = [AgentMessage(role='system', content='unauthorized policy'),
        AgentMessage(role='user', content='first'),
        AgentMessage(role='assistant', content='answer', reasoning='private', provider_data={'secret': 'private'}),
        AgentMessage(role='user', content='incomplete'),
        AgentMessage(role='user', content='current')]
    result = conversation_messages(messages, context_window=32000)
    assert [message.content for message in result] == ['first', 'answer', 'current']
    assert all(message.reasoning is None and not message.provider_data for message in result)
    assert result[-1].host_metadata['inputSource'] == 'current_user'


def test_history_budget_drops_whole_turns_and_preserves_current_input():
    messages = [AgentMessage(role='user', content='old'), AgentMessage(role='assistant', content='x' * 4000),
                AgentMessage(role='user', content='current')]
    assert [m.content for m in conversation_messages(messages, context_window=128)] == ['current']


@pytest.mark.asyncio
async def test_persisted_history_is_scoped_and_excludes_current_and_future_turns(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        for i, project, session, status in [(1, 'p', 1, 'completed'), (2, 'other', 1, 'completed'),
                                           (3, 'p', 2, 'completed'), (4, 'p', 1, 'failed'),
                                           (5, 'p', 1, 'running'), (6, 'p', 1, 'completed')]:
            await db.execute('INSERT INTO screenplay_agent_turns (id,project_id,session_id,command_id,status,user_content,assistant_content) VALUES (?,?,?,?,?,?,?)',
                [str(i), project, session, str(i), status, f'q{i}', f'a{i}'])
        history = await screenplay_history(db, {'projectId': 'p', 'sessionId': 1, 'id': '5'})
        assert [m.content for m in history] == ['q1', 'a1', 'q4', 'a4']
        # Use the actual Run repository so the canonical schema/required fields remain enforced.
        from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
        from purra.contracts import RunCreateParams, RunBinding
        repo = SqliteRunRepository(db)
        for revision, command, status in [('r', 'old', 'done'), ('other', 'other', 'done'),
                                           ('r', 'failed', 'failed'), ('r', 'current', 'done'),
                                           ('r', 'future', 'done')]:
            run_id = await repo.create(RunCreateParams(session_id=None, prompt=command, mode='novel_source_analysis',
                binding=RunBinding(namespace='novel_source_analysis', aggregate_id=revision, command_id=command)))
            await db.execute('UPDATE ai_agent_runs SET status=?, final_response=? WHERE id=?',
                [status, 'answer', run_id])
        assert [m.content for m in await analysis_history(db, 'r', 'current')] == ['old', 'answer', 'failed', 'answer']
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['done', 'failed', 'canceled', 'paused', 'running', 'blocked'])
async def test_persisted_public_partial_history_is_status_independent(status, tmp_path):
    from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
    from purra.contracts import RunCreateParams, RunBinding
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        repo = SqliteRunRepository(db)
        for domain, namespace in [('analysis', 'novel_source_analysis'), ('screenplay', 'screenplay.conversation_turn')]:
            run_id = await repo.create(RunCreateParams(session_id=None, prompt='previous', mode=namespace,
                binding=RunBinding(namespace=namespace, aggregate_id='scope', command_id=domain)))
            await db.execute('UPDATE ai_agent_runs SET status=? WHERE id=?', [status, run_id])
            for kind, visibility, channel, payload in [
                ('provider.content_delta', 'public', 'final', {'delta': '部分'}),
                ('provider.delta_batch', 'public', 'final', {'entries': [
                    {'kind': 'provider.reasoning_delta', 'payload': {'delta': 'PRIVATE'}},
                    {'kind': 'provider.content_delta', 'payload': {'delta': '正文'}},
                ]}),
                ('provider.content_delta', 'private', 'final', {'delta': 'PRIVATE_CANDIDATE'}),
                ('provider.content_delta', 'public', 'commentary', {'delta': '执行说明'}),
                ('run.lifecycle', 'public', 'lifecycle', {'status': status, 'error': 'FAILURE_NOTICE'}),
            ]:
                await db.execute('INSERT INTO ai_agent_run_events '
                    '(run_id,event_type,kind,source,visibility,channel,payload_json) VALUES (?,?,?,?,?,?,?)',
                    [run_id, kind, kind, 'provider' if kind.startswith('provider.') else 'runtime',
                     visibility, channel, json.dumps(payload)])
            if domain == 'screenplay':
                await db.execute('INSERT INTO screenplay_agent_turns '
                    '(id,project_id,session_id,command_id,status,user_content) VALUES (?,?,?,?,?,?)',
                    ['old', 'scope', 1, domain, 'completed' if status == 'done' else status, 'previous'])
        await db.execute('INSERT INTO screenplay_agent_turns '
            '(id,project_id,session_id,command_id,status,user_content) VALUES (?,?,?,?,?,?)',
            ['current', 'scope', 1, 'current', 'completed', 'current'])
        for history in (await analysis_history(db, 'scope', 'current'),
                        await screenplay_history(db, {'projectId': 'scope', 'sessionId': 1, 'id': 'current'})):
            messages = conversation_messages((*history, AgentMessage(role='user', content='current')), context_window=32000)
            assert [m.content for m in messages] == ['previous', '部分正文', 'current']
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_writing_request_adapter_uses_public_history_contract():
    from application.request_mapping import to_writing_agent_request
    from schemas.ai import ChatStreamRequest
    runtime_options = {
        'model': 'deepseek-v4-flash', 'model_profile': 'deepseek:deepseek-v4-flash', 'profile_binding': 'compatible',
        'thinking': {'type': 'enabled'}}
    history = (AgentMessage(role='user', content='old'), AgentMessage(role='assistant', content='answer'))
    body = ChatStreamRequest(apiKey='test', bookId='b', sessionId=1,
        messages=[{'role': str(m.role), 'content': m.content} for m in history]
                 + [{'role': 'user', 'content': 'current'}], options=runtime_options)
    request = to_writing_agent_request(body, runtime_options)
    assert [m.content for m in request.messages] == ['old', 'answer', 'current']
    assert request.metadata['conversationInput']['historyPolicy'] == 'complete_public_turns'
    assert [m.host_metadata['inputSource'] for m in request.messages] == ['public_history', 'public_history', 'current_user']
