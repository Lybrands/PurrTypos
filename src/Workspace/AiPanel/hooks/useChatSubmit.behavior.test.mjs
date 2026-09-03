import assert from 'node:assert/strict'
import { after, before, beforeEach, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

let vite
let useChatSubmit
let runtime
let services

const session = (id, title = `会话 ${id}`) => ({ id, title })
const model = (id, apiKey = 'test-key') => ({
  id,
  name: `${id}-name`,
  apiProvider: 'openai',
  supportsThinking: false,
  thinkingOnly: false,
  apiKey,
  baseUrl: `https://${id}.example.test`,
})

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ useChatSubmit } = await vite.ssrLoadModule(
    '/src/Workspace/AiPanel/hooks/useChatSubmit.ts',
  ))
  runtime = await vite.ssrLoadModule(
    '/src/Workspace/AiPanel/hooks/chatRuntimeStore.ts',
  )
  ;({ services } = await vite.ssrLoadModule('/src/services/index.ts'))
})

after(async () => {
  delete globalThis.document
  await vite?.close()
})

beforeEach(() => {
  runtime.clearChatRuntime(7)
  runtime.clearChatRuntime(8)
  runtime.replaceChatRuntimeQueue([])
})

function renderHook(overrides = {}) {
  let result
  let messages = overrides.conversations ?? []
  const setConversations = overrides.setConversations ?? ((next) => {
    messages = typeof next === 'function' ? next(messages) : next
  })
  const params = {
    selectedModelConfig: model('model-a'),
    prompt: '',
    setPrompt: () => undefined,
    loading: false,
    setLoading: () => undefined,
    conversations: messages,
    setConversations,
    bookId: 'book-a',
    chapterId: 'chapter-a',
    activeSessionId: 7,
    sessions: [session(7)],
    setSessions: () => undefined,
    associatedChapterIds: ['associated-chapter-a'],
    associatedOutlineIds: ['outline-a'],
    currentChapterTitle: 'A 章',
    selectedModel: 'model-a',
    agentEnabled: true,
    selectedMemoryIds: ['memory-a'],
    selectedForeshadowingIds: ['foreshadowing-a'],
    writingMethodOverrides: {
      forceRevisionIds: ['method-force-a'],
      excludeRevisionIds: ['method-exclude-a'],
    },
    sessionScope: 'chapter',
    ...overrides,
    setConversations,
  }
  function Harness() {
    result = useChatSubmit(params)
    return React.createElement('div')
  }
  renderToStaticMarkup(React.createElement(Harness))
  return { result, readMessages: () => messages }
}

const jsonResponse = (payload) => new Response(JSON.stringify(payload), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
})

async function waitUntil(predicate) {
  for (let attempt = 0; attempt < 50 && !predicate(); attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}

function rootTransportSnapshot() {
  return {
    version: 1,
    run: {
      runId: 'root-transport',
      sessionId: 7,
      conversationId: null,
      status: 'done',
      lineage: { rootRunId: 'root-transport', depth: 0 },
      finalResponse: 'Authoritative Root response',
      execution: { attempt: 1, cancellationRequested: false },
      provenance: {},
    },
    todos: [],
    events: [{
      version: 1,
      cursor: 1,
      type: 'provider.content_delta',
      runId: 'root-transport',
      payload: { delta: 'Authoritative Root response' },
      createdAt: '2026-08-14T00:00:01+00:00',
      chunk: {
        eventId: 'root-response-1',
        outputStreamId: 'root-final',
        runId: 'root-transport',
        turnId: 'root-turn',
        invocationId: 'root-invocation',
        sequence: 1,
        source: 'provider',
        kind: 'provider.content_delta',
        channel: 'final',
        visibility: 'public',
        payload: { delta: 'Authoritative Root response' },
        occurredAt: '2026-08-14T00:00:01+00:00',
        emittedAt: '2026-08-14T00:00:01+00:00',
      },
    }],
    delegations: {
      items: [],
      aggregate: {
        state: 'ready',
        counts: {
          queued: 0,
          claimed: 0,
          running: 0,
          done: 0,
          failed: 0,
          canceled: 0,
        },
        requiredFailures: [],
        results: [],
      },
    },
    nextCursor: 1,
    hasMore: false,
  }
}

test('batched Provider text is visible output and does not create an empty-response report', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const streamId = 'chat-batched-visible-output'
  const runId = 'run-batched-visible-output'
  const requestReceipt = {
    requestId: streamId,
    sessionId: 7,
    status: 'run_bound',
    runId,
    cancelRequested: false,
    rejectionCode: null,
    revision: 2,
  }
  let errorReportPosts = 0
  const delivered = []
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout: globalThis.setTimeout,
  })
  globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : String(input)
    if (url.includes(`/api/ai/chat/requests/${streamId}`)) {
      return jsonResponse({ success: true, data: requestReceipt })
    }
    if (url.endsWith('/api/ai/chat/stream')) {
      return new Response([
        `data: ${JSON.stringify({ requestReceipt })}`,
        `data: ${JSON.stringify({
          eventId: 'batched-visible-output-1',
          outputStreamId: 'batched-final',
          runId,
          turnId: 'turn-batched-visible-output',
          invocationId: 'invocation-batched-visible-output',
          sequence: 1,
          source: 'provider',
          kind: 'provider.delta_batch',
          channel: 'final',
          visibility: 'public',
          payload: {
            schemaVersion: 'purra.provider-delta-batch/v1',
            entries: [{
              sourceChunkIndex: 1,
              sourcePartIndex: 0,
              kind: 'provider.content_delta',
              payload: { delta: '批量回答' },
            }],
          },
          occurredAt: '2026-08-27T07:34:57Z',
          emittedAt: '2026-08-27T07:34:57Z',
        })}`,
        `data: ${JSON.stringify({
          done: true,
          runResult: { runId, status: 'done', errorCode: null },
        })}`,
        '',
      ].join('\n'), { status: 200 })
    }
    if (url.endsWith('/api/ai/error-reports')) {
      errorReportPosts += 1
      return jsonResponse({ success: true, data: { id: 'unexpected-error-report' } })
    }
    throw new Error(`unexpected URL ${url}`)
  }
  const unsubscribe = services.ai.onAiChunk(
    (chunk) => delivered.push(chunk),
    streamId,
  )
  try {
    services.ai.aiChatStream({
      streamId,
      apiKey: 'test-key',
      sessionId: 7,
      messages: [{ role: 'user', content: '继续' }],
      options: { model: 'test-model' },
      chatAgentMode: 'agent',
      enableAgentTools: true,
    })
    await waitUntil(() => delivered.some((chunk) => chunk.done))

    assert.equal(errorReportPosts, 0)
    assert.equal(delivered.at(-1)?.runResult?.status, 'done')
  } finally {
    unsubscribe()
    services.ai.abortAiStream(streamId)
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
  }
})

