const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')
const { buildNovelAnalysisMessages, hydrateNovelAnalysisHistory, NovelAnalysisConversationStream, novelAnalysisArtifactId } = loadTypeScriptModule(
  path.join(__dirname, 'analysisConversation.ts'),
)

test('artifact ids accept only the current analysis reference', () => {
  assert.equal(novelAnalysisArtifactId('novel-analysis-artifact://retired-id'), '')
  assert.equal(novelAnalysisArtifactId('novel-analysis://artifact-id'), 'artifact-id')
  assert.equal(novelAnalysisArtifactId('plain-id'), 'plain-id')
  assert.equal(novelAnalysisArtifactId('unknown://artifact'), '')
})

const run = (overrides = {}) => {
  const value = {
    runId: 'analysis-run', commandId: 'analysis-command', runStatus: 'running',
    conversationStatus: 'streaming', workflowStatus: 'running',
    workflowPauseKind: null, workflowReasonCode: null, workflowResumable: false,
    taskId: 'analysis-task', taskStatus: 'running', taskRevision: 1,
    prompt: '只分析人物的认知差异', totalUnits: 6, completedUnits: 0, failedUnits: 0,
    units: [{
      unitId: 'extract:1', title: '分析来源片段 1', kind: 'extract_section',
      status: 'running', attempt: 1, maxAttempts: 2,
      summary: '分析来源片段 1完成，识别 20 条事实和 7 个写作技法。',
      highlights: ['旧版合成摘要'],
    }],
    ...overrides,
  }
  if (!Object.hasOwn(overrides, 'workflowStatus')) {
    value.workflowStatus = value.taskStatus === 'pending' ? 'queued' : value.taskStatus
  }
  if (!Object.hasOwn(overrides, 'conversationStatus')) {
    value.conversationStatus = ['pending', 'running', 'claimed'].includes(value.runStatus)
      ? 'streaming'
      : 'finalized'
  }
  return value
}

const event = (sequence, overrides = {}) => ({
  cursor: sequence,
  type: overrides.kind || 'provider.content_delta',
  chunk: {
    eventId: `event-${sequence}`, runId: 'analysis-run', sequence,
    outputStreamId: 'public-answer', turnId: null, invocationId: 'model-call',
    source: 'provider', kind: 'provider.content_delta', channel: 'final',
    visibility: 'public', payload: { delta: '模型的真实公开回答。' },
    occurredAt: '2026-08-31T00:00:00Z', emittedAt: '2026-08-31T00:00:00Z',
    ...overrides,
  },
})

const replayAnalysisEvents = (run, events) => new NovelAnalysisConversationStream().apply({
  kind: 'analysis_events', nextCursor: events.length, hasMore: false, projectionVersion: 'v1',
  runs: [run], chunks: events.map((event, index) => ({
    cursor: index + 1, runId: event.chunk.runId, createdAt: '', chunk: event.chunk,
  })),
}).message

const snapshot = (runId, overrides = {}) => {
  const { run: runOverrides = {}, ...rest } = overrides
  return {
    version: 2,
    run: {
      runId,
      status: 'done',
      finalResponse: '',
      execution: { attempt: 1, cancellationRequested: false },
      provenance: { modelName: 'model' },
      ...runOverrides,
    },
    events: [],
    nextCursor: 1,
    hasMore: false,
    ...rest,
  }
}

