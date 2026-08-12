import assert from 'node:assert/strict'
import test from 'node:test'
import {
  addBookAssistantAttachment,
  associateBookAssistantAttachmentIdentities,
  bookAttachmentKey,
  getBookAssistantAttachments,
  getBookAssistantAttachmentsForMessage,
  getBookAssistantAttachmentsVersion,
  reduceBookAssistantAttachment,
  resolveStoredBookAssistantAttachments,
  resolveBookAssistantAttachments,
  subscribeBookAssistantAttachments,
} from './bookAssistantAttachments.ts'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
} from '../../agent-runtime/contracts.ts'
import type { AiModelConfig, AiSession } from '../../types.ts'
import type { Conversation } from '../../types.ts'
import { parseConversationsFromApi } from './utils.ts'
import {
  createBookConversationController,
  createHistoryRequestCoordinator,
  toBookQueuedSubmissions,
  type BookConversationBindings,
} from './useBookConversationController.ts'

const noOp = () => undefined
const resolveToolApproval = async () => ({ success: true })
const onSubmitErrorReport = async () => ({ success: true })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}
const messages: AgentConversationMessage[] = [{ role: 'assistant', content: '' }]
const historyMessages: AgentConversationMessage[] = [{ role: 'user', content: '历史' }]
const activities: Record<number, AgentConversationActivity> = {
  7: { state: 'running', queuedCount: 1 },
}
const sessions: AiSession[] = [{
  id: 7,
  title: '第一轮',
  create_time: '2026-08-12 09:30:00',
}]
const historySessions: AiSession[] = [{
  id: 8,
  title: '已关闭对话',
  create_time: '2026-08-11 08:00:00',
}]
const modelConfigs: AiModelConfig[] = [{
  id: 'model-1',
  name: 'Model One',
  apiProvider: 'openai',
  supportsThinking: false,
  thinkingOnly: false,
  apiKey: 'test-key',
  baseUrl: 'https://example.test',
}]
const bindings: BookConversationBindings = {
  sessions,
  historySessions,
  historyLoading: false,
  activeSessionId: 7,
  messages,
  prependedHistory: historyMessages,
  activities,
  queuedMessages: ['继续审阅'],
  prompt: '',
  setPrompt: noOp,
  initializing: false,
  running: false,
  modelConfigs,
  selectedModelId: 'model-1',
  setSelectedModelId: noOp,
  openModelSettings: noOp,
  taskPlan: undefined,
  actions: {
    selectSession: noOp,
    createSession: noOp,
    closeSession: noOp,
    renameSession: noOp,
    loadSessionHistory: noOp,
    openHistorySession: noOp,
    deleteSession: noOp,
    send: noOp,
    abort: noOp,
    editMessage: noOp,
    resolveToolApproval,
    onSubmitErrorReport,
  },
}