test('failed reconnect reports connection loss once without fabricating a Run terminal', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const streamId = 'chat-recovery-exhausted'
  const receipt = { requestId: streamId, sessionId: 7, status: 'run_bound',
    runId: 'root-transport', cancelRequested: false, rejectionCode: null, revision: 2 }
  const delivered = []
  let subscriptions = 0
  let commands = 0
  globalThis.window = Object.assign(new EventTarget(), { setTimeout: globalThis.setTimeout })
  globalThis.fetch = async input => {
    const url = String(input)
    if (url.includes(`/chat/requests/${streamId}`)) return jsonResponse({ success: true, data: receipt })
    if (url.endsWith('/chat/stream')) {
      commands++
      return new Response(`data: ${JSON.stringify({ requestReceipt: receipt })}\n\n`)
    }
    if (url.includes('/agent-runs/root-transport/events')) {
      subscriptions++
      return new Response('', { status: 404 })
    }
    throw new Error(`unexpected request ${url}`)
  }
  const unsubscribe = services.ai.onAiChunk(chunk => delivered.push(chunk), streamId)
  try {
    services.ai.aiChatStream({ streamId, apiKey: 'test-key', sessionId: 7,
      messages: [{ role: 'user', content: '恢复测试' }], options: { model: 'test-model' },
      chatAgentMode: 'agent', enableAgentTools: true, bookId: 'book-1' })
    await waitUntil(() => delivered.some(chunk => chunk.transportError))
    assert.equal(subscriptions, 1)
    assert.equal(commands, 1)
    assert.equal(delivered.filter(chunk => chunk.transportError).length, 1)
    assert.equal(delivered.some(chunk => chunk.done || chunk.error || chunk.runResult), false)
  } finally {
    unsubscribe()
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
  }
})

