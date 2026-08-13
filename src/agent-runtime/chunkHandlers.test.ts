import assert from 'node:assert/strict'
import test from 'node:test'
import {
  dispatchAgentChunk,
  initialAgentAccumulator,
  type AgentChunkHost,
  type AgentTerminalSnapshot,
} from './chunkHandlers/index.ts'
import type { AgentConversationMessage } from './contracts.ts'

function createTestChunkContext(
  initialMessages: AgentConversationMessage[],
  overrides: Pick<
    Partial<AgentChunkHost>,
    | 'flushCommits'
    | 'onHostChunk'
    | 'onSettled'
    | 'replaceMessages'
    | 'setRunning'
  > = {},
) {
  let messages = initialMessages
  let replacementCount = 0
  const host: AgentChunkHost = {
    readMessages: () => messages,
    replaceMessages: (next) => {
      replacementCount += 1
      overrides.replaceMessages?.(next)
      messages = next
    },
    scheduleCommit: (updater) => { messages = updater(messages) },
    flushCommits: overrides.flushCommits ?? (() => undefined),
    setRunning: overrides.setRunning ?? (() => undefined),
    isVisible: () => true,
    onHostChunk: overrides.onHostChunk,
    onSettled: overrides.onSettled ?? (() => undefined),
  }
  return {
    context: {
      acc: initialAgentAccumulator({
        sessionId: 1,
        userText: '问题',
        turnStartedAt: 0,
      }),
      sessionId: 1,
      modelIdentity: { name: 'test-model' },
      host,
      now: () => 100,
    },
    readMessages: () => messages,
    readReplacementCount: () => replacementCount,
  }
}

test('terminal projection calls the host once and keeps provider text unchanged', () => {
  const settled: AgentTerminalSnapshot[] = []
  const messages = [
    { role: 'user' as const, content: '问题' },
    { role: 'assistant' as const, content: '', streamingContent: '模型原文' },
  ]
  const harness = createTestChunkContext(messages, {
    onSettled: (_outcome, snapshot) => settled.push(snapshot),
  })
  harness.context.acc.response = '模型原文'

  dispatchAgentChunk({ done: true, model: 'test-model' }, harness.context)

  assert.equal(settled.length, 1)
  assert.equal(settled[0].response, '模型原文')
  assert.equal(harness.readMessages().at(-1)?.content, '模型原文')
})

test('snapshot-recovered terminal replaces partial canonical copy with final response', () => {
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    {
      role: 'assistant',
      content: '半段',
      canonicalOutput: {
        lastSequence: 1,
        lastSequenceByRun: { 'run-1': 1 },
        finalText: '半段',
        commentaryText: '',
        commentaryBlocks: [],
        operations: {},
        operationOrder: [],
        delegations: {},
        delegationOrder: [],
        approvals: {},
        approvalOrder: [],
        runId: 'run-1',
        runStatus: null,
        runTerminal: false,
        finalStreamStatus: 'open',
        finalStreamId: 'final-1',
        finalStreamErrorCode: null,
        latestRuntimeEvent: null,
      },
    },
  ])
  harness.context.acc.response = '半段'
  harness.context.acc.canonicalOutput = harness.readMessages().at(-1)?.canonicalOutput

  dispatchAgentChunk({
    done: true,
    finalResponse: '服务端完整终稿',
    runResult: { runId: 'run-1', status: 'done' },
  }, harness.context)

  const message = harness.readMessages().at(-1)
  assert.equal(message?.content, '服务端完整终稿')
  assert.equal(message?.canonicalOutput?.finalText, '服务端完整终稿')
  assert.equal(message?.canonicalOutput?.finalStreamStatus, 'committed')
})

test('durable long-task dispatch settles without receipt prose or empty-response error', () => {
  const outcomes: string[] = []
  const harness = createTestChunkContext([
    { role: 'user', content: '继续长篇任务' },
    { role: 'assistant', content: '' },
  ], {
    onSettled: (outcome) => outcomes.push(outcome),
  })

  dispatchAgentChunk({
    eventId: 'dispatch-1',
    runId: 'run-long-task',
    sequence: 1,
    source: 'runtime',
    kind: 'runtime.event',
    channel: 'lifecycle',
    visibility: 'public',
    payload: {
      eventType: 'long_task.dispatched',
      data: { taskId: 'long-task-1' },
    },
    occurredAt: '2026-08-13T00:00:00+00:00',
    emittedAt: '2026-08-13T00:00:00+00:00',
  }, harness.context)
  dispatchAgentChunk({
    done: true,
    finalResponse: '已恢复原有长篇正文任务。',
    finalResponseExpected: false,
    runResult: { runId: 'run-long-task', status: 'done' },
  }, harness.context)

  const message = harness.readMessages().at(-1)
  assert.equal(message?.content, '')
  assert.equal(message?.longTaskId, 'long-task-1')
  assert.equal(message?.error, undefined)
  assert.equal(message?.isError, undefined)
  assert.deepEqual(outcomes, ['paused'])
})

