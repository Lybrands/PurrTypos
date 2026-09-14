"""Candidate Core -> host SQLite -> SSE envelope -> real frontend reducer/timeline."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from application.agent_delegation_policy import RESULT_PRESENTATION
from purra.api import AgentCore, AgentPreset, AgentTreePolicy
from purra.contracts import (
    AgentMessage, AgentRunRequest, DomainContext, ModelRequest, ModelStream,
    ModelStreamChunk, ModelFinishReason, ToolCallDelta, RuntimeLimits,
)
from purra.model_protocol import generic_capability_snapshot
from purra.tools import InMemoryToolCatalog
from application.composition_factory import create_agent_composition
from application.sse_mapping import canonical_output_to_sse_chunk
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository


def request():
    return AgentRunRequest(
        messages=(AgentMessage(role='user', content='SYNTHETIC_ROOT'),),
        model=ModelRequest(provider='test', model='test-model', capability_snapshot=replace(
            generic_capability_snapshot(), profile_id='test:model', max_generation_tokens=4096),
            max_generation_tokens=2048),
        domain_context=DomainContext(namespace='test'), context_window=65536, tools_enabled=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['done', 'interrupted', 'canceled'])
async def test_main_run_receives_early_results_and_owns_frontend_output(tmp_path, outcome):
    received, release_slow = asyncio.Event(), asyncio.Event()
    calls = []
    feedback_done = asyncio.Event()
    class Gateway:
        async def complete(self, *args, **kwargs):
            raise AssertionError('Use streaming')
        async def stream(self, messages, invocation, signal=None):
            async def chunks():
                texts = [m.content for m in messages]
                if any('"completedChildResults"' in str(t) for t in texts):
                    feedback_done.set()
                    yield ModelStreamChunk(content_delta='主 Agent 反馈说明', finish_reason=ModelFinishReason.STOP)
                    return
                if 'CHILD_FAST' in texts or 'CHILD_SLOW' in texts:
                    if 'CHILD_SLOW' in texts:
                        await release_slow.wait()
                    yield ModelStreamChunk(content_delta='INTERNAL_CHILD_RESULT', reasoning_delta='PRIVATE_REASONING', finish_reason=ModelFinishReason.STOP)
                    return
                receipts = [m for m in messages if m.role.value == 'tool']
                if not receipts:
                    name, args = 'delegateToAgents', {'children': [
                        {'name': n, 'title': n, 'instruction': i, 'objective': 'Analyze synthetic text'}
                        for n, i in [('fast', 'CHILD_FAST'), ('slow', 'CHILD_SLOW')]]}
                else:
                    data = json.loads(receipts[-1].content)
                    calls.append(data)
                    if data['pendingRunIds']:
                        received.set()
                        name, args = 'receiveAgentResults', {'runIds': data['runIds'], 'afterRunIds': [r['runId'] for r in data['results']]}
                    else:
                        yield ModelStreamChunk(content_delta='统一回答', finish_reason=(ModelFinishReason.LENGTH if outcome == 'interrupted' else ModelFinishReason.STOP))
                        return
                yield ModelStreamChunk(tool_call_deltas=(ToolCallDelta(index=0, id=name, type='function', name=name, arguments_fragment=json.dumps(args)),), finish_reason=ModelFinishReason.TOOL_CALLS)
            return ModelStream(chunks=chunks(), model='test-model', applied_generation_limit=invocation.output_budget.max_generation_tokens)
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    core = AgentCore(model_gateway=Gateway(), run_repository=composition._repository,
        output_repository=composition.output_repository, output_processor=composition.output_processor,
        output_publisher=composition._output_publisher, run_tree_repository=composition._run_tree_repository,
        execution_lease_store=composition._execution_lease_store,
        execution_owner_id=composition._repository.owner_id,
        preset=AgentPreset(id='host-inbox-test', revision='1', tool_catalog=InMemoryToolCatalog(()),
            runtime_limits=RuntimeLimits(max_run_generation_tokens=None), agent_tree_policy=AgentTreePolicy(result_presentation_instruction=RESULT_PRESENTATION)))
    frontend = await asyncio.create_subprocess_exec('node', '--experimental-strip-types',
        str(Path(__file__).parent/'support/parent_stage_frontend.mts'),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def project(run_id):
        rows = await composition.output_repository.list_events(run_id, after_sequence=0, limit=10000)
        wire = [item for row in rows if (item := canonical_output_to_sse_chunk(row))]
        assert 'PRIVATE_REASONING' not in json.dumps(wire)
        assert 'INTERNAL_CHILD_RESULT' not in json.dumps(wire)
        frontend.stdin.write((json.dumps(wire)+'\n').encode())
        await frontend.stdin.drain()
        line = await asyncio.wait_for(frontend.stdout.readline(), 5)
        assert line, (await frontend.stderr.read()).decode()
        return json.loads(line)
    try:
        handle = await core.submit(request())
        await asyncio.wait_for(received.wait(), 5)
        async def wait_feedback():
            while True:
                rows = await composition.output_repository.list_events(handle.run_id, after_sequence=0, limit=10000)
                if any(row.payload.get("eventType") == "parent.stage.delivery" and row.payload["data"]["state"] == "completed" for row in rows):
                    return
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait_feedback(), 5)
        assert calls[0]['pendingRunIds']
        assert not release_slow.is_set()
        assert not (await composition._repository.get(handle.run_id)).terminal
        view = await project(handle.run_id)
        assert view['stages']
        assert not view['state']['runTerminal']
        children = await composition._run_tree_repository.list_descendants(handle.run_id)
        assert len(children) == 2
        assert await SqliteRunTreeRepository(db).list_descendants(handle.run_id) == children
        if outcome == 'canceled':
            await handle.cancel('user_canceled')
        release_slow.set()
        result = await asyncio.wait_for(handle.wait(), 5)
        view = await project(handle.run_id)
        assert view['state']['runTerminal']
        assert view['stages']
        assert await project(handle.run_id) == view
        if outcome == 'done':
            assert len(view['stages']) == 2
            assert result.status.value == 'done', result.error
            assert view['state']['finalText'] == '统一回答'
            assert not calls[-1]['pendingRunIds']
        else:
            assert result.status.value != 'done'
    finally:
        release_slow.set()
        if "handle" in locals():
            await handle.cancel("test_cleanup")
            await asyncio.wait_for(handle.wait(), 5)
        await core.close()
        frontend.stdin.close()
        await asyncio.wait_for(frontend.wait(), 5)
        await composition.shutdown()
        await db.close()


@pytest.mark.asyncio
async def test_model_reuses_responsibility_and_context_through_sqlite(tmp_path):
    child_inputs, feedback = [], []
    class Gateway:
        async def complete(self, *args, **kwargs):
            raise AssertionError("stream required")
        async def stream(self, messages, invocation, signal=None):
            async def chunks():
                texts = [str(message.content) for message in messages]
                if any('"completedChildResults"' in text for text in texts):
                    feedback.append(texts)
                    yield ModelStreamChunk(content_delta="协作者已完成本次核对。", finish_reason=ModelFinishReason.STOP)
                    return
                if "Own evidence across tasks" in texts:
                    child_inputs.append(texts)
                    yield ModelStreamChunk(content_delta=f"evidence-pass-{len(child_inputs)}", finish_reason=ModelFinishReason.STOP)
                    return
                receipts = [message for message in messages if message.role.value == "tool"]
                count = len(receipts)
                if count == 0:
                    name, args = "delegateToAgents", {"children": [{"name": "evidence", "title": "Evidence",
                        "instruction": "Own evidence across tasks", "objective": "First check"}]}
                elif count in {1, 5}:
                    prior = json.loads(receipts[-1].content)
                    name, args = "receiveAgentResults", {"runIds": prior["runIds"]}
                elif count == 2:
                    name, args = "listAgents", {}
                elif count == 3:
                    identity = json.loads(receipts[-1].content)["agents"][0]
                    name, args = "getAgent", {"agentId": identity["agentId"]}
                elif count == 4:
                    prior = json.loads(receipts[-1].content)
                    identity = prior.get("agent", prior)
                    name, args = "continueAgent", {"agentId": identity["agentId"],
                        "expectedContextVersion": identity["contextVersion"], "message": "Second check"}
                else:
                    yield ModelStreamChunk(content_delta="两次核对完成。", finish_reason=ModelFinishReason.STOP)
                    return
                yield ModelStreamChunk(tool_call_deltas=(ToolCallDelta(index=0, id=f"root-call-{count}",
                    name=name, arguments_fragment=json.dumps(args)),), finish_reason=ModelFinishReason.TOOL_CALLS)
            return ModelStream(chunks=chunks(), model="test-model", applied_generation_limit=invocation.output_budget.max_generation_tokens)
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    core = AgentCore(model_gateway=Gateway(), run_repository=composition._repository,
        output_repository=composition.output_repository, output_processor=composition.output_processor,
        output_publisher=composition._output_publisher, run_tree_repository=composition._run_tree_repository,
        preset=AgentPreset(id="reuse-test", revision="1", tool_catalog=InMemoryToolCatalog(()),
            runtime_limits=RuntimeLimits(max_model_rounds=10, max_run_generation_tokens=None),
            agent_tree_policy=AgentTreePolicy(result_presentation_instruction=RESULT_PRESENTATION)))
    try:
        handle = await core.submit(request())
        result = await asyncio.wait_for(handle.wait(), 10)
        assert result.status.value == "done", result.error
        runs = await composition._run_tree_repository.list_descendants(handle.run_id)
        assert len(runs) == 2 and len({run.agent_id for run in runs}) == 1
        assert len(child_inputs) == 2 and "evidence-pass-1" in child_inputs[1]
        assert len(feedback) == 2
        restored = SqliteRunTreeRepository(db)
        node = await restored.get_agent(runs[0].agent_id)
        assert node.instruction == "Own evidence across tasks" and node.context_version == 2
    finally:
        await core.close()
        await composition.shutdown()
        await db.close()
