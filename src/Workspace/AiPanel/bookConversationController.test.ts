import assert from 'node:assert/strict'
import test from 'node:test'
import {
  addBookAssistantAttachment,
  bookAttachmentKey,
  getBookAssistantAttachments,
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
