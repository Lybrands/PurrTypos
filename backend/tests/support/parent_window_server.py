"""Opt-in isolated acceptance server. Never mounted in the product application."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from purra.api import AgentCore, AgentPreset, AgentTreePolicy, AgentCoreRunOptions
from purra.contracts import AgentMessage, ModelStream, ModelStreamChunk, ModelFinishReason, ToolCallDelta, RuntimeLimits
from purra.output import ResponseTransactionPolicy, ResponseTransactionMode, PublicPresentationMode
from purra.tools import InMemoryToolCatalog
from application.assistant_text_facts import AssistantTextFactsProvider
from application.composition_factory import create_agent_composition
from application.model_runtime import model_request_from_runtime
from application.sse_mapping import canonical_output_to_sse_chunk
from database.connection import DatabaseConnection
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest
from tests.test_parent_result_window import request

ROOT = Path(os.environ['PURRTYPOS_ACCEPTANCE_DIR']).resolve()
assert str(ROOT).startswith('/private/tmp/')
LIVE = os.environ.get('PURRTYPOS_ACCEPTANCE_LIVE') == '1'
STAGE = ('请用中文把当前可用结果写成一个自然、简洁的普通段落，总共约150字；'
         '区分未完成任务，不使用标题、列表、Markdown 标记或“阶段进展”等栏目词。'
         '不要工具、私有推理或宣称整轮完成。')
SYNTHETIC = '请首先调用 delegateToAgents，一次派发两个子任务。fast 的 instruction 必须以 CHILD_FAST 开头，检查合成句子“小猫把红色球放进蓝色盒子”的颜色；slow 的 instruction 必须以 CHILD_SLOW 开头，检查同一句子的动作。两个 objective 均为分析合成句子。两项都完成后用中文给出最终总结。'
state = {'state':'ready','live':LIVE,'slowReleased':False,'modelStreamAttempts':0}
events = []
release_stage, release_slow = asyncio.Event(), asyncio.Event()
handle = None
core = None
started_at = time.monotonic()

class Gateway:
    def __init__(self, delegate=None): self.delegate = delegate
    async def complete(self, *args, **kwargs): raise AssertionError('Streaming required')
    async def stream(self, messages, invocation, signal=None):
        state['modelStreamAttempts'] += 1
        assert state['modelStreamAttempts'] <= 10
        texts = [str(m.content) for m in messages]
        stage = STAGE in texts
        slow = any(t.startswith('CHILD_SLOW') for t in texts)
        child = slow or any(t.startswith('CHILD_FAST') for t in texts)
        if slow: await release_slow.wait()
        if self.delegate:
            assert invocation.request.model == 'glm-5.3-flash'
            stream = await self.delegate.stream(messages, invocation, signal)
            source = stream.chunks
        else:
            async def chunks():
                if stage:
                    for text in ['首段：颜色检查已完成。', '\n\n红色球放进蓝色盒子。', '动作检查仍在进行。']:
                        yield ModelStreamChunk(content_delta=text, reasoning_delta='PRIVATE_REASONING')
                elif child:
                    yield ModelStreamChunk(content_delta='合成句子的检查已完成。')
                elif not any(m.role.value == 'tool' for m in messages) and any(t == SYNTHETIC for t in texts):
                    yield ModelStreamChunk(tool_call_deltas=(ToolCallDelta(index=0,id='delegate',type='function',name='delegateToAgents',arguments_fragment=json.dumps({'children':[
                        {'name':n,'title':n,'instruction':i,'objective':'分析合成句子'} for n,i in [('fast','CHILD_FAST'),('slow','CHILD_SLOW')]
                    ]})),), finish_reason=ModelFinishReason.TOOL_CALLS)
                    return
                else: yield ModelStreamChunk(content_delta='最终回答：颜色和动作两项检查均已完成。')
                yield ModelStreamChunk(finish_reason=ModelFinishReason.STOP)
            source = chunks()
            stream = ModelStream(chunks=source, model='test-model', applied_generation_limit=invocation.output_budget.max_generation_tokens)
        async def gated():
            first = True
            try:
                async for chunk in source:
                    yield chunk
                    if stage and first and chunk.content_delta:
                        first = False
                        state['state'] = 'stage_first_chunk'
                        await release_stage.wait()
            finally:
                close = getattr(source, 'aclose', None)
                if close: await close()
        return replace(stream, chunks=gated())

@asynccontextmanager
async def lifespan(app):
    global core
    ROOT.mkdir(parents=True,exist_ok=True)
    db = DatabaseConnection(ROOT/'database'); await db.init()
    composition = create_agent_composition(db)
    model = request().model
    delegate = None
    if LIVE:
        p=Path('/Users/liuyubin/Library/Application Support/purrtypos/purrtypos.db')
        # A normal read-only SQLite transaction sees a consistent WAL snapshot.
        # Read only the model configuration; all acceptance Runs and events stay
        # in the isolated database rooted under /private/tmp.
        with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as source:
            configs=json.loads(source.execute("SELECT value FROM settings WHERE key='ai_model_configs'").fetchone()[0])
        cfg=next(c for c in configs if c.get('name')=='glm-5.3-flash')
        assert cfg['baseUrl']=='https://open.bigmodel.cn/api/paas/v4/'
        model=model_request_from_runtime(ScreenplayAgentRuntimeRequest(apiKey=cfg['apiKey'], apiProvider='zai',baseURL=cfg['baseUrl'],contextWindow='128k',options={'model':'glm-5.3-flash','model_profile':'zai:glm-5.3-flash','max_generation_tokens':4096,'reasoning_effort':'low'}))
        delegate=ProviderModelGateway(cfg['apiKey'])
    core=AgentCore(model_gateway=Gateway(delegate),run_repository=composition._repository,
        output_repository=composition.output_repository,output_processor=composition.output_processor,
        output_publisher=composition._output_publisher,run_tree_repository=composition._run_tree_repository,
        execution_lease_store=composition._execution_lease_store,execution_owner_id=composition._repository.owner_id,
        preset=AgentPreset(id='acceptance',revision='1',tool_catalog=InMemoryToolCatalog(()),
            runtime_limits=RuntimeLimits(max_run_generation_tokens=None),
            agent_tree_policy=AgentTreePolicy(result_presentation_instruction=STAGE)))
    app.state.request=replace(request(),model=model,messages=(AgentMessage('user',SYNTHETIC),),context_window=model.capability_snapshot.context_window_tokens)
    app.state.composition=composition
    yield
    release_stage.set();release_slow.set()
    await core.close();await composition.shutdown();await db.close()
    (ROOT/'evidence.json').write_text(json.dumps({'status':state,'events':events},ensure_ascii=False,indent=2))

app=FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5175','http://localhost:5175','null'],allow_methods=['GET','POST'],allow_headers=['*'])
async def run():
    global handle
    try:
        handle=await core.submit(app.state.request, options=AgentCoreRunOptions(
            response_transaction_policy=ResponseTransactionPolicy(mode=ResponseTransactionMode.VALIDATED_RESULT,public_presentation=PublicPresentationMode.MODEL_LIVE),
            committed_result_facts_provider=AssistantTextFactsProvider()))
        state['runId']=handle.run_id
        async for event in handle.subscribe():
            wire=canonical_output_to_sse_chunk(event)
            if wire:
                events.append(wire)
                if wire['channel']=='commentary' and wire['kind']=='provider.delta_batch':
                    state.setdefault('firstChunkSinceServerStartSeconds',round(time.monotonic()-started_at,3))
                    state.setdefault('firstChunkBeforeSlow',not release_slow.is_set())
        result=await handle.wait()
        state.update(state=result.status.value,errorCode=result.error)
    except Exception as e: state.update(state='failed',errorCode=getattr(e,'code',type(e).__name__))
    (ROOT/'evidence.json').write_text(json.dumps({'status':state,'events':events},ensure_ascii=False,indent=2))
@app.post('/start')
async def start():
    if state['state']=='ready': state['state']='running';app.state.task=asyncio.create_task(run())
    return {'success':True}
@app.post('/stage')
async def stage(): release_stage.set();return {'success':True}
@app.post('/slow')
async def slow(): state['slowReleased']=True;release_slow.set();return {'success':True}
@app.post('/cancel')
async def cancel():
    if handle: await handle.cancel('user_canceled')
    return {'success':True}
@app.get('/events')
async def stream():
    async def body():
        index=0
        while True:
            for event in events[index:]: yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            index=len(events)
            yield 'data: '+json.dumps({'fixture':state},ensure_ascii=False)+'\n\n'
            await asyncio.sleep(.03)
    return StreamingResponse(body(),media_type='text/event-stream')
