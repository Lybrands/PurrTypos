import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
} from '../agent-runtime/contracts.ts'
import type { AiModelConfig, AiSession, ScreenplayProject } from '../types.ts'
import { advanceLiveTurnCursor } from '../components/AgentConversation/scrollFollowPolicy.ts'
import {
  createScreenplayConversationController,
  type ScreenplayConversationBindings,
  type ScreenplayQueuedSubmission,
} from './useScreenplayConversationController.ts'

const noOp = () => undefined
const resolveToolApproval = async () => ({ success: true })
const onSubmitErrorReport = async () => ({ success: true })
const messages: AgentConversationMessage[] = [{ role: 'assistant', content: '' }]
const activities: Record<string, AgentConversationActivity> = {
  7: { state: 'running', queuedCount: 1 },
}
const sessions: AiSession[] = [{
  id: 7,
  title: '第一轮',
  create_time: '2026-08-12 09:30:00',
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
const bindings: ScreenplayConversationBindings = {
  project: {
    id: 'p1',
    title: '剧本',
    status: 'active',
  } as ScreenplayProject,
  sessions,
  historySessions: [{
    id: 8,
    title: '已关闭对话',
    create_time: '2026-08-11 08:00:00',
    closed: 1,
  }],
  historyLoading: false,
  activeSessionId: 7,
  messages,
  activities,
  queuedSubmissions: [],
  prompt: '',
  setPrompt: noOp,
  initializing: false,
  running: false,
  stopping: false,
  paused: false,
  resuming: false,
  modelConfigs,
  selectedModelId: 'model-1',
  setSelectedModelId: noOp,
  openModelSettings: noOp,
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
    resume: noOp,
    editMessage: noOp,
    resolveToolApproval,
    onSubmitErrorReport,
  },
}

test('screenplay adapter maps durable status and queue without adding prose', () => {
  const queuedSubmissions: ScreenplayQueuedSubmission[] = [{
    id: 'q1',
    projectId: 'p1',
    sessionId: 7,
    content: '继续审阅',
    runtime: {} as ScreenplayQueuedSubmission['runtime'],
  }]
  const controller = createScreenplayConversationController({
    ...bindings,
    running: true,
    queuedSubmissions,
  })

  assert.equal(controller.conversation.running, true)
  assert.deepEqual(controller.conversation.queuedSubmissions, [
    { id: 'q1', sessionId: 7, content: '继续审阅' },
  ])
  assert.equal(controller.conversation.messages, messages)
  assert.equal(controller.conversation.messages.at(-1)?.content, '')
})

test('screenplay adapter preserves sessions, composer state, and existing actions', () => {
  const controller = createScreenplayConversationController(bindings)

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
  assert.equal(controller.actions.loadSessionHistory, bindings.actions.loadSessionHistory)
  assert.equal(controller.actions.openHistorySession, bindings.actions.openHistorySession)
  assert.equal(controller.actions.deleteSession, bindings.actions.deleteSession)
  assert.equal(controller.conversation.activities, activities)
  assert.equal(controller.composer.selectedModel, modelConfigs[0])
  assert.equal(controller.actions.resolveToolApproval, resolveToolApproval)
  assert.equal(controller.actions.onSubmitErrorReport, onSubmitErrorReport)
})

test('screenplay adapter derives archived conversation capabilities', () => {
  const controller = createScreenplayConversationController({
    ...bindings,
    project: { ...bindings.project, status: 'archived' },
    running: true,
    initializing: true,
  })

  assert.deepEqual(controller.capabilities, {
    inputDisabled: true,
    sessionNavigationDisabled: true,
    submitMode: 'queue',
  })
})

test('screenplay adapter blocks composer submission throughout authoritative hydration', () => {
  const controller = createScreenplayConversationController({
    ...bindings,
    prompt: 'A 草稿',
    initializing: true,
  })

  assert.equal(controller.capabilities.inputDisabled, false)
  assert.equal(controller.composer.submitDisabled, true)
  assert.equal(controller.actions, bindings.actions)
})

test('durable screenplay turn ids distinguish fast turns with the same timestamp', () => {
  const pageSource = readFileSync(new URL('./index.tsx', import.meta.url), 'utf8')
  assert.match(pageSource, /clientTurnId:\s*entry\.turnId/)

  const createdAt = '2026-08-12 12:00:00'
  const firstTurn: AgentConversationMessage[] = [
    { role: 'user', content: '第一问', sentAt: createdAt, clientTurnId: 'turn-1' },
    { role: 'assistant', content: '', sentAt: createdAt, clientTurnId: 'turn-1' },
  ]
  const secondTurn: AgentConversationMessage[] = [
    ...firstTurn,
    { role: 'user', content: '第二问', sentAt: createdAt, clientTurnId: 'turn-2' },
    { role: 'assistant', content: '', sentAt: createdAt, clientTurnId: 'turn-2' },
  ]
  const first = advanceLiveTurnCursor(undefined, firstTurn)
  const second = advanceLiveTurnCursor(first.cursor, secondTurn)

  assert.deepEqual(first.cursor, { key: 'user-client:turn-1' })
  assert.deepEqual(second.cursor, { key: 'user-client:turn-2' })
  assert.equal(second.anchorIndex, 2)
})
