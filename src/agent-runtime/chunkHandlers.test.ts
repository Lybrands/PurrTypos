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