test('history hydration reads every root and related Run page before exposing its replay', async () => {
  const calls = []
  const root = run({
    runStatus: 'done',
    finalResponse: '完整恢复的公开答复',
    relatedRuns: [{ runId: 'analysis-unit', status: 'done', role: 'child' }],
  })
  const hydrated = await hydrateNovelAnalysisHistory({
    run: root,
    model: { id: 'model', name: 'model', apiKey: '', baseUrl: '' },
    isCurrent: () => true,
    getRunSnapshot: async ({ runId, after }) => {
      calls.push(`${runId}:${after ?? 0}`)
      if (runId === root.runId && after == null) {
        return { success: true, data: snapshot(runId, {
          nextCursor: 1, hasMore: true,
          run: { finalResponse: root.finalResponse },
        }) }
      }
      if (runId === root.runId) {
        return { success: true, data: snapshot(runId, {
          nextCursor: 2,
          run: { finalResponse: root.finalResponse },
          events: [event(2, { payload: { delta: '不应覆盖最终答复' } })],
        }) }
      }
      return { success: true, data: snapshot(runId) }
    },
  })
  assert.deepEqual(calls, ['analysis-run:0', 'analysis-unit:0', 'analysis-run:1'])
  assert.equal(hydrated.runId, root.runId)
  assert.equal(hydrated.message.content, '完整恢复的公开答复')
})

test('completed resume combines restored history with a persisted continuation reply', async () => {
  const root = run({
    runId: 'continued-root', runStatus: 'done', finalResponse: '这是重试后的回复。',
    relatedRuns: [{ runId: 'failed-root', status: 'failed', role: 'previous_root' }],
  })
  const hydrated = await hydrateNovelAnalysisHistory({
    run: root,
    model: { id: 'model', name: 'model', apiKey: '', baseUrl: '' },
    isCurrent: () => true,
    getRunSnapshot: async ({ runId }) => {
      if (runId === root.runId) return {
        success: true,
        data: snapshot(runId, { run: { finalResponse: root.finalResponse } }),
      }
      return {
        success: true,
        data: snapshot(runId, { events: [
          event(1, { eventId: 'failed-root-text', runId, payload: { delta: '这是失败前的回复。' } }),
          event(2, { eventId: 'failed-root-commit', runId, kind: 'stream.committed', channel: 'final', payload: {} }),
        ] }),
      }
    },
  })
  assert.equal(hydrated.message.content, '这是失败前的回复。\n\n这是重试后的回复。')
})

test('one analysis stream catches up dynamic child membership without merging child output', () => {
  const stream = new NovelAnalysisConversationStream()
  const cfg = { id: 'model', name: 'model', apiKey: '', baseUrl: '' }
  const root = run()
  const page = { kind: 'analysis_events', nextCursor: 1, hasMore: false, projectionVersion: 'v1',
    runs: [root], chunks: [{ cursor: 1, runId: root.runId, createdAt: '', chunk: event(1, {
      kind: 'run.lifecycle', source: 'runtime', channel: 'lifecycle', payload: { status: 'running' },
    }).chunk }] }
  stream.apply(page, cfg)
  const child = { cursor: 2, runId: 'unit-1', createdAt: '', chunk: event(1, {
    eventId: 'child-event', runId: 'unit-1', channel: 'commentary',
    payload: { delta: '来自模型的证据说明' },
  }).chunk }
  stream.apply({ ...page, runs: undefined, chunks: [child], nextCursor: 2 }, cfg)
  const result = stream.apply({ ...page, nextCursor: 2, chunks: [],
    runs: [run({ relatedRuns: [{ runId: 'unit-1', status: 'running', role: 'child' }] })] }, cfg)
  assert.equal(result.message.canonicalOutput.commentaryBlocks.length, 0)
  const repeated = stream.apply({ ...page, runs: undefined, chunks: [child], nextCursor: 2 }, cfg)
  assert.equal(repeated.message.canonicalOutput.commentaryBlocks.length, 0)
  assert.equal(repeated.message.canonicalOutput.runStatus, 'running')
})

test('history remains readable without a configured model or a creation timestamp', () => {
  const result = new NovelAnalysisConversationStream().apply({
    kind: 'analysis_events', nextCursor: 1, hasMore: false, projectionVersion: 'v1',
    runs: [run()], chunks: [{ cursor: 1, runId: 'analysis-run', createdAt: '', chunk: event(1).chunk }],
  })
  assert.equal(result.message.canonicalOutput.finalText, '模型的真实公开回答。')
  assert.equal(Number.isNaN(result.message.durationMs), false)
})

