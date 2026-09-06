"""Read prior public turns inside the product's durable conversation boundary."""
import json
from purra.contracts import AgentMessage, MessageRole


async def _messages(db, rows):
    missing = [row['run_id'] for row in rows if row.get('run_id') and not str(row['response'] or '').strip()]
    public = {}
    if missing:
        marks = ','.join('?' for _ in missing)
        events = await db.fetch_all(
            "SELECT run_id, kind, payload_json FROM ai_agent_run_events "
            f"WHERE run_id IN ({marks}) AND visibility='public' "
            "AND source='provider' AND channel='final' "
            "AND kind IN ('provider.content_delta','provider.delta_batch') "
            "ORDER BY id", missing)
        for event in events:
            payload = json.loads(event['payload_json'])
            entries = (payload.get('entries') or []) if event['kind'] == 'provider.delta_batch' else [
                {'kind': event['kind'], 'payload': payload}]
            for entry in entries:
                if entry.get('kind') == 'provider.content_delta':
                    delta = (entry.get('payload') or {}).get('delta')
                    if isinstance(delta, str):
                        public.setdefault(event['run_id'], []).append(delta)
    messages = []
    for row in reversed(rows):
        response = (str(row['response'] or '').strip()
                    or ''.join(public.get(row.get('run_id'), ())).strip())
        if response:
            messages.extend((AgentMessage(role=MessageRole.USER, content=str(row['prompt'])),
                             AgentMessage(role=MessageRole.ASSISTANT, content=response)))
    return tuple(messages)


async def analysis_history(db, revision_id, command_id):
    rows = await db.fetch_all(
        "SELECT id AS run_id, prompt, final_response AS response FROM ai_agent_runs "
        "WHERE binding_namespace='novel_source_analysis' AND binding_aggregate_id=? "
        "AND binding_command_id != ? "
        "AND rowid < COALESCE((SELECT MIN(rowid) FROM ai_agent_runs "
        "WHERE binding_namespace='novel_source_analysis' AND binding_aggregate_id=? "
        "AND binding_command_id=?), 9223372036854775807) "
        "ORDER BY rowid DESC LIMIT 32", [revision_id, command_id, revision_id, command_id])
    return await _messages(db, rows)


async def screenplay_history(db, turn):
    rows = await db.fetch_all(
        "SELECT r.id AS run_id, t.user_content AS prompt, t.assistant_content AS response "
        "FROM screenplay_agent_turns t LEFT JOIN ai_agent_runs r "
        "ON r.binding_namespace='screenplay.conversation_turn' "
        "AND r.binding_aggregate_id=t.project_id AND r.binding_command_id=t.command_id "
        "WHERE t.project_id=? AND t.session_id=? "
        "AND t.rowid < (SELECT rowid FROM screenplay_agent_turns WHERE id=?) "
        "ORDER BY t.rowid DESC LIMIT 32", [turn['projectId'], turn['sessionId'], turn['id']])
    return await _messages(db, rows)