test('bound backend transport recovers Root after foreign terminal envelopes', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const streamId = 'chat-foreign-terminal-transport'
  const requestReceipt = {
    requestId: streamId,
    sessionId: 7,
    status: 'run_bound',
    runId: 'root-transport',
    cancelRequested: false,
    rejectionCode: null,
    revision: 3,
  }
  let errorReportPosts = 0
  let recoveryReads = 0
  const delivered = []
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout: globalThis.setTimeout,
  })
  globalThis.fetch = async (input, init) => {
    const url = input instanceof Request ? input.url : String(input)
    if (url.includes(`/api/ai/chat/requests/${streamId}`)) {
      return jsonResponse({
        success: true,
        data: {
          ...requestReceipt,
          status: 'accepted',
          runId: null,
          revision: 1,
        },
      })
    }
    if (url.endsWith('/api/ai/chat/stream')) {
      return new Response([
        `data: ${JSON.stringify({ requestReceipt })}`,
        `data: ${JSON.stringify({
          done: true,
          runResult: {
            runId: 'foreign-child',
            status: 'failed',
            errorCode: 'foreign_failure',
          },
        })}`,
        `data: ${JSON.stringify({
          eventId: 'mixed-conflicting-transport-terminal',
          outputStreamId: 'root-final',
          runId: 'root-transport',
          turnId: null,
          invocationId: 'foreign-request-envelope',
          sequence: 9,
          source: 'provider',
          kind: 'provider.content_delta',
          channel: 'final',
          visibility: 'public',
          payload: { delta: 'Foreign envelope text' },
          occurredAt: '2026-08-14T00:00:09+00:00',
          emittedAt: '2026-08-14T00:00:09+00:00',
          done: true,
          aborted: true,
          finalResponseExpected: false,
          requestResult: {
            ...requestReceipt,
            status: 'canceled',
            runId: 'foreign-request-run',
            cancelRequested: true,
            revision: 6,
          },
        })}`,
        '',
      ].join('\n'), { status: 200 })
    }
    if (url.includes('/api/ai/agent-runs/root-transport')) {
      recoveryReads += 1
      assert.ok(url.includes('/events?sessionId=7&after='))
      return new Response(`data: ${JSON.stringify({ ...rootTransportSnapshot(), done: true })}\n\n`)
    }
    if (url.endsWith('/api/ai/error-reports')) {
      errorReportPosts += 1
      return jsonResponse({ success: true, data: { id: 'error-report' } })
    }
    throw new Error(`unexpected ${init?.method || 'GET'} ${url}`)
  }
  const unsubscribe = services.ai.onAiChunk(
    (chunk) => delivered.push(chunk),
    streamId,
  )
  try {
    services.ai.aiChatStream({
      streamId,
      apiKey: 'test-key',
      sessionId: 7,
      messages: [{ role: 'user', content: '继续 Root' }],
      options: { model: 'test-model' },
      chatAgentMode: 'agent',
      enableAgentTools: true,
      bookId: 'book-1',
      chapterId: 'chapter-1',
    })
    await waitUntil(() => delivered.some((chunk) => (
      chunk.runResult?.runId === 'root-transport'
    )))

    assert.equal(errorReportPosts, 0)
    assert.equal(recoveryReads, 1)
    assert.equal(delivered.some((chunk) => (
      chunk.eventId === 'mixed-conflicting-transport-terminal'
    )), false)
    assert.equal(delivered.at(-1)?.runResult?.runId, 'root-transport')
  } finally {
    unsubscribe()
    services.ai.abortAiStream(streamId)
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
  }
})

