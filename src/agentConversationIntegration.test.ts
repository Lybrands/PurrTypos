import assert from 'node:assert/strict'
import test from 'node:test'
import { createScopedComposer } from './components/AgentConversation/scopedComposer.ts'
import { isComposerSubmitDisabled } from './components/AgentConversation/composerPolicy.ts'
import { createAnalysisConversationController } from './NovelSourcesPage/analysisController.ts'
import { createBookConversationController } from './Workspace/AiPanel/useBookConversationController.ts'
import { createScreenplayConversationController } from './ScreenplayAgentPage/useScreenplayConversationController.ts'
import type { ScreenplayProject } from './types.ts'

const noop = () => undefined
const model = { id: 'm', name: 'model', apiKey: 'test', baseUrl: '', supportsThinking: false, thinkingOnly: false }
const actions = { send: noop, abort: noop, resume: noop, selectSession: noop, createSession: noop,
  closeSession: noop, renameSession: noop, editMessage: noop, resolveToolApproval: async () => ({ success: true }) }
const base = { sessions: [], historySessions: [], historyLoading: false, activeSessionId: 1,
  messages: [], prependedHistory: [], activities: {}, queuedMessages: [], queuedSubmissions: [],
  prompt: '任务', setPrompt: noop, initializing: false, running: false, stopping: false, paused: false,
  resuming: false, modelConfigs: [model], selectedModelId: 'm', setSelectedModelId: noop,
  openModelSettings: noop, actions, project: { id: 'p', status: 'active' } as ScreenplayProject }

const factories = {
  writing: (overrides: Partial<typeof base>) => createBookConversationController({ ...base, ...overrides }),
  screenplay: (overrides: Partial<typeof base>) => createScreenplayConversationController({ ...base, ...overrides }),
  analysis: (overrides: Partial<typeof base>) => {
    const b = { ...base, ...overrides }
    return createAnalysisConversationController({
      conversation: { identity: 'analysis:revision', sessions: [], activeSessionId: b.activeSessionId,
        messages: [], activities: {}, queuedSubmissions: [], initializing: b.initializing,
        running: b.running, stopping: b.stopping, paused: b.paused, resuming: b.resuming },
      composer: { value: b.prompt, setValue: noop, placeholder: '分析', ariaLabel: '分析',
        submitDisabled: false, selectedModel: b.modelConfigs.find(m => m.id === b.selectedModelId) ?? null,
        modelConfigs: b.modelConfigs, selectModel: noop, openModelSettings: noop }, actions,
    })
  },
}

for (const [name, create] of Object.entries(factories)) {
  test(`${name}: shared composer admission preserves editing and protects submission`, () => {
    const running = create({ running: true })
    assert.equal(running.capabilities.inputDisabled, false)
    assert.equal(running.capabilities.submitMode, 'queue')
    assert.equal(isComposerSubmitDisabled(running), false)
    for (const overrides of [{ initializing: true }, { stopping: true }, { prompt: '  ' }, { modelConfigs: [] }]) {
      const controller = create(overrides)
      assert.equal(controller.capabilities.inputDisabled, false)
      assert.equal(isComposerSubmitDisabled(controller), true)
    }
    for (const field of ['paused', 'resuming'] as const) {
      const controller = create({})
      controller.conversation[field] = true
      assert.equal(isComposerSubmitDisabled(controller), true)
    }
    const structured = create({ prompt: '' })
    assert.equal(isComposerSubmitDisabled(structured, '结构化回答'), false)
    structured.composer.ready = false
    assert.equal(isComposerSubmitDisabled(structured, '结构化回答'), true)
    const missingSession = create({})
    missingSession.conversation.activeSessionId = null
    assert.equal(isComposerSubmitDisabled(missingSession), true)
  })
}