test('related child output stays outside the root timeline', () => {
  const input = run({ relatedRuns: [{ runId: 'unit-run', status: 'failed', role: 'child' }] })
  const childEvent = (sequence, overrides) => event(sequence, { runId: 'unit-run', ...overrides })
  const child = [
    childEvent(1, { channel: 'commentary', outputStreamId: 'unit-commentary', payload: { delta: '核对原文中的人物关系' } }),
    childEvent(2, { source: 'runtime', kind: 'operation.started', channel: 'operation', payload: {
      operationId: 'read-input', kind: 'tool', startedAt: '2026-08-31T00:00:00Z',
      display: { labelKey: 'agent.operation.tool', labelParams: { toolName: 'readNovelAnalysisInput', displayNames: { 'zh-CN': '读取分析材料' } } },
    } }),
    childEvent(3, { visibility: 'private', payload: { delta: '{"facts":["PRIVATE_RESULT"]}' } }),
    childEvent(4, { source: 'runtime', kind: 'operation.finished', channel: 'operation', payload: {
      operationId: 'read-input', status: 'failed', finishedAt: '2026-08-31T00:00:01Z', durationMs: 1000,
    } }),
    childEvent(5, { source: 'runtime', kind: 'run.lifecycle', channel: 'lifecycle', payload: { status: 'failed' } }),
  ]
  const message = replayAnalysisEvents(input, [
    event(20, { source: 'runtime', kind: 'run.lifecycle', channel: 'lifecycle', payload: { status: 'running' } }),
    ...child,
  ])
  assert.equal(message.content, '')
  assert.equal(message.canonicalOutput.runStatus, 'running')
  assert.equal(message.canonicalOutput.operations['read-input'], undefined)
  assert.equal(message.canonicalOutput.commentaryBlocks.length, 0)
  assert.equal(JSON.stringify(message).includes('PRIVATE_'), false)
})

test('a resumed analysis retains the previous root reply and appends the continuation', () => {
  const input = run({
    runId: 'continuation-run',
    relatedRuns: [{ runId: 'failed-run', status: 'failed', role: 'previous_root' }],
  })
  const previous = event(1, {
    eventId: 'previous-text', runId: 'failed-run', payload: { delta: '失败前已经生成。' },
  })
  const current = event(1, {
    eventId: 'continued-text', runId: 'continuation-run', payload: { delta: '重试后继续生成。' },
  })
  const message = replayAnalysisEvents(input, [previous, current])
  assert.equal(message.canonicalOutput.finalText, '失败前已经生成。重试后继续生成。')
})

test('analysis child Runs are presented as clickable delegation records', () => {
  const [message] = buildNovelAnalysisMessages(run({
    relatedRuns: [{
      runId: 'child-run', status: 'running', role: 'child',
      agentName: 'evidence-reader', agentTitle: '证据读取 Agent', objective: '核对第一章',
    }],
  }), 'model').slice(-1)
  assert.deepEqual(message.delegations, [{
    delegationId: 'run:child-run', runId: 'child-run', agentName: 'evidence-reader',
    agentTitle: '证据读取 Agent', objective: '核对第一章', status: 'running',
    required: true, priority: 0,
  }])
})

test('analysis stream merges overlapping pages without duplicating public text', () => {
  const stream = new NovelAnalysisConversationStream()
  const chunks = [1, 2].map(sequence => ({
    cursor: sequence,
    runId: 'analysis-run',
    createdAt: '',
    chunk: event(sequence, {
      channel: 'commentary',
      outputStreamId: 'progress',
      payload: { delta: sequence === 1 ? '核对' : '证据' },
    }).chunk,
  }))
  const page = {
    kind: 'analysis_events', runs: [run()],
    nextCursor: 1, hasMore: true, projectionVersion: 'v1', chunks: [chunks[0]],
  }
  stream.apply(page)
  const replayed = stream.apply({ ...page, runs: undefined, nextCursor: 2, hasMore: false, chunks })
  assert.equal(replayed.message.canonicalOutput.commentaryBlocks[0].text, '核对证据')
})