test('bound chat ignores foreign terminal envelopes until Root settles', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let unsubscribeCalls = 0
  const saved = []
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => { unsubscribeCalls += 1 }
  }
  services.ai.aiChatStream = () => undefined
  services.ai.getAgentRunSnapshot = () => new Promise(() => {})
  services.conversations.saveConversation = async (input) => {
    saved.push(input)
    return { success: true, data: { id: 991 } }
  }
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '继续 Root' }), 'started')
    onChunk({
      requestReceipt: {
        requestId: 'chat-root-terminal',
        sessionId: 7,
        status: 'run_bound',
        runId: 'root-chat',
        cancelRequested: false,
        rejectionCode: null,
        revision: 3,
      },
    })
    onChunk({
      eventId: 'mixed-conflicting-chat-terminal',
      outputStreamId: 'root-final',
      runId: 'root-chat',
      turnId: null,
      invocationId: 'foreign-request-envelope',
      sequence: 1,
      source: 'provider',
      kind: 'provider.content_delta',
      channel: 'final',
      visibility: 'public',
      payload: { delta: 'Foreign envelope text' },
      occurredAt: '2026-08-14T00:00:01+00:00',
      emittedAt: '2026-08-14T00:00:01+00:00',
      done: true,
      runResult: {
        runId: 'foreign-child',
        status: 'failed',
        errorCode: 'foreign_failure',
      },
    })
    onChunk({
      done: true,
      aborted: true,
      finalResponseExpected: false,
      requestResult: {
        requestId: 'foreign-request',
        sessionId: 7,
        status: 'canceled',
        runId: 'foreign-request-run',
        cancelRequested: true,
        rejectionCode: null,
        revision: 4,
      },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.deepEqual(saved, [])
    assert.equal(unsubscribeCalls, 0)
    assert.equal(runtime.getChatSessionRuntime(7)?.loading, true)
    assert.equal(
      runtime.getChatSessionRuntime(7)?.messages.at(-1)?.streamingContent,
      undefined,
    )

    onChunk({
      done: true,
      finalResponse: 'Authoritative Root response',
      runResult: { runId: 'root-chat', status: 'done' },
    })
    await waitUntil(() => (
      saved.length === 1
      && runtime.getChatSessionRuntime(7)?.loading === false
    ))

    assert.equal(saved.length, 1)
    assert.equal(saved[0].agentRunId, 'root-chat')
    assert.equal(saved[0].response, 'Authoritative Root response')
    assert.equal(unsubscribeCalls, 1)
    assert.equal(runtime.getChatSessionRuntime(7)?.loading, false)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('durable Book stop uses the request receipt plane and retains the stream', async () => {
  runtime.replaceChatRuntimeMessages(7, [
    { role: 'user', content: '慢任务' },
    { role: 'assistant', content: '', agentRunId: 'run-slow-1' },
  ])
  runtime.setChatRuntimeLoading(7, true)
  runtime.setChatRuntimeStreamId(7, 'stream-slow-1')
  let cancelCalls = 0
  let abortCalls = 0
  const originalCancel = services.ai.cancelWritingChatRequest
  const originalAbort = services.ai.abortAiStream
  services.ai.cancelWritingChatRequest = async ({ requestId }) => {
    cancelCalls += 1
    assert.equal(requestId, 'stream-slow-1')
    return { success: true, data: { status: 'run_bound' } }
  }
  services.ai.abortAiStream = () => { abortCalls += 1 }
  try {
    const { result } = renderHook({ loading: true })
    await result.handleAbort()
    await result.handleAbort()

    assert.equal(cancelCalls, 1)
    assert.equal(abortCalls, 0)
    assert.equal(runtime.getChatSessionRuntime(7)?.streamId, 'stream-slow-1')
  } finally {
    services.ai.cancelWritingChatRequest = originalCancel
    services.ai.abortAiStream = originalAbort
  }
})

test('stop before the first Run id still request-cancels exactly once', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let cancelCalls = 0
  const originalCancel = services.ai.cancelWritingChatRequest
  const originalStream = services.ai.aiChatStream
  const originalSubscribe = services.ai.onAiChunk
  services.ai.cancelWritingChatRequest = async ({ requestId }) => {
    cancelCalls += 1
    assert.match(requestId, /^chat-7-/)
    return { success: true, data: { status: 'starting' } }
  }
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = () => undefined
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '启动慢 Agent' }), 'started')
    await result.handleAbort()
    await result.handleAbort()
    assert.equal(cancelCalls, 1)
    assert.equal(runtime.getChatSessionRuntime(7)?.stopping, true)
  } finally {
    services.ai.cancelWritingChatRequest = originalCancel
    services.ai.aiChatStream = originalStream
    services.ai.onAiChunk = originalSubscribe
  }
})

test('the actual missing-key submission keeps Assistant content empty', () => {
  const { result, readMessages } = renderHook({
    selectedModelConfig: model('model-a', ''),
  })

  assert.equal(result.handleSubmit({ content: '继续写作' }), 'rejected')
  const assistant = readMessages().at(-1)
  assert.equal(assistant.role, 'assistant')
  assert.equal(assistant.content, '')
  assert.equal(assistant.isError, true)
  assert.match(assistant.error, /API Key/)
})

test('queued request drains the frozen A envelope after the UI switches to B', () => {
  globalThis.document = { documentElement: { lang: 'locale-A' } }
  runtime.replaceChatRuntimeMessages(7, [])
  runtime.setChatRuntimeLoading(7, true)
  const first = renderHook({ loading: true })
  assert.equal(first.result.handleSubmit({ content: '冻结 A 请求' }), 'queued')
  const queued = runtime.getChatRuntimeQueue()[0]
  assert.ok(queued)

  runtime.setChatRuntimeLoading(7, false)
  globalThis.document.documentElement.lang = 'locale-B'
  let streamRequest
  const originalStream = services.ai.aiChatStream
  const originalSubscribe = services.ai.onAiChunk
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = (request) => { streamRequest = request }
  try {
    const second = renderHook({
      selectedModelConfig: model('model-b'),
      bookId: 'book-b',
      chapterId: 'chapter-b',
      currentChapterTitle: 'B 章',
      activeSessionId: 8,
      sessions: [session(7), session(8)],
      associatedChapterIds: ['associated-chapter-b'],
      associatedOutlineIds: ['outline-b'],
      selectedModel: 'model-b',
      agentEnabled: false,
      selectedMemoryIds: ['memory-b'],
      selectedForeshadowingIds: ['foreshadowing-b'],
      writingMethodOverrides: {
        forceRevisionIds: ['method-force-b'],
        excludeRevisionIds: ['method-exclude-b'],
      },
      sessionScope: 'setting',
    })
    assert.equal(second.result.handleSubmit({
      content: queued.content,
      queuedContext: queued,
      preservePrompt: true,
    }), 'started')
  } finally {
    services.ai.aiChatStream = originalStream
    services.ai.onAiChunk = originalSubscribe
  }

  assert.equal(streamRequest.sessionId, 7)
  assert.equal(streamRequest.locale, 'locale-A')
  assert.equal(streamRequest.bookId, 'book-a')
  assert.equal(streamRequest.chapterId, 'chapter-a')
  assert.equal(streamRequest.currentChapterTitle, 'A 章')
  assert.deepEqual(streamRequest.associatedChapterIds, ['associated-chapter-a'])
  assert.deepEqual(streamRequest.associatedOutlineIds, ['outline-a'])
  assert.deepEqual(streamRequest.selectedMemoryIds, ['memory-a'])
  assert.deepEqual(streamRequest.selectedForeshadowingIds, ['foreshadowing-a'])
  assert.deepEqual(streamRequest.writingMethodOverrides, {
    forceRevisionIds: ['method-force-a'],
    excludeRevisionIds: ['method-exclude-a'],
  })
  assert.equal(streamRequest.chatAgentMode, 'agent')
  assert.equal(streamRequest.options.model, 'model-a-name')
})

