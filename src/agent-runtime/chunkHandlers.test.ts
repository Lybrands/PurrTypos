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
  overrides: Pick<Partial<AgentChunkHost>, 'onHostChunk' | 'onSettled'> = {},
) {
  let messages = initialMessages
  const host: AgentChunkHost = {
    readMessages: () => messages,
    replaceMessages: (next) => { messages = next },
    scheduleCommit: (updater) => { messages = updater(messages) },
    flushCommits: () => undefined,
    setRunning: () => undefined,
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