test('a new analysis stream starts without the previous source projection', () => {
  const first = replayAnalysisEvents(run(), [event(1)])
  assert.equal(first.canonicalOutput.finalText, '模型的真实公开回答。')
  const second = replayAnalysisEvents(run({ runId: 'next-run' }), [])
  assert.equal(second.agentRunId, 'next-run')
  assert.equal(second.content, '')
  assert.equal(second.canonicalOutput, undefined)
})

for (const status of ['pending', 'running', 'paused', 'failed', 'canceled']) {
  test(`analysis ${status} state never supplies assistant prose`, () => {
    const messages = buildNovelAnalysisMessages(run({
      taskStatus: status,
      finalResponse: '非完成状态不得展示的内容',
      artifactRef: 'review-artifact',
    }), 'model')
    assert.equal(messages[0].content, '只分析人物的认知差异')
    assert.equal(messages[1].content, '')
    assert.equal(messages[1].longTaskId, 'analysis-task')
    assert.equal(messages[1].taskPlan, undefined)
  })
}

test('completed analysis uses the persisted root response when no public events exist', () => {
  const finalResponse = '故事概览：红门事件连接了人物认知差与钥匙冲突。'
  const input = run({ runStatus: 'done', taskStatus: 'completed', finalResponse })
  const replayed = replayAnalysisEvents(input, [])
  assert.equal(buildNovelAnalysisMessages(input, 'model', replayed).at(-1).content, finalResponse)
  assert.equal(buildNovelAnalysisMessages(input, 'model').at(-1).content, finalResponse)
})

test('budget checkpoint remains a normal assistant result while the task is incomplete', () => {
  const finalResponse = '已保存 11/39 个执行单元，尚未生成完整分析。'
  const assistant = buildNovelAnalysisMessages(run({
    runStatus: 'done',
    taskStatus: 'failed',
    partialCompletion: true,
    finalResponse,
    error: 'runtime_budget_exceeded',
  }), 'model').at(-1)

  assert.equal(assistant.content, finalResponse)
  assert.equal(assistant.isError, false)
  assert.equal(assistant.error, undefined)
})

test('a finalized turn and a paused workflow remain distinct without an assistant error', () => {
  const assistant = buildNovelAnalysisMessages(run({
    runStatus: 'done', conversationStatus: 'finalized', taskStatus: 'paused',
    workflowStatus: 'paused', workflowPauseKind: 'system',
    workflowReasonCode: 'upstream_stream_interrupted', workflowResumable: true,
  }), 'model').at(-1)

  assert.equal(assistant.content, '')
  assert.equal(assistant.isError, false)
  assert.equal(assistant.error, undefined)
})

test('public model answer and commentary survive without summaries or private JSON', () => {
  const input = run({ runStatus: 'done', taskStatus: 'completed' })
  const replayed = replayAnalysisEvents(input, [
    event(1, { visibility: 'private', payload: { delta: '{"facts":["private"]}' } }),
    event(2, { channel: 'commentary', outputStreamId: 'model-progress', payload: { delta: '我会先核对两个人物各自知道的信息。' } }),
    event(3, { source: 'runtime', channel: 'commentary', outputStreamId: 'model-progress', kind: 'stream.committed', payload: {} }),
    event(4),
    event(5, { source: 'runtime', kind: 'stream.committed', payload: {} }),
  ])
  const assistant = buildNovelAnalysisMessages(input, 'model', replayed).at(-1)
  assert.equal(assistant.content, '模型的真实公开回答。')
  assert.equal(assistant.canonicalOutput.finalText, assistant.content)
  assert.deepEqual(assistant.commentaryBlocks, ['我会先核对两个人物各自知道的信息。'])
  assert.equal(JSON.stringify(assistant).includes('private'), false)
})