test('stop before Run identity cancels once and only the authoritative terminal persists', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let unsubscribeCalls = 0
  let cancelCalls = 0
  let abortCalls = 0
  let persistenceCalls = 0
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalCancel = services.ai.cancelWritingChatRequest
  const originalAbort = services.ai.abortAiStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => { unsubscribeCalls += 1 }
  }
  services.ai.aiChatStream = () => undefined
  services.ai.cancelWritingChatRequest = async ({ requestId }) => {
    cancelCalls += 1
    assert.match(requestId, /^chat-7-/)
    return { success: true, data: { status: 'starting' } }
  }
  services.ai.abortAiStream = () => { abortCalls += 1 }
  services.ai.getAgentRunSnapshot = () => new Promise(() => {})
  services.conversations.saveConversation = async (input) => {
    persistenceCalls += 1
    assert.equal(input.agentRunId, 'run-late-identity')
    assert.equal(input.response, '')
    assert.match(input.agentProcess?.termination || '', /终止/)
    return { success: true, data: { id: 901 } }
  }
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '启动慢 Agent' }), 'started')
    await result.handleAbort()
    await result.handleAbort()
    assert.equal(cancelCalls, 1)
    assert.equal(abortCalls, 0)
    assert.equal(unsubscribeCalls, 0)

    onChunk({
      eventId: 'event-1',
      runId: 'run-late-identity',
      sequence: 1,
      source: 'runtime',
      kind: 'run.lifecycle',
      channel: 'lifecycle',
      visibility: 'public',
      payload: { status: 'running' },
    })
    await Promise.resolve()
    assert.equal(cancelCalls, 1)
    assert.equal(unsubscribeCalls, 0)

    const terminal = {
      done: true,
      runResult: { runId: 'run-late-identity', status: 'canceled' },
    }
    onChunk(terminal)
    onChunk(terminal)
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(persistenceCalls, 1)
    assert.equal(unsubscribeCalls, 1)
    assert.equal(runtime.getChatSessionRuntime(7)?.stopping, false)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.ai.cancelWritingChatRequest = originalCancel
    services.ai.abortAiStream = originalAbort
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})