test('setting diff is stored outside the generic message', () => {
  const message = { role: 'assistant' as const, content: '', clientTurnId: 'turn-1' }
  const next = reduceBookAssistantAttachment({}, bookAttachmentKey(message), {
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  assert.equal(next['client:turn-1'][0].sessionKey, 'character:1')
  assert.equal('settingDiffCards' in message, false)
})

test('attachment keys prefer live turn, then persisted conversation and replay run', () => {
  assert.equal(bookAttachmentKey({
    role: 'assistant',
    content: '',
    clientTurnId: 'turn-1',
    conversationId: 12,
    agentRunId: 'run-12',
  }), 'client:turn-1')
  assert.equal(bookAttachmentKey({
    role: 'assistant',
    content: '',
    conversationId: 12,
    agentRunId: 'run-12',
  }), 'conversation:12')
  assert.equal(bookAttachmentKey({
    role: 'assistant',
    content: '',
    agentRunId: 'run-12',
  }), 'run:run-12')
})

test('attachment without a stable turn identity is ignored instead of leaking across turns', () => {
  const current = {
    'client:turn-1': [{
      sessionKey: 'character:1',
      kind: 'character' as const,
      title: '人物设定',
      status: 'pending' as const,
    }],
  }

  assert.equal(bookAttachmentKey({ role: 'assistant', content: '' }), undefined)
  assert.equal(reduceBookAssistantAttachment(current, undefined, {
    sessionKey: 'background:1',
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  }), current)
})

test('resolved setting diff updates the same attachment store', () => {
  const current = reduceBookAssistantAttachment({}, 'client:turn-1', {
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  const next = resolveBookAssistantAttachments(current, {
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'committed',
  })

  assert.equal(next['client:turn-1'][0].status, 'committed')
})

test('book attachment store survives consumer unsubscribe and remount', () => {
  const message = {
    role: 'assistant' as const,
    content: '',
    clientTurnId: 'turn-lifecycle',
  }
  let firstConsumerNotifications = 0
  const unsubscribe = subscribeBookAssistantAttachments(() => {
    firstConsumerNotifications += 1
  })

  assert.equal(addBookAssistantAttachment(message, {
    sessionKey: 'character:lifecycle',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  }), true)
  assert.equal(firstConsumerNotifications, 1)
  unsubscribe()

  const versionAfterUnmount = getBookAssistantAttachmentsVersion()
  assert.equal(
    getBookAssistantAttachments()['client:turn-lifecycle'][0].status,
    'pending',
  )
  assert.equal(resolveStoredBookAssistantAttachments({
    sessionKey: 'character:lifecycle',
    kind: 'character',
    title: '人物设定',
    status: 'committed',
  }), true)
  assert.equal(getBookAssistantAttachmentsVersion(), versionAfterUnmount + 1)
  assert.equal(
    getBookAssistantAttachments()['client:turn-lifecycle'][0].status,
    'committed',
  )
})

test('book attachment store does not notify or mutate without a stable turn key', () => {
  const version = getBookAssistantAttachmentsVersion()
  const snapshot = getBookAssistantAttachments()

  assert.equal(addBookAssistantAttachment({ role: 'assistant', content: '' }, {
    sessionKey: 'background:unstable',
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  }), false)
  assert.equal(getBookAssistantAttachmentsVersion(), version)
  assert.equal(getBookAssistantAttachments(), snapshot)
})

test('attachment survives live identity persistence and API message rebuild', () => {
  const liveMessage: AgentConversationMessage = {
    role: 'assistant',
    content: '已更新人物',
    clientTurnId: 'turn-persist-alias',
    agentRunId: 'run-persist-alias',
  }
  addBookAssistantAttachment(liveMessage, {
    sessionKey: 'character:persist-alias',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  assert.equal(
    getBookAssistantAttachments()['run:run-persist-alias'][0].sessionKey,
    'character:persist-alias',
  )
  associateBookAssistantAttachmentIdentities(liveMessage, {
    ...liveMessage,
    conversationId: 501,
  })

  const rebuilt = parseConversationsFromApi([{
    id: 501,
    session_id: 7,
    chapter_id: 'chapter-1',
    prompt: '更新人物',
    response: '已更新人物',
    agent_run_id: 'run-persist-alias',
  } as Conversation]).at(-1)!
  assert.equal(rebuilt.clientTurnId, undefined)
  assert.equal(rebuilt.conversationId, 501)
  assert.equal(
    getBookAssistantAttachmentsForMessage(
      getBookAssistantAttachments(),
      rebuilt,
    )[0].sessionKey,
    'character:persist-alias',
  )
})

test('API message falls through an empty conversation key to its run alias', () => {
  addBookAssistantAttachment({
    role: 'assistant',
    content: '',
    agentRunId: 'run-fallback-alias',
  }, {
    sessionKey: 'background:fallback-alias',
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  })

  const rebuilt: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    conversationId: 999,
    agentRunId: 'run-fallback-alias',
  }
  assert.equal(bookAttachmentKey(rebuilt), 'conversation:999')
  assert.equal(
    getBookAssistantAttachmentsForMessage(
      getBookAssistantAttachments(),
      rebuilt,
    )[0].sessionKey,
    'background:fallback-alias',
  )
})

test('book adapter exposes only display queue entries', () => {
  assert.deepEqual(
    toBookQueuedSubmissions(7, ['第一条', '第二条']),
    [
      { id: 'book-queue-7-0', sessionId: 7, content: '第一条' },
      { id: 'book-queue-7-1', sessionId: 7, content: '第二条' },
    ],
  )
})

test('book adapter maps history, current messages, queue, model and actions', () => {
  const controller = createBookConversationController(bindings)

  assert.deepEqual(controller.conversation.sessions, [{
    id: 7,
    title: '第一轮',
    createdAt: '2026-08-12 09:30:00',
  }])
  assert.deepEqual(controller.conversation.history?.sessions, [{
    id: 8,
    title: '已关闭对话',
    createdAt: '2026-08-11 08:00:00',
  }])
  assert.deepEqual(controller.conversation.messages, [...historyMessages, ...messages])
  assert.deepEqual(controller.conversation.queuedSubmissions, [{
    id: 'book-queue-7-0',
    sessionId: 7,
    content: '继续审阅',
  }])
  assert.equal(controller.conversation.activities, activities)
  assert.equal(controller.composer.selectedModel, modelConfigs[0])
  assert.equal(controller.actions.resolveToolApproval, resolveToolApproval)
  assert.equal(controller.actions.onSubmitErrorReport, onSubmitErrorReport)
})

test('book adapter derives queue capabilities and chapter send availability', () => {
  const running = createBookConversationController({
    ...bindings,
    running: true,
  })
  const unavailable = createBookConversationController({
    ...bindings,
    activeSessionId: null,
    prompt: '继续',
    scopeAvailable: false,
  })

  assert.deepEqual(running.capabilities, {
    inputDisabled: false,
    sessionNavigationDisabled: false,
    submitMode: 'queue',
  })
  assert.equal(unavailable.composer.submitDisabled, true)
})

test('paused book activity is terminal and the panel contract accepts a new request', () => {
  const pausedInput: BookConversationBindings & { paused: true } = {
    ...bindings,
    prompt: '继续新任务',
    paused: true,
    activities: {
      7: { state: 'paused', queuedCount: 0 },
    },
  }
  const controller = createBookConversationController(pausedInput)

  assert.equal(controller.conversation.paused, false)
  assert.equal(controller.conversation.resuming, false)
  assert.equal(controller.capabilities.inputDisabled, false)
  assert.equal(controller.composer.submitDisabled, false)
  assert.equal(controller.actions.resume, undefined)
})

test('book history coordinator ignores stale lifecycle work and deduplicates deletes', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const stale = coordinator.beginLatest()
  const current = coordinator.beginLatest()
  let calls = 0
  let resolveDelete!: () => void
  const pending = new Promise<void>((resolve) => { resolveDelete = resolve })
  const remove = () => coordinator.runOnce('session:7', async () => {
    calls += 1
    await pending
  })

  assert.equal(coordinator.isCurrent(stale), false)
  assert.equal(coordinator.isCurrent(current), true)
  const first = remove()
  const duplicate = remove()
  assert.equal(first, duplicate)
  assert.equal(calls, 1)

  coordinator.deactivate()
  assert.equal(coordinator.isCurrent(current), false)
  resolveDelete()
  await first
})

test('book history coordinator reactivation rejects StrictMode lifecycle work', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const staleRequest = coordinator.beginLatest()
  const stalePending = deferred<void>()
  let staleCommits = 0
  const staleDelete = coordinator.runOnce('session:X', async () => {
    await stalePending.promise
    if (coordinator.isCurrent(staleRequest)) staleCommits += 1
  })

  coordinator.deactivate()
  assert.equal(coordinator.isMounted(), false)
  coordinator.activate()
  const currentRequest = coordinator.beginLatest()
  let currentCommits = 0
  await coordinator.runOnce('session:Y', async () => {
    if (coordinator.isCurrent(currentRequest)) currentCommits += 1
  })

  stalePending.resolve()
  await staleDelete
  assert.equal(staleCommits, 0)
  assert.equal(currentCommits, 1)
})

test('book history coordinator isolates same-key deletes across lifecycles', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const stalePending = deferred<void>()
  let calls = 0
  const staleDelete = coordinator.runOnce('session:X', async () => {
    calls += 1
    await stalePending.promise
  })

  coordinator.deactivate()
  coordinator.activate()
  const currentPending = deferred<void>()
  const currentDelete = coordinator.runOnce('session:X', async () => {
    calls += 1
    await currentPending.promise
  })

  assert.notEqual(currentDelete, staleDelete)
  assert.equal(calls, 2)
  stalePending.resolve()
  currentPending.resolve()
  await Promise.all([staleDelete, currentDelete])
})

test('book history coordinator isolates same-key deletes across scope changes', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const stalePending = deferred<void>()
  const currentPending = deferred<void>()
  let visible = [historySessions[0]]
  let calls = 0
  let staleCleanups = 0
  let currentCleanups = 0

  const staleDelete = coordinator.runOnce('session:8', async () => {
    calls += 1
    const scopeRequest = coordinator.beginMutation()
    await stalePending.promise
    if (!coordinator.completeMutation(scopeRequest)) return
    visible = []
    staleCleanups += 1
  })

  coordinator.invalidateLatest()
  coordinator.invalidateLatest()
  visible = [historySessions[0]]
  const currentDelete = coordinator.runOnce('session:8', async () => {
    calls += 1
    const scopeRequest = coordinator.beginMutation()
    await currentPending.promise
    if (!coordinator.completeMutation(scopeRequest)) return
    visible = []
    currentCleanups += 1
  })

  stalePending.resolve()
  await staleDelete
  assert.deepEqual(visible.map((session) => session.id), [8])
  currentPending.resolve()
  await currentDelete

  assert.notEqual(currentDelete, staleDelete)
  assert.equal(calls, 2)
  assert.equal(staleCleanups, 0)
  assert.equal(currentCleanups, 1)
  assert.deepEqual(visible, [])
})

test('history delete invalidates an earlier load and removes the active session once', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const loadPending = deferred<AiSession[]>()
  const deletePending = deferred<void>()
  let visible = [historySessions[0]]
  let activeCleanupCount = 0

  const loadRequest = coordinator.beginLatest()
  const load = loadPending.promise.then((result) => {
    if (coordinator.isCurrent(loadRequest)) visible = result
  })
  const remove = coordinator.runOnce('session:8', async () => {
    const scopeRequest = coordinator.beginMutation()
    await deletePending.promise
    if (!coordinator.completeMutation(scopeRequest)) return
    visible = visible.filter((session) => session.id !== 8)
    activeCleanupCount += 1
  })

  deletePending.resolve()
  await remove
  loadPending.resolve([historySessions[0]])
  await load

  assert.deepEqual(visible, [])
  assert.equal(activeCleanupCount, 1)
})

