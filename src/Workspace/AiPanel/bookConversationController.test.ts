import assert from 'node:assert/strict'
import test from 'node:test'
import {
  bookAttachmentKeys,
  createBookAssistantAttachmentManager,
  getBookAssistantAttachmentsForMessage,
  reduceBookAssistantAttachment,
  resolveBookAssistantAttachments,
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
import { reconcileDeletedOpenSessions } from './sessionDeletion.ts'
import {
  createViewportEditTarget,
  resolveViewportEditTarget,
} from '../../components/AgentConversation/viewportSession.ts'

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
  queuedSubmissions: [{ id: 'q-review', sessionId: 7, content: '继续审阅' }],
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
  const next = reduceBookAssistantAttachment({}, bookAttachmentKeys(message)[0], {
    proposalId: 'proposal-1',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  assert.equal(next['client:turn-1'][0].sessionKey, 'character:1')
  assert.equal('settingDiffCards' in message, false)
})

test('attachment keys prefer live turn, then persisted conversation and replay run', () => {
  assert.equal(bookAttachmentKeys({
    role: 'assistant',
    content: '',
    clientTurnId: 'turn-1',
    conversationId: 12,
    agentRunId: 'run-12',
  })[0], 'client:turn-1')
  assert.equal(bookAttachmentKeys({
    role: 'assistant',
    content: '',
    conversationId: 12,
    agentRunId: 'run-12',
  })[0], 'conversation:12')
  assert.equal(bookAttachmentKeys({
    role: 'assistant',
    content: '',
    agentRunId: 'run-12',
  })[0], 'run:run-12')
})

test('attachment without a stable turn identity is ignored instead of leaking across turns', () => {
  const current = {
    'client:turn-1': [{
      proposalId: 'proposal-1',
      sessionKey: 'character:1',
      kind: 'character' as const,
      title: '人物设定',
      status: 'pending' as const,
    }],
  }

  assert.equal(bookAttachmentKeys({ role: 'assistant', content: '' })[0], undefined)
  assert.equal(reduceBookAssistantAttachment(current, undefined, {
    proposalId: 'proposal-2',
    sessionKey: 'background:1',
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  }), current)
})

test('resolved setting diff updates the same attachment store', () => {
  const current = reduceBookAssistantAttachment({}, 'client:turn-1', {
    proposalId: 'run-1:call-1:0',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  const next = resolveBookAssistantAttachments(current, {
    proposalId: 'run-1:call-1:0',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'committed',
  })

  assert.equal(next['client:turn-1'][0].status, 'committed')
})

test('same entity proposal occurrences retain independent resolutions', () => {
  const first = reduceBookAssistantAttachment({}, 'run:run-1', {
    proposalId: 'run-1:call-1:0',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })
  const second = reduceBookAssistantAttachment(first, 'run:run-2', {
    proposalId: 'run-2:call-2:0',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  const resolved = resolveBookAssistantAttachments(second, {
    proposalId: 'run-1:call-1:0',
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'rejected',
  })

  assert.equal(resolved['run:run-1'][0].status, 'rejected')
  assert.equal(resolved['run:run-2'][0].status, 'pending')
})

test('owner attachment manager survives consumer unsubscribe while owned', () => {
  const manager = createBookAssistantAttachmentManager('book-1:session-7')
  const message = {
    role: 'assistant' as const,
    content: '',
    clientTurnId: 'turn-lifecycle',
  }
  let firstConsumerNotifications = 0
  const unsubscribe = manager.subscribe(() => {
    firstConsumerNotifications += 1
  })

  assert.equal(manager.add(message, {
    proposalId: 'proposal-lifecycle',
    sessionKey: 'character:lifecycle',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  }), true)
  assert.equal(firstConsumerNotifications, 1)
  unsubscribe()

  const versionAfterUnmount = manager.getVersion()
  assert.equal(
    manager.getSnapshot()['client:turn-lifecycle'][0].status,
    'pending',
  )
  assert.equal(manager.resolve({
    proposalId: 'proposal-lifecycle',
    sessionKey: 'character:lifecycle',
    kind: 'character',
    title: '人物设定',
    status: 'committed',
  }), true)
  assert.equal(manager.getVersion(), versionAfterUnmount + 1)
  assert.equal(
    manager.getSnapshot()['client:turn-lifecycle'][0].status,
    'committed',
  )
})

test('deleting a session evicts only that persisted attachment owner', () => {
  const manager = createBookAssistantAttachmentManager('book-1')
  const add = (sessionId: number, suffix: string) => manager.add({
    role: 'assistant',
    content: '',
    agentRunId: `run-${suffix}`,
  }, {
    proposalId: `proposal-${suffix}`,
    sessionKey: `background:${suffix}`,
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  }, {
    sessionId,
    bookId: 'book-1',
    chapterId: null,
    prompt: `prompt-${suffix}`,
    message: { role: 'assistant', content: '' },
  })
  add(7, 'a')
  add(8, 'b')

  assert.equal(manager.evictSession(7), true)
  assert.equal(manager.ownerForProposal('proposal-a'), undefined)
  assert.equal(manager.getSnapshot()['run:run-a'], undefined)
  assert.equal(manager.ownerForProposal('proposal-b')?.sessionId, 8)
  assert.equal(manager.getSnapshot()['run:run-b'][0].proposalId, 'proposal-b')
  assert.equal(manager.evictSession(7), false)

  assert.equal(add(7, 'late-a'), false)
  assert.equal(manager.ownerForProposal('proposal-late-a'), undefined)
  assert.equal(manager.getSnapshot()['run:run-late-a'], undefined)
})

test('book attachment store does not notify or mutate without a stable turn key', () => {
  const manager = createBookAssistantAttachmentManager('book-1:session-7')
  const version = manager.getVersion()
  const snapshot = manager.getSnapshot()

  assert.equal(manager.add({ role: 'assistant', content: '' }, {
    proposalId: 'proposal-unstable',
    sessionKey: 'background:unstable',
    kind: 'background',
    title: '故事背景',
    status: 'pending',
  }), false)
  assert.equal(manager.getVersion(), version)
  assert.equal(manager.getSnapshot(), snapshot)
})

test('attachment survives live identity persistence and API message rebuild', () => {
  const manager = createBookAssistantAttachmentManager('book-1:session-7')
  const liveMessage: AgentConversationMessage = {
    role: 'assistant',
    content: '已更新人物',
    clientTurnId: 'turn-persist-alias',
    agentRunId: 'run-persist-alias',
  }
  manager.add(liveMessage, {
    proposalId: 'proposal-persist-alias',
    sessionKey: 'character:persist-alias',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })

  assert.equal(
    manager.getSnapshot()['run:run-persist-alias'][0].sessionKey,
    'character:persist-alias',
  )
  manager.associate(liveMessage, {
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
      manager.getSnapshot(),
      rebuilt,
    )[0].sessionKey,
    'character:persist-alias',
  )
})

test('persisted user identity comes from the real conversation row', () => {
  const first = parseConversationsFromApi([{
    id: 501,
    session_id: 7,
    chapter_id: 'chapter-1',
    prompt: '第一条用户消息',
    response: '第一条回复',
  } as Conversation])
  const replacement = parseConversationsFromApi([{
    id: 502,
    session_id: 7,
    chapter_id: 'chapter-1',
    prompt: '替换消息',
    response: '替换回复',
  } as Conversation])

  assert.equal(first[0].conversationId, 501)
  assert.equal(first[1].conversationId, 501)
  const target = createViewportEditTarget('session:7:1', 0, first[0])
  assert.equal(
    resolveViewportEditTarget(target, 'session:7:1', replacement),
    undefined,
  )
})

test('API message falls through an empty conversation key to its run alias', () => {
  const manager = createBookAssistantAttachmentManager('book-1:session-7')
  manager.add({
    role: 'assistant',
    content: '',
    agentRunId: 'run-fallback-alias',
  }, {
    proposalId: 'proposal-fallback-alias',
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
  assert.equal(bookAttachmentKeys(rebuilt)[0], 'conversation:999')
  assert.equal(
    getBookAssistantAttachmentsForMessage(
      manager.getSnapshot(),
      rebuilt,
    )[0].sessionKey,
    'background:fallback-alias',
  )
})

test('local non-journal terminal metadata reloads as structured state, never Assistant prose', () => {
  const rebuilt = parseConversationsFromApi([{
    id: 777,
    session_id: 7,
    chapter_id: 'chapter-1',
    prompt: '本地失败',
    response: '',
    agent_process: JSON.stringify({
      error: '本地传输失败',
      isError: true,
      termination: '本轮已停止',
    }),
  } as Conversation]).at(-1)!

  assert.equal(rebuilt.content, '')
  assert.equal(rebuilt.error, '本地传输失败')
  assert.equal(rebuilt.isError, true)
  assert.equal(rebuilt.termination, '本轮已停止')
})

test('book adapter exposes only display queue entries', () => {
  assert.deepEqual(
    toBookQueuedSubmissions(7, [
      { id: 'q-first', sessionId: 7, content: '第一条' },
      { id: 'q-second', sessionId: 7, content: '第二条' },
      { id: 'other-session', sessionId: 8, content: '其他会话' },
    ]),
    [
      { id: 'q-first', sessionId: 7, content: '第一条' },
      { id: 'q-second', sessionId: 7, content: '第二条' },
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
    id: 'q-review',
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
    submitMode: 'queue',
  })
  assert.equal(unavailable.composer.submitDisabled, true)
})

test('book hydration keeps typing editable while blocking send and edit actions', async () => {
  let sends = 0
  let edits = 0
  const controller = createBookConversationController({
    ...bindings,
    initializing: true,
    prompt: 'A 会话尚未恢复完成',
    actions: {
      ...bindings.actions,
      send: () => { sends += 1 },
      editMessage: () => { edits += 1 },
    },
  })

  assert.equal(controller.capabilities.inputDisabled, false)
  assert.equal(controller.composer.submitDisabled, true)
  await controller.actions.send()
  await controller.actions.editMessage(0, '不能截断尚未恢复的会话')
  assert.equal(sends, 0)
  assert.equal(edits, 0)
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

test('pending history delete blocks open and a newer load orders a late failure out', () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const token = coordinator.beginSessionDelete(8, true)

  assert.ok(token)
  assert.equal(coordinator.canOpenSession(8), false)
  coordinator.beginLatest()
  assert.equal(coordinator.failSessionDelete(token!), false)
  assert.equal(coordinator.canOpenSession(8), true)
})

test('pending history delete also blocks an already-open tab selection action', () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const selected: Array<string | number> = []
  const controller = createBookConversationController({
    ...bindings,
    canSelectSession: (id: string | number) => coordinator.canOpenSession(Number(id)),
    actions: {
      ...bindings.actions,
      selectSession: (id) => { selected.push(id) },
    },
  })
  const token = coordinator.beginSessionDelete(8, true)
  assert.ok(token)

  controller.actions.selectSession(8)
  assert.deepEqual(selected, [])

  coordinator.failSessionDelete(token!)
  controller.actions.selectSession(8)
  assert.deepEqual(selected, [8])
})

test('active running or queued history sessions are rejected before the delete service', () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()

  assert.equal(coordinator.beginSessionDelete(7, false), null)
  assert.equal(coordinator.isSessionDeleting(7), false)
})

test('delete completion reconciles against the current active session, not request start', () => {
  const open = [
    { id: 7, title: 'A' },
    { id: 8, title: 'B' },
  ] as AiSession[]

  assert.deepEqual(reconcileDeletedOpenSessions(open, 7, 8), {
    sessions: [open[1]],
    activeSessionId: 8,
  })
  assert.deepEqual(reconcileDeletedOpenSessions(open, 8, 8), {
    sessions: [open[0]],
    activeSessionId: 7,
  })
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

test('history deletion lifecycle blocks opening and rejects active work', () => {
  const coordinator = createHistoryRequestCoordinator() as ReturnType<
    typeof createHistoryRequestCoordinator
  > & {
    beginSessionDelete(id: number, allowed: boolean): object | null
    isSessionDeleting(id: number): boolean
    canOpenSession(id: number): boolean
  }
  coordinator.activate()

  assert.equal(coordinator.beginSessionDelete(7, false), null)
  const token = coordinator.beginSessionDelete(8, true)
  assert.ok(token)
  assert.equal(coordinator.isSessionDeleting(8), true)
  assert.equal(coordinator.canOpenSession(8), false)
})