test('production-shaped tool completion hydrates proposal identity before terminal persistence', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  globalThis.window = new EventTarget()
  let onChunk
  const attachments = []
  let visibleSessionId = 7
  const saved = []
  let snapshotReads = 0
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSnapshot = services.ai.getAgentRunSnapshot
  const originalSave = services.conversations.saveConversation
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => undefined
  }
  services.ai.aiChatStream = () => undefined
  services.ai.getAgentRunSnapshot = async () => {
    snapshotReads += 1
    return {
      success: true,
      data: {
      version: 1,
      run: {
        runId: 'run-proposal-live',
        sessionId: 7,
        conversationId: null,
        status: 'running',
        lineage: { rootRunId: 'run-proposal-live', depth: 0 },
        finalResponse: '',
        execution: { attempt: 1, cancellationRequested: false },
        provenance: {},
      },
      todos: [],
      events: [],
      productEvents: [{
        version: 1,
        type: 'writing.proposed_setting_diff',
        runId: 'run-proposal-live',
        proposalId: 'proposal-live-1',
        toolCallId: 'tool-call-1',
        effectIndex: 0,
        payload: {
          proposalId: 'proposal-live-1',
          kind: 'character',
          bookId: 'book-a',
          characterId: 1,
          before: { name: '甲', tags: '', profileMd: '旧' },
          proposed: { name: '甲', tags: '', profileMd: '新' },
        },
        chunk: {
          runId: 'run-proposal-live',
          proposedSettingDiff: {
            proposalId: 'proposal-live-1',
            kind: 'character',
            bookId: 'book-a',
            characterId: 1,
            before: { name: '甲', tags: '', profileMd: '旧' },
            proposed: { name: '甲', tags: '', profileMd: '新' },
          },
        },
      }],
      delegations: {
        items: [],
        aggregate: {
          state: 'ready',
          counts: { queued: 0, claimed: 0, running: 0, done: 0, failed: 0, canceled: 0 },
          requiredFailures: [],
          results: [],
        },
      },
      nextCursor: 0,
      hasMore: false,
      },
    }
  }
  services.conversations.saveConversation = async (input) => {
    saved.push(input)
    return { success: true, data: { id: 902 } }
  }
  try {
    const { result } = renderHook({
      onAssistantAttachment: (message, card, frozenOwner) => attachments.push({
        message,
        card,
        owner: frozenOwner ?? { sessionId: visibleSessionId },
      }),
      productAgentProcess: () => ({
        settingDiff: {
          version: 1,
          aliases: {
            'proposal-live-1': {
              clientTurnIds: ['turn-live'],
              runIds: ['run-proposal-live'],
              conversationIds: [],
            },
          },
        },
      }),
    })
    assert.equal(result.handleSubmit({ content: '更新人物' }), 'started')
    visibleSessionId = 8

    // The private legacy effect has no occurrence identity and is inert.
    onChunk({
      runId: 'run-proposal-live',
      proposedSettingDiff: {
        kind: 'character',
        bookId: 'book-a',
        characterId: 1,
        before: { name: '甲', tags: '', profileMd: '旧' },
        proposed: { name: '甲', tags: '', profileMd: '新' },
      },
    })
    assert.equal(attachments.length, 0)

    onChunk({
      runId: 'run-proposal-live',
      source: 'tool',
      kind: 'tool.event',
      channel: 'operation',
      visibility: 'public',
      payload: { toolCallId: 'tool-call-1', toolName: 'updateCharacter', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(snapshotReads, 1)
    assert.equal(attachments.length, 1)
    assert.equal(attachments[0].card.proposalId, 'proposal-live-1')
    assert.deepEqual(attachments[0].owner, {
      sessionId: 7,
      bookId: 'book-a',
      chapterId: 'chapter-a',
      prompt: '更新人物',
    })

    onChunk({
      done: true,
      runId: 'run-proposal-live',
      runResult: { runId: 'run-proposal-live', status: 'done' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(saved.length, 1)
    assert.ok(saved[0].agentProcess.settingDiff.aliases['proposal-live-1'])
  } finally {
    delete globalThis.window
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.ai.getAgentRunSnapshot = originalSnapshot
    services.conversations.saveConversation = originalSave
  }
})

test('terminal keeps editing disabled until durable conversation persistence settles', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let finishSave
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => undefined
  }
  services.ai.aiChatStream = () => undefined
  services.ai.getAgentRunSnapshot = async () => ({
    success: true,
    data: { productEvents: [] },
  })
  services.conversations.saveConversation = () => new Promise((resolve) => {
    finishSave = resolve
  })
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '等待持久化' }), 'started')
    onChunk({
      done: true,
      finalResponse: '权威终稿',
      runResult: { runId: 'run-persisting', status: 'done' },
    })
    await Promise.resolve()

    assert.equal(runtime.getChatSessionRuntime(7).loading, true)
    assert.equal(result.handleSubmit({ editIndex: 0, content: '不能抢跑' }), 'rejected')

    finishSave({ success: true, data: { id: 99 } })
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('queued turn freezes the persisted conversation frontier after terminal save', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const callbacks = []
  const requests = []
  let finishSave
  let associatedTarget
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    callbacks.push(callback)
    return () => undefined
  }
  services.ai.aiChatStream = (request) => { requests.push(request) }
  services.ai.getAgentRunSnapshot = async () => ({
    success: true,
    data: { productEvents: [] },
  })
  services.conversations.saveConversation = () => new Promise((resolve) => {
    finishSave = resolve
  })
  try {
    const { result } = renderHook({
      associateAssistantIdentities: (_source, target) => {
        associatedTarget = target
      },
    })
    assert.equal(result.handleSubmit({ content: '第一轮' }), 'started')
    assert.equal(result.handleSubmit({ content: '第二轮' }), 'queued')
    callbacks[0]({
      done: true,
      finalResponse: '第一轮终稿',
      runResult: { runId: 'run-first', status: 'done' },
    })
    await Promise.resolve()
    assert.equal(requests.length, 1)

    finishSave({ success: true, data: { id: 77 } })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(requests.length, 2)
    assert.equal(associatedTarget.conversationId, 77)
    assert.equal(runtime.getChatSessionRuntime(7).messages[1].conversationId, 77)
    assert.deepEqual(requests[1].expectedConversationIds, [77])
    assert.deepEqual(requests[1].expectedRunIds, ['run-first'])
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('ambiguous terminal save failure replays idempotently before draining queued work', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let streamCalls = 0
  let saveCalls = 0
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => undefined
  }
  services.ai.aiChatStream = () => { streamCalls += 1 }
  services.ai.getAgentRunSnapshot = async () => ({
    success: true,
    data: { productEvents: [] },
  })
  services.conversations.saveConversation = async () => {
    saveCalls += 1
    return saveCalls === 1
      ? { success: false, error: 'response lost' }
      : { success: true, data: { id: 88 } }
  }
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '第一轮' }), 'started')
    assert.equal(result.handleSubmit({ content: '第二轮' }), 'queued')
    onChunk({
      done: true,
      finalResponse: '第一轮终稿',
      runResult: { runId: 'run-ambiguous-save', status: 'done' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(saveCalls, 2)
    assert.equal(streamCalls, 2)
    assert.equal(runtime.getChatSessionRuntime(7).loading, true)
    assert.equal(runtime.getChatRuntimeQueue().length, 0)
    assert.equal(runtime.getChatSessionRuntime(7).messages[1].conversationId, 88)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('authoritative terminal save conflict unlocks without draining stale queued work', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let streamCalls = 0
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => undefined
  }
  services.ai.aiChatStream = () => { streamCalls += 1 }
  services.ai.getAgentRunSnapshot = async () => ({
    success: true,
    data: { productEvents: [] },
  })
  services.conversations.saveConversation = async () => ({
    success: false,
    error: '对话历史已变化',
    httpStatus: 409,
  })
  try {
    const conflicts = []
    const { result } = renderHook({
      onPersistenceConflict: (sessionId) => conflicts.push(sessionId),
    })
    assert.equal(result.handleSubmit({ content: '第一轮' }), 'started')
    assert.equal(result.handleSubmit({ content: '过期队列' }), 'queued')
    onChunk({
      done: true,
      finalResponse: '第一轮终稿',
      runResult: { runId: 'run-conflicted-save', status: 'done' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(streamCalls, 1)
    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
    assert.equal(runtime.getChatRuntimeQueue().length, 0)
    assert.equal(runtime.getChatSessionRuntime(7).activity.state, 'failed')
    assert.deepEqual(conflicts, [7])
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('Stop exits an ambiguous terminal persistence retry without another save', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  let onChunk
  let saveCalls = 0
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  const originalSave = services.conversations.saveConversation
  const originalSnapshot = services.ai.getAgentRunSnapshot
  services.ai.onAiChunk = (callback) => {
    onChunk = callback
    return () => undefined
  }
  services.ai.aiChatStream = () => undefined
  services.ai.getAgentRunSnapshot = async () => ({
    success: true,
    data: { productEvents: [] },
  })
  services.conversations.saveConversation = async () => {
    saveCalls += 1
    return { success: false, error: 'backend unavailable' }
  }
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '第一轮' }), 'started')
    assert.equal(result.handleSubmit({ content: '不应发送' }), 'queued')
    onChunk({
      done: true,
      finalResponse: '第一轮终稿',
      runResult: { runId: 'run-persistence-stop', status: 'done' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    const callsBeforeStop = saveCalls

    await result.handleAbort()
    await new Promise((resolve) => setTimeout(resolve, 300))

    assert.equal(saveCalls, callsBeforeStop)
    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
    assert.equal(runtime.getChatRuntimeQueue().length, 0)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
    services.conversations.saveConversation = originalSave
    services.ai.getAgentRunSnapshot = originalSnapshot
  }
})

test('live user and assistant rows share the production client turn identity', () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = () => undefined
  try {
    const { result } = renderHook()
    assert.equal(result.handleSubmit({ content: '稳定编辑目标' }), 'started')
    const [user, assistant] = runtime.getChatSessionRuntime(7).messages

    assert.ok(user.clientTurnId)
    assert.equal(user.clientTurnId, assistant.clientTurnId)
  } finally {
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
  }
})

test('edit waits for durable truncation and sends the edited user exactly once', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  runtime.replaceChatRuntimeMessages(7, [
    { role: 'user', content: '保留问题', clientTurnId: 'turn-1', conversationId: 10 },
    { role: 'assistant', content: '保留回答', clientTurnId: 'turn-1', conversationId: 10, agentRunId: 'run-1' },
    { role: 'user', content: '旧问题', clientTurnId: 'turn-2', conversationId: 11 },
    { role: 'assistant', content: '旧回答', clientTurnId: 'turn-2', conversationId: 11, agentRunId: 'run-2' },
  ])
  let finishTruncation
  let streamRequest
  let deleteRequest
  const originalDelete = services.conversations.deleteConversationsAfterTurn
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  services.conversations.deleteConversationsAfterTurn = (input) => new Promise((resolve) => {
    deleteRequest = input
    finishTruncation = resolve
  })
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = (request) => { streamRequest = request }
  try {
    const { result } = renderHook({
      conversations: runtime.getChatSessionRuntime(7).messages,
    })
    assert.equal(result.handleSubmit({ editIndex: 2, content: '新问题' }), 'started')
    assert.equal(streamRequest, undefined)
    assert.equal(runtime.getChatSessionRuntime(7).loading, true)
    assert.deepEqual(deleteRequest.expectedConversationIds, [10, 11])
    assert.deepEqual(deleteRequest.expectedRunIds, ['run-1', 'run-2'])
    assert.deepEqual(deleteRequest.retireClientTurnIds, ['turn-2'])

    finishTruncation({ success: true })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.ok(streamRequest)
    assert.deepEqual(streamRequest.messages, [
      { role: 'user', content: '保留问题' },
      { role: 'assistant', content: '保留回答' },
      { role: 'user', content: '新问题' },
    ])
    assert.deepEqual(streamRequest.expectedConversationIds, [10])
    assert.deepEqual(streamRequest.expectedRunIds, ['run-1'])
  } finally {
    services.conversations.deleteConversationsAfterTurn = originalDelete
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
  }
})

test('authoritative edit truncation conflict reloads instead of retrying stale history', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  runtime.replaceChatRuntimeMessages(7, [
    { role: 'user', content: '旧问题', conversationId: 11 },
    { role: 'assistant', content: '旧回答', conversationId: 11, agentRunId: 'run-11' },
  ])
  let streamCalls = 0
  const conflicts = []
  const originalDelete = services.conversations.deleteConversationsAfterTurn
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  services.conversations.deleteConversationsAfterTurn = async () => ({
    success: false,
    error: '对话历史已变化',
    httpStatus: 409,
  })
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = () => { streamCalls += 1 }
  try {
    const { result } = renderHook({
      conversations: runtime.getChatSessionRuntime(7).messages,
      onPersistenceConflict: (sessionId) => conflicts.push(sessionId),
    })
    assert.equal(result.handleSubmit({ editIndex: 0, content: '新问题' }), 'started')
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(streamCalls, 0)
    assert.deepEqual(conflicts, [7])
    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
  } finally {
    services.conversations.deleteConversationsAfterTurn = originalDelete
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
  }
})

test('Stop during edit truncation prevents the replacement Run from starting', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const history = [
    { role: 'user', content: '旧问题', conversationId: 11 },
    { role: 'assistant', content: '旧回答', conversationId: 11, agentRunId: 'run-old' },
  ]
  runtime.replaceChatRuntimeMessages(7, history)
  let finishTruncation
  let streamCalls = 0
  const originalDelete = services.conversations.deleteConversationsAfterTurn
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  services.conversations.deleteConversationsAfterTurn = () => new Promise((resolve) => {
    finishTruncation = resolve
  })
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = () => { streamCalls += 1 }
  try {
    const { result } = renderHook({ conversations: history })
    assert.equal(result.handleSubmit({ editIndex: 0, content: '新问题' }), 'started')
    await result.handleAbort()
    assert.equal(runtime.getChatSessionRuntime(7).loading, true)
    assert.equal(result.handleSubmit({ content: '不可抢跑' }), 'rejected')
    finishTruncation({ success: true })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(streamCalls, 0)
    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
    assert.equal(runtime.getChatSessionRuntime(7).activity.state, 'canceled')
    assert.deepEqual(runtime.getChatSessionRuntime(7).messages, [])
  } finally {
    services.conversations.deleteConversationsAfterTurn = originalDelete
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
  }
})

test('Stop plus failed truncation restores a settled retryable session', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const history = [
    { role: 'user', content: '旧问题', conversationId: 11 },
    { role: 'assistant', content: '旧回答', conversationId: 11, agentRunId: 'run-old' },
  ]
  runtime.replaceChatRuntimeMessages(7, history)
  let finishTruncation
  const originalDelete = services.conversations.deleteConversationsAfterTurn
  const originalSubscribe = services.ai.onAiChunk
  const originalStream = services.ai.aiChatStream
  services.conversations.deleteConversationsAfterTurn = () => new Promise((resolve) => {
    finishTruncation = resolve
  })
  services.ai.onAiChunk = () => () => undefined
  services.ai.aiChatStream = () => undefined
  try {
    const { result } = renderHook({ conversations: history })
    assert.equal(result.handleSubmit({ editIndex: 0, content: '新问题' }), 'started')
    await result.handleAbort()
    finishTruncation({ success: false, error: 'history changed' })
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(runtime.getChatSessionRuntime(7).loading, false)
    assert.equal(runtime.getChatSessionRuntime(7).activity.state, 'failed')
    assert.deepEqual(runtime.getChatSessionRuntime(7).messages, history)
  } finally {
    services.conversations.deleteConversationsAfterTurn = originalDelete
    services.ai.onAiChunk = originalSubscribe
    services.ai.aiChatStream = originalStream
  }
})
