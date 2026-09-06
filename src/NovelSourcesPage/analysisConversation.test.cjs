const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')
const { buildNovelAnalysisMessages, NovelAnalysisConversationStream } = loadTypeScriptModule(
  path.join(__dirname, 'analysisConversation.ts'),
)

const run = (overrides = {}) => ({
  runId: 'analysis-run', commandId: 'analysis-command', runStatus: 'running',
  taskId: 'analysis-task', taskStatus: 'running', taskRevision: 1,
  prompt: '只分析人物的认知差异', totalUnits: 6, completedUnits: 0, failedUnits: 0,
  units: [{
    unitId: 'extract:1', title: '分析来源片段 1', kind: 'extract_section',
    status: 'running', attempt: 1, maxAttempts: 2,
    summary: '分析来源片段 1完成，识别 20 条事实和 7 个写作技法。',
    highlights: ['旧版合成摘要'],
  }],
  ...overrides,
})

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

test('one analysis stream catches up dynamic units without re-reading snapshots', () => {
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
    runs: [run({ relatedRuns: [{ runId: 'unit-1', status: 'running' }] })] }, cfg)
  assert.equal(result.message.canonicalOutput.commentaryBlocks[0].text, '来自模型的证据说明')
  const repeated = stream.apply({ ...page, runs: undefined, chunks: [child], nextCursor: 2 }, cfg)
  assert.equal(repeated.message.canonicalOutput.commentaryBlocks[0].text, '来自模型的证据说明')
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

test('related unit commentary and tools replay without private results or a false root terminal', () => {
  const input = run({ relatedRuns: [{ runId: 'unit-run', status: 'failed' }] })
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
  assert.equal(message.canonicalOutput.operations['read-input'].status, 'failed')
  assert.equal(message.canonicalOutput.commentaryBlocks[0].text, '核对原文中的人物关系')
  assert.ok(message.canonicalOutput.operations['read-input'].firstSequence > 20)
  assert.equal(message.canonicalOutput.lastSequenceByRun['unit-run'], 5)
  assert.equal(JSON.stringify(message).includes('PRIVATE_'), false)
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