test('draft and queue are scoped; frozen requests drain once in FIFO order', () => {
  const store = createScopedComposer<{ model: string }>()
  store.setDraft('source-A:v1', '第一问')
  const runtime = { model: 'old' }
  const first = store.enqueue('source-A:v1', '1', '第一问', runtime, true)
  runtime.model = 'new'
  store.setDraft('source-A:v1', '继续输入')
  store.setDraft('source-B:v1', 'B 草稿')
  store.enqueue('source-A:v1', '2', '第二问', runtime, false)
  assert.equal(store.claim('source-B:v1'), undefined)
  assert.equal(store.claim('source-A:v1'), first)
  assert.equal(store.claim('source-A:v1'), undefined)
  assert.equal(first.snapshot.model, 'old')
  store.settle(first, true)
  assert.equal(store.draft('source-A:v1'), '继续输入')
  assert.equal(store.draft('source-B:v1'), 'B 草稿')
  assert.equal(store.draft('source-A:v2'), '')
  assert.equal(store.claim('source-A:v1')?.id, '2')
})

test('late rejection restores only untouched drafts and never overwrites another scope or newer edit', () => {
  const store = createScopedComposer<null>()
  store.setDraft('A', '原稿')
  store.enqueue('A', '1', '原稿', null, true)
  const first = store.claim('A')!
  store.setDraft('B', '新会话')
  store.settle(first, false)
  assert.equal(store.draft('A'), '原稿')
  assert.equal(store.draft('B'), '新会话')
  store.enqueue('A', '2', '原稿', null, true)
  const second = store.claim('A')!
  store.setDraft('A', '后来输入')
  store.setDraft('A', '')
  store.settle(second, false)
  assert.equal(store.draft('A'), '')
  assert.deepEqual(store.queue('A').map(item => item.content), ['原稿'])
  assert.equal(store.claim('A'), undefined)
  store.retry('A')
  const retry = store.claim('A')!
  assert.equal(retry.id, '2')
  store.settle(retry, false)
  store.cancelQueue('A')
  assert.deepEqual(store.queue('A'), [])
})

test('draft typed during source loading moves only to its resolved revision without replacing a saved draft', () => {
  const store = createScopedComposer<null>()
  store.setDraft('A:pending', '加载时输入')
  store.setDraft('A:v1', '已有草稿')
  store.setDraft('B:pending', '另一个来源')
  store.adoptDraft('A:pending', 'A:v1')
  store.adoptDraft('A:pending', 'A:v1')
  assert.equal(store.draft('A:v1'), '已有草稿\n加载时输入')
  assert.equal(store.draft('A:pending'), '')
  assert.equal(store.draft('B:pending'), '另一个来源')
  assert.equal(store.draft('A:v2'), '')
})

test('pending queue edits preserve identity, order and runtime; claimed items cannot be changed', () => {
  const store = createScopedComposer<{ model: string }>()
  store.setDraft('A', '输入框草稿')
  const first = store.enqueue('A', 'first', '第一条', { model: 'frozen' }, false)
  store.enqueue('A', 'second', '第二条', { model: 'other' }, false)
  assert.equal(store.updateQueued('B', 'first', null), false)
  assert.equal(store.updateQueued('A', 'second', { editing: true }), true)
  assert.equal(store.claim('A'), undefined, 'editing any entry holds the scope in FIFO order')
  assert.equal(store.updateQueued('A', 'second', { content: '  ' }), false)
  assert.equal(store.updateQueued('A', 'first', null), true)
  assert.equal(store.updateQueued('A', 'first', { content: '不能修改已删除项' }), false)
  store.updateQueued('A', 'second', { content: '修改后的第二条', editing: false })
  const claimed = store.claim('A')!
  assert.equal(claimed.id, 'second')
  assert.equal(claimed.content, '修改后的第二条')
  assert.deepEqual(claimed.snapshot, { model: 'other' })
  assert.equal(store.updateQueued('A', claimed.id, null), false)
  assert.equal(store.draft('A'), '输入框草稿')
  assert.equal(first.content, '第一条')
})