test('a Run with long-task progress preserves genuine provider final text', () => {
  const outcomes: string[] = []
  const harness = createTestChunkContext([
    { role: 'user', content: '创作正文' },
    { role: 'assistant', content: '' },
  ], {
    onSettled: (outcome) => outcomes.push(outcome),
  })
  harness.context.acc.longTaskId = 'long-task-with-text'
  harness.context.acc.response = '第一场正文。'

  dispatchAgentChunk({ done: true }, harness.context)

  assert.equal(harness.readMessages().at(-1)?.content, '第一场正文。')
  assert.deepEqual(outcomes, ['completed'])
})

test('long-task completion does not settle the public plan before Root Run events', () => {
  const harness = createTestChunkContext([
    { role: 'user', content: '续写正文' },
    { role: 'assistant', content: '' },
  ])
  const dispatch = (
    sequence: number,
    eventType: string,
    data: Record<string, unknown>,
    runId = 'root-run-2',
  ) => {
    dispatchAgentChunk({
      eventId: `public-plan-${sequence}`,
      runId,
      sequence,
      source: 'runtime',
      kind: 'runtime.event',
      channel: 'lifecycle',
      visibility: 'public',
      payload: { eventType, data },
      occurredAt: `2026-08-13T00:00:0${sequence}+00:00`,
      emittedAt: `2026-08-13T00:00:0${sequence}+00:00`,
    }, harness.context)
  }

  dispatch(1, 'run.todos_updated', {
    runId: 'root-run-2',
    title: 'Root Run 公开计划',
    status: 'running',
    steps: [{
      id: 'draft',
      title: '起草正文',
      type: 'write',
      status: 'running',
    }],
  })
  dispatch(2, 'run.todo_updated', {
    runId: 'child-run-2',
    stepId: 'draft',
    status: 'running',
    step: {
      id: 'draft',
      title: '子 Run 改写步骤',
      type: 'write',
      status: 'done',
    },
  }, 'child-run-2')
  dispatch(3, 'run.completed', { runId: 'child-run-2' }, 'child-run-2')

  let message = harness.readMessages().at(-1)
  assert.equal(message?.taskPlan?.status, 'running')
  assert.deepEqual(
    message?.taskPlan?.steps.map((step) => [step.id, step.title, step.status]),
    [['draft', '起草正文', 'running']],
  )

  dispatch(4, 'long_task.progress', {
    taskId: 'recipe-task-2',
    status: 'completed',
    units: [{
      id: 'recipe:write',
      position: 0,
      title: 'Recipe 完成正文',
      kind: 'generate_document_section',
      status: 'completed',
    }],
  })

  message = harness.readMessages().at(-1)
  assert.equal(message?.longTaskId, 'recipe-task-2')
  assert.equal(message?.taskPlan?.status, 'running')
  assert.deepEqual(
    message?.taskPlan?.steps.map((step) => [step.id, step.title, step.status]),
    [['draft', '起草正文', 'running']],
  )

  dispatch(5, 'run.todo_updated', {
    runId: 'root-run-2',
    stepId: 'draft',
    status: 'running',
    step: {
      id: 'draft',
      title: '起草正文',
      type: 'write',
      status: 'done',
    },
  })
  message = harness.readMessages().at(-1)
  assert.equal(message?.taskPlan?.steps[0]?.status, 'done')
  assert.equal(message?.taskPlan?.status, 'running')

  dispatch(6, 'run.completed', { runId: 'root-run-2' })
  assert.equal(harness.readMessages().at(-1)?.taskPlan?.status, 'done')
})

test('runtime invokes injected host chunk handling without knowing book events', () => {
  const received: unknown[] = []
  const harness = createTestChunkContext([], {
    onHostChunk: (chunk) => received.push(chunk),
  })
  const chunk = { settingUpdated: { kind: 'character' as const } }
  dispatchAgentChunk(chunk, harness.context)
  assert.deepEqual(received, [chunk])
})

