import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
} from '../agent-runtime/contracts.ts'
import type { AiModelConfig, AiSession, ScreenplayProject } from '../types.ts'
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