test('live public model text is retained in the shared streaming projection', () => {
  const input = run()
  const replayed = replayAnalysisEvents(input, [event(1)])
  const assistant = buildNovelAnalysisMessages(input, 'model', replayed).at(-1)
  assert.equal(assistant.canonicalOutput.finalText, '模型的真实公开回答。')
  assert.equal(assistant.streamingContent, replayed.streamingContent)
})

test('follow-up retains its actual persisted model answer', () => {
  const input = run({
    interactionKind: 'follow_up', taskId: null, taskStatus: null,
    runStatus: 'done', finalResponse: '甲不知道钥匙的位置，乙知道。',
  })
  const replayed = replayAnalysisEvents(input, [])
  assert.equal(buildNovelAnalysisMessages(input, 'model', replayed).at(-1).content, input.finalResponse)
  assert.equal(buildNovelAnalysisMessages(input, 'model').at(-1).content, input.finalResponse)
})

test('missing prompts and stale replay cannot fabricate conversation text', () => {
  const messages = buildNovelAnalysisMessages(run({ prompt: '' }), 'model', {
    role: 'assistant', agentRunId: 'another-run', content: '另一轮回复',
  })
  assert.equal(messages.length, 1)
  assert.equal(messages[0].role, 'assistant')
  assert.equal(messages[0].content, '')
})

test('failure is kept as an error notice rather than an assistant answer', () => {
  const assistant = buildNovelAnalysisMessages(run({
    taskStatus: 'failed', error: 'model_invocation_deadline_exceeded',
  }), 'model').at(-1)
  assert.equal(assistant.content, '')
  assert.equal(assistant.isError, true)
  assert.match(assistant.error, /超过当前时限/)
})

test('child failure is a terminal Root error without a resume instruction', () => {
  const assistant = buildNovelAnalysisMessages(run({
    runStatus: 'failed', taskStatus: 'failed', workflowStatus: 'failed',
    error: 'max_model_rounds', workflowResumable: false,
  }), 'model').at(-1)
  assert.equal(assistant.content, '')
  assert.equal(assistant.isError, true)
  assert.match(assistant.error, /子 Agent 达到模型轮次上限/)
  assert.doesNotMatch(assistant.error, /继续|恢复|重试/)
})

test('publishes each chunk while more analysis pages are still pending', () => {
  const stream = new NovelAnalysisConversationStream()
  const seen = []
  const page = { kind: 'analysis_events', nextCursor: 2, hasMore: true,
    projectionVersion: 'v1', runs: [run()], chunks: [1, 2].map(sequence => ({
      cursor: sequence, runId: 'analysis-run', createdAt: '',
      chunk: event(sequence, { payload: { delta: String(sequence) } }).chunk,
    })) }
  stream.apply(page, undefined, value => seen.push(value.message.streamingContent))
  assert.deepEqual(seen, ['1', '12'])
  stream.apply(page, undefined, value => seen.push(value.message.streamingContent))
  assert.deepEqual(seen, ['1', '12'])
})

for (const status of ['running', 'completed']) {
  test(`resumed analysis ${status} does not project previous unit failure as current failure`, () => {
    const input = run({ taskStatus: status, runStatus: status === 'completed' ? 'done' : 'running',
      units: [{ unitId:'trial', status:status === 'completed' ? 'completed' : 'running', errorCode:'task_failed_dependency' }],
    })
    const assistant = buildNovelAnalysisMessages(input, 'model', {
      role:'assistant',agentRunId:input.runId,content:'正在重新检验方法',isError:true,error:'旧尝试失败',
    }).at(-1)
    assert.equal(assistant.isError, false)
    assert.equal(assistant.error, undefined)
    assert.equal(assistant.content, '正在重新检验方法')
  })
}