test('a runtime context settles only once across duplicate terminal chunks', () => {
  const settled: string[] = []
  const running: boolean[] = []
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    { role: 'assistant', content: '' },
  ], {
    setRunning: (next) => running.push(next),
    onSettled: (outcome) => settled.push(outcome),
  })
  harness.context.acc.response = '模型终稿'

  dispatchAgentChunk({ done: true }, harness.context)
  const firstTerminalMessage = harness.readMessages().at(-1)
  dispatchAgentChunk({ error: '迟到的 transport error' }, harness.context)

  assert.deepEqual(settled, ['completed'])
  assert.deepEqual(running, [false])
  assert.equal(harness.readReplacementCount(), 1)
  assert.deepEqual(harness.readMessages().at(-1), firstTerminalMessage)
})

test('an early transport error stays out of content and remains visible in metadata', () => {
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    { role: 'assistant', content: '' },
  ])

  dispatchAgentChunk({ error: '上游连接失败' }, harness.context)

  const message = harness.readMessages().at(-1)
  assert.equal(message?.content, '')
  assert.equal(message?.isError, true)
  assert.equal(message?.error, '上游连接失败')
})

test('a pre-Run canceled request settles as canceled rather than paused', () => {
  const outcomes: string[] = []
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    { role: 'assistant', content: '' },
  ], {
    onSettled: (outcome) => outcomes.push(outcome),
  })

  dispatchAgentChunk({
    done: true,
    aborted: true,
    finalResponseExpected: false,
    requestResult: {
      requestId: 'request-canceled',
      sessionId: 7,
      status: 'canceled',
      runId: null,
      cancelRequested: true,
      rejectionCode: null,
      revision: 2,
    },
  }, harness.context)

  assert.deepEqual(outcomes, ['canceled'])
  assert.match(harness.readMessages().at(-1)?.termination || '', /终止/)
})

test('a pre-Run rejected request settles as a structured failure', () => {
  const outcomes: string[] = []
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    { role: 'assistant', content: '' },
  ], {
    onSettled: (outcome) => outcomes.push(outcome),
  })

  dispatchAgentChunk({
    done: true,
    finalResponseExpected: false,
    requestResult: {
      requestId: 'request-rejected',
      sessionId: 7,
      status: 'rejected',
      runId: null,
      cancelRequested: false,
      rejectionCode: 'invalid_model',
      revision: 2,
    },
  }, harness.context)

  assert.deepEqual(outcomes, ['failed'])
  assert.equal(harness.readMessages().at(-1)?.content, '')
  assert.equal(harness.readMessages().at(-1)?.error, 'invalid_model')
})

for (const failingHostMethod of [
  'flushCommits',
  'replaceMessages',
  'setRunning',
] as const) {
  test(`terminal settlement retries after ${failingHostMethod} throws`, () => {
    let shouldThrow = true
    const settled: string[] = []
    const failOnce = () => {
      if (!shouldThrow) return
      shouldThrow = false
      throw new Error(`${failingHostMethod} failed`)
    }
    const harness = createTestChunkContext([
      { role: 'user', content: '问题' },
      { role: 'assistant', content: '' },
    ], {
      flushCommits: failingHostMethod === 'flushCommits'
        ? failOnce
        : () => undefined,
      replaceMessages: failingHostMethod === 'replaceMessages'
        ? failOnce
        : undefined,
      setRunning: failingHostMethod === 'setRunning'
        ? failOnce
        : undefined,
      onSettled: (outcome) => settled.push(outcome),
    })
    harness.context.acc.response = '可重试的模型终稿'

    assert.throws(
      () => dispatchAgentChunk({ done: true }, harness.context),
      new RegExp(`${failingHostMethod} failed`),
    )
    dispatchAgentChunk({ done: true }, harness.context)

    assert.deepEqual(settled, ['completed'])
    assert.equal(
      harness.readMessages().at(-1)?.content,
      '可重试的模型终稿',
    )
  })
}

test('onSettled is delivered at most once even when the host throws', () => {
  const deliveries: string[] = []
  let shouldThrow = true
  const harness = createTestChunkContext([
    { role: 'user', content: '问题' },
    { role: 'assistant', content: '' },
  ], {
    onSettled: (outcome) => {
      deliveries.push(outcome)
      if (!shouldThrow) return
      shouldThrow = false
      throw new Error('host failed after side effect')
    },
  })
  harness.context.acc.response = '已投影终稿'

  assert.throws(
    () => dispatchAgentChunk({ done: true }, harness.context),
    /host failed after side effect/,
  )
  dispatchAgentChunk({ done: true }, harness.context)

  assert.deepEqual(deliveries, ['completed'])
  assert.equal(harness.readReplacementCount(), 1)
  assert.equal(harness.readMessages().at(-1)?.content, '已投影终稿')
})