test('a later history load cannot overwrite a successful current-scope delete', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const deletePending = deferred<void>()
  const loadPending = deferred<AiSession[]>()
  let visible = [historySessions[0]]
  let activeCleanupCount = 0

  const remove = coordinator.runOnce('session:8', async () => {
    const scopeRequest = coordinator.beginMutation()
    await deletePending.promise
    if (!coordinator.completeMutation(scopeRequest)) return
    visible = visible.filter((session) => session.id !== 8)
    activeCleanupCount += 1
  })
  const loadRequest = coordinator.beginLatest()
  const load = loadPending.promise.then((result) => {
    if (coordinator.isCurrent(loadRequest)) visible = result
  })

  deletePending.resolve()
  await remove
  loadPending.resolve([historySessions[0]])
  await load

  assert.deepEqual(visible, [])
  assert.equal(activeCleanupCount, 1)
})

test('scope invalidation blocks old history delete cleanup', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const deletePending = deferred<void>()
  let visible = [historySessions[0]]
  let activeCleanupCount = 0

  const remove = coordinator.runOnce('session:8', async () => {
    const scopeRequest = coordinator.beginMutation()
    await deletePending.promise
    if (!coordinator.completeMutation(scopeRequest)) return
    visible = []
    activeCleanupCount += 1
  })
  coordinator.invalidateLatest()
  visible = [{ ...historySessions[0], id: 9, title: '新作用域' }]
  deletePending.resolve()
  await remove

  assert.deepEqual(visible.map((session) => session.id), [9])
  assert.equal(activeCleanupCount, 0)
})

test('scope invalidation blocks an old deferred history load', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const loadPending = deferred<AiSession[]>()
  let visible = [historySessions[0]]
  const loadRequest = coordinator.beginLatest()
  const load = loadPending.promise.then((result) => {
    if (coordinator.isCurrent(loadRequest)) visible = result
  })

  coordinator.invalidateLatest()
  visible = [{ ...historySessions[0], id: 9, title: '新作用域' }]
  loadPending.resolve([{ ...historySessions[0], title: '旧作用域' }])
  await load

  assert.deepEqual(visible.map((session) => session.id), [9])
})