test('completed analysis units do not hide a failed root public presentation', () => {
  const input = run({ taskStatus: 'completed', runStatus: 'failed',
    error: 'model_reasoning_mode_conflict',
    units: [{ unitId: 'artifact:review', status: 'completed' }],
  })
  const assistant = buildNovelAnalysisMessages(input, 'model').at(-1)
  assert.equal(assistant.isError, true)
  assert.match(assistant.error, /推理配置发生冲突/)
})

test('adding a child Run does not replay already delivered root history', () => {
  const stream = new NovelAnalysisConversationStream()
  const page = {kind:'analysis_events', nextCursor:1, hasMore:false, projectionVersion:'v1',
    runs:[run()], chunks:[{cursor:1, runId:'analysis-run', createdAt:'', chunk:event(1).chunk}]}
  let deliveries = 0
  stream.apply(page, undefined, () => deliveries++)
  const previous = deliveries
  stream.apply({...page, chunks:[], runs:[run({relatedRuns:[{runId:'new-child',status:'running'}]})]},
    undefined, () => deliveries++)
  assert.equal(deliveries, previous)
})

test('large history bounds simultaneous snapshot requests without dropping Runs', async () => {
  let active = 0, maximum = 0, completed = 0
  const relatedRuns = Array.from({length:24}, (_,i)=>({runId:`child-${i}`, status:'done'}))
  const result = await hydrateNovelAnalysisHistory({run:run({relatedRuns}),
    model:{id:'test',name:'test',apiKey:'',baseUrl:''}, isCurrent:()=>true,
    getRunSnapshot:async ({runId})=>{
      active++; maximum=Math.max(maximum,active)
      await new Promise(resolve=>setTimeout(resolve,1))
      active--;completed++
      return {success:true,data:snapshot(runId)}
    }})
  assert.ok(result)
  assert.equal(completed,25)
  assert.equal(maximum,4)
})

test('time-sliced history replay yields to interaction and preserves the synchronous result', async () => {
  const {replayAgentRunSnapshot,replayAgentRunSnapshotAsync} = loadTypeScriptModule(
    path.join(__dirname, '../agent-runtime/runSnapshotHydration.ts'))
  const input = {snapshot:snapshot('analysis-run', {run:{status:'running'},
    events:Array.from({length:2500},(_,i)=>event(i+1))}), prompt:'test',turnId:'time-slice'}
  let responded=false
  const timer=setTimeout(()=>{responded=true},0)
  const actual=await replayAgentRunSnapshotAsync(input)
  clearTimeout(timer)
  assert.equal(responded,true,'history replay must allow a queued interaction before completing')
  const expected=replayAgentRunSnapshot(input)
  assert.equal(actual.streamingContent,expected.streamingContent)
  assert.equal(actual.content,expected.content)
  assert.equal(actual.canonicalOutput.finalText,expected.canonicalOutput.finalText)
})

test('new turns and full reload retain every previous public answer', () => {
  const stream = new NovelAnalysisConversationStream()
  const first = run({runStatus: 'done', finalResponse: '第一轮回答'})
  const next = run({runId: 'followup', commandId: 'next', interactionKind: 'follow_up', taskId: null})
  const page = {kind: 'analysis_events', nextCursor: 1, hasMore: false, projectionVersion: 'v1', runs: [first],
    chunks: [{cursor: 1, runId: first.runId, createdAt: '', chunk: event(1).chunk}]}
  stream.apply(page)
  const result = stream.apply({...page, runs: [next, first], nextCursor: 2, chunks: [{cursor: 2, runId: next.runId, createdAt: '',
    chunk: event(2, {runId: next.runId, eventId: 'next-event', payload: {delta: '追问回答'}}).chunk}]})
  assert.ok(result.history[first.runId])
  const fresh = new NovelAnalysisConversationStream().apply({...page, runs: [next, first], chunks: page.chunks})
  assert.deepEqual(fresh.history[first.runId].canonicalOutput, result.history[first.runId].canonicalOutput)
  assert.ok(result.history[next.runId])
})
