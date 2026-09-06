from datetime import datetime, timezone
from purra.ports import RunCommit
from purra.events import AgentEvent, CoreEventType
from purra.contracts import RunStatus
from purra.output import RunLifecycleOutputDraft
import json
import pytest
from purra.contracts import RunCreateParams, ModelStreamChunk, ModelFinishReason, ToolCallDelta
from purra.model_protocol import InvocationOutputBudget
from purra.model_invocation import ModelInvocationReceipt
from purra.output import AgentOutputIntent, OutputCommitMode, OutputStreamSpec
from application.composition_factory import create_agent_composition
from application.public_commentary_output import PublicCommentaryParser
from application.sse_mapping import canonical_output_to_sse_chunk
from database.connection import DatabaseConnection
from tests.support.canonical_wire import provider_text


def test_public_frame_preserves_paragraphs_and_split_markers():
    parser = PublicCommentaryParser()
    pieces = ['【公', '开说明】第一段\n', '\n- 第二段', '【说明', '结束】PRIVATE']
    assert ''.join(parser.feed(piece) for piece in pieces) == '第一段\n\n- 第二段'
    assert parser.feed('PRIVATE') == ''
    assert PublicCommentaryParser().feed('普通未标记的候选') == ''
    assert PublicCommentaryParser().feed('【公开说明】【说明结束】PRIVATE') == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['novel_analysis_unit', 'agent'])
@pytest.mark.parametrize('abort', [False, True, 'terminal'])
async def test_public_chunks_reach_journal_and_sse_before_model_finishes(tmp_path, mode, abort):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    try:
        run_id = await composition._repository.create(RunCreateParams(session_id=None, prompt='test', mode=mode))
        spec = OutputStreamSpec(output_stream_id='public-test', invocation_id='invoke-test',
            run_id=run_id, turn_id=None, intent=AgentOutputIntent.STRUCTURED_PRIVATE,
            commit_mode=OutputCommitMode.PRIVATE)
        receipt = ModelInvocationReceipt(invocation_id=spec.invocation_id,
            output_stream_id=spec.output_stream_id, run_id=run_id, turn_id=None,
            model='test', output_intent=spec.intent, commit_mode=spec.commit_mode,
            output_budget=InvocationOutputBudget(max_generation_tokens=2000, generation_source='model_profile', profile_max_generation_tokens=2000),
            input_fingerprint='input', tool_schema_fingerprint='tools')
        processor = composition.output_processor
        await processor.open_model_stream(receipt, spec)
        assert not await composition.output_repository.has_public_progress(run_id)
        await processor.accept_provider_chunk(spec.output_stream_id, ModelStreamChunk(content_delta='【公开说明】第一段', reasoning_delta='PRIVATE_REASONING'))
        assert await composition.output_repository.has_public_progress(run_id)
        assert not await composition.output_repository.has_public_progress('another-run')
        # The provider has not emitted its second chunk, tool call or terminal marker.
        rows = await composition.output_repository.list_events(run_id, after_sequence=0, limit=500)
        public = [canonical_output_to_sse_chunk(event) for event in rows]
        public = [item for item in public if item and item['channel'] == 'commentary']
        assert [item['payload']['delta'] for item in public] == ['第一段']
        assert (await db.fetch_one('SELECT status FROM ai_agent_output_streams WHERE id=?', [spec.output_stream_id]))['status'] == 'open'
        second = '\n\n- ' + '具体公开说明' * 80
        await processor.accept_provider_chunk(spec.output_stream_id, ModelStreamChunk(content_delta=second+'【说明结束】PRIVATE_RESULT'))
        if abort == 'terminal':
            await processor.accept_run_lifecycle_event(
                RunCommit(terminal_status=RunStatus.CANCELED, events=(AgentEvent(
                    type=CoreEventType.RUN_CANCELED, run_id=run_id, payload={'status': 'canceled'}),)),
                RunLifecycleOutputDraft(source_event_key=f'run:{run_id}:canceled', status=RunStatus.CANCELED,
                    payload={'status': 'canceled'}, occurred_at=datetime.now(timezone.utc)))
        elif abort:
            await processor.abort_model_stream(spec.output_stream_id, 'interrupted')
        else:
            await processor.accept_provider_chunk(spec.output_stream_id, ModelStreamChunk(
                tool_call_deltas=(ToolCallDelta(index=0, id='tool-1', name='save', arguments_fragment='PRIVATE_ARGS'),),
                finish_reason=ModelFinishReason.TOOL_CALLS))
            await processor.finish_model_stream(spec.output_stream_id, ModelFinishReason.TOOL_CALLS)
            assert await processor.publish_model_stream_commentary(spec.output_stream_id) == ()
        rows = await composition.output_repository.list_events(run_id, after_sequence=0, limit=500)
        public = [item for event in rows if (item := canonical_output_to_sse_chunk(event))]
        text = ''.join(item['payload'].get('delta', '') for item in public)
        assert text == '第一段' + second
        assert 'PRIVATE' not in json.dumps(public)
        assert any(item['kind'] == ('stream.aborted' if abort else 'stream.committed') and item['channel'] == 'commentary' for item in public)
        assert not processor._commentary_streams
        assert await composition.output_repository.has_public_progress(run_id) is (not abort)
    finally:
        await composition.shutdown()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', [AgentOutputIntent.FINAL_PUBLIC, AgentOutputIntent.EXECUTION_PUBLIC])
async def test_live_public_output_flushes_each_provider_chunk(tmp_path, intent):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    try:
        run_id = await composition._repository.create(RunCreateParams(session_id=None, prompt='test', mode='agent'))
        spec = OutputStreamSpec(output_stream_id='live-test', invocation_id='live-invoke',
            run_id=run_id, turn_id=None, intent=intent, commit_mode=OutputCommitMode.LIVE)
        receipt = ModelInvocationReceipt(invocation_id=spec.invocation_id,
            output_stream_id=spec.output_stream_id, run_id=run_id, turn_id=None,
            model='test', output_intent=spec.intent, commit_mode=spec.commit_mode,
            output_budget=InvocationOutputBudget(max_generation_tokens=2000, generation_source='model_profile', profile_max_generation_tokens=2000),
            input_fingerprint='input', tool_schema_fingerprint='tools')
        processor = composition.output_processor
        await processor.open_model_stream(receipt, spec)
        for piece, expected in [('第一块', '第一块'), ('第二块', '第一块第二块')]:
            await processor.accept_provider_chunk(spec.output_stream_id,
                ModelStreamChunk(content_delta=piece, reasoning_delta='PRIVATE'))
            rows = await composition.output_repository.list_events(run_id, after_sequence=0, limit=500)
            public = [item for row in rows if (item := canonical_output_to_sse_chunk(row))]
            assert provider_text(public) + provider_text(public, channel='commentary') == expected
            assert 'PRIVATE' not in json.dumps(public)
            assert (await db.fetch_one('SELECT status FROM ai_agent_output_streams WHERE id=?', [spec.output_stream_id]))['status'] == 'open'
        await processor.finish_model_stream(spec.output_stream_id, ModelFinishReason.STOP)
    finally:
        await db.close()
