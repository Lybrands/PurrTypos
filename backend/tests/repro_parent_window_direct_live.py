"""Known candidate compatibility blocker: tree binding overrides DIRECT_LIVE."""
import asyncio
from dataclasses import replace
import pytest
from purra.api import AgentCore, AgentPreset, AgentTreePolicy, InMemoryAgentAdapters, AgentCoreRunOptions
from purra.contracts import ModelStream, ModelStreamChunk, ModelFinishReason, RuntimeLimits
from purra.output import ResponseTransactionPolicy, ResponseTransactionMode
from purra.tools import InMemoryToolCatalog
from tests.test_parent_result_window import request
from application.public_commentary_output import PublicCommentaryOutputProcessor

@pytest.mark.asyncio
@pytest.mark.parametrize('tree_enabled', [False, True])
async def test_explicit_direct_live_delivers_before_model_end(tree_enabled):
    first, release = asyncio.Event(), asyncio.Event()
    class Gateway:
        async def complete(self, *args, **kwargs):
            raise AssertionError('Only streaming is allowed')
        async def stream(self, messages, invocation, signal=None):
            async def chunks():
                yield ModelStreamChunk(content_delta='First')
                first.set()
                await release.wait()
                yield ModelStreamChunk(content_delta='Final', finish_reason=ModelFinishReason.STOP)
            return ModelStream(chunks=chunks(), model='test-model', applied_generation_limit=invocation.output_budget.max_generation_tokens)
    adapters = InMemoryAgentAdapters()
    core = AgentCore(model_gateway=Gateway(),run_repository=adapters.runs,
        output_repository=adapters.outputs,output_publisher=adapters.publisher,
        output_processor=PublicCommentaryOutputProcessor(adapters.outputs,adapters.publisher),
        run_tree_repository=adapters.run_tree if tree_enabled else None,
        preset=AgentPreset(id='direct-live-repro',revision='1',tool_catalog=InMemoryToolCatalog(()),runtime_limits=RuntimeLimits(max_run_generation_tokens=None),
            agent_tree_policy=AgentTreePolicy(result_presentation_instruction="Summarize the received collaborator result.") if tree_enabled else None))
    try:
        handle=await core.submit(replace(request(),tools_enabled=False),options=AgentCoreRunOptions(
            response_transaction_policy=ResponseTransactionPolicy(mode=ResponseTransactionMode.DIRECT_LIVE)))
        await asyncio.wait_for(first.wait(),5)
        rows=await adapters.outputs.list_events(handle.run_id,after_sequence=0)
        text=[e for e in rows if e.visibility.value=='public' and e.channel.value=='final' and e.kind.value in ('provider.delta_batch','provider.content_delta')]
        assert text, 'DIRECT_LIVE emitted no public chunk before model end'
    finally:
        release.set()
        if 'handle' in locals(): await asyncio.wait_for(handle.wait(),5)
        await core.close()
