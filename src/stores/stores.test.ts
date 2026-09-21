import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  __resetQuoteStoreForTests,
  addSelectionQuote,
  useQuoteStore,
} from './quoteStore.ts'
import {
  __resetSettingsInvalidationStoreForTests,
  notifySettingsUpdated,
  useSettingsInvalidationStore,
} from './settingsInvalidationStore.ts'
import {
  __resetChatContextStoreForTests,
  useChatContextStore,
  writeAssociatedContext,
  writeMemorySelection,
} from './chatContextStore.ts'
import {
  __resetAnnotationsStoreForTests,
  bumpAnnotationsRevision,
  useAnnotationsStore,
} from './annotationsStore.ts'
import {
  __resetAiProposalBridgeForTests,
  proposeSettingDiff,
  useAiProposalBridge,
} from './aiProposalBridge.ts'
import {
  __resetWorkspaceStoreForTests,
  notifyChapterContentUpdated,
  useWorkspaceStore,
} from './workspaceStore.ts'

test('quoteStore 去重与逐条删除', () => {
  __resetQuoteStoreForTests()
  addSelectionQuote('第一处', '第一章')
  addSelectionQuote('第一处', '第一章') // 同章节同文本去重
  addSelectionQuote('第二处', '第二章')
  assert.deepEqual(
    useQuoteStore.getState().quotes,
    [
      { quote: '第一处', chapterTitle: '第一章' },
      { quote: '第二处', chapterTitle: '第二章' },
    ],
  )
  useQuoteStore.getState().removeAt(0)
  assert.deepEqual(useQuoteStore.getState().quotes, [
    { quote: '第二处', chapterTitle: '第二章' },
  ])
  useQuoteStore.getState().removeAll()
  assert.equal(useQuoteStore.getState().quotes.length, 0)
})

test('settingsInvalidation 按 kind 递增且全局 seq 同步', () => {
  __resetSettingsInvalidationStoreForTests()
  notifySettingsUpdated('character', { id: 3, name: '主角' })
  notifySettingsUpdated('background')
  const state = useSettingsInvalidationStore.getState()
  assert.equal(state.revisions.character, 1)
  assert.equal(state.revisions.background, 1)
  assert.equal(state.revisions.entity, 0)
  assert.equal(state.seq, 2)
  assert.deepEqual(state.lastUpdate, { kind: 'background', id: undefined, name: undefined })
})

test('chatContextStore 双入口共享（弹层写入，主面板立即可见）', () => {
  __resetChatContextStoreForTests()
  writeMemorySelection('book-1', {
    longTerm: ['m1'],
    memory: [7],
    foreshadowing: [],
  })
  assert.deepEqual(
    useChatContextStore.getState().byBook['book-1'].memorySelection,
    { longTerm: ['m1'], memory: [7], foreshadowing: [] },
  )
  writeAssociatedContext('book-1', { chapters: ['ch-1'], outlines: [] })
  assert.deepEqual(
    useChatContextStore.getState().byBook['book-1'].associatedContext,
    { chapters: ['ch-1'], outlines: [] },
  )
  // 未动过的书不受影响
  assert.equal(useChatContextStore.getState().byBook['book-2'], undefined)
})

test('annotationsStore 按章 bump 互不干扰', () => {
  __resetAnnotationsStoreForTests()
  bumpAnnotationsRevision('b', 'c1')
  bumpAnnotationsRevision('b', 'c1')
  bumpAnnotationsRevision('b', 'c2')
  const revision = useAnnotationsStore.getState().revision
  assert.equal(revision['b::c1'], 2)
  assert.equal(revision['b::c2'], 1)
})

test('aiProposalBridge 队列排空保序且一次性', () => {
  __resetAiProposalBridgeForTests()
  proposeSettingDiff({ proposalId: 'a', kind: 'background' } as never)
  proposeSettingDiff({ proposalId: 'b', kind: 'background' } as never)
  assert.equal(useAiProposalBridge.getState().settingDiffQueue.length, 2)
  const drained = useAiProposalBridge.getState().drainSettingDiff()
  assert.deepEqual(
    drained.map((p) => p.proposalId),
    ['a', 'b'],
  )
  // 排空后队列清空，二次排空为空
  assert.equal(useAiProposalBridge.getState().drainSettingDiff().length, 0)
})

test('workspaceStore 章节内容修订号按章递增', () => {
  __resetWorkspaceStoreForTests()
  notifyChapterContentUpdated('ch-9')
  notifyChapterContentUpdated('ch-9')
  notifyChapterContentUpdated('ch-8')
  const state = useWorkspaceStore.getState()
  assert.equal(state.chapterContentRevision['ch-9'], 2)
  assert.equal(state.chapterContentRevision['ch-8'], 1)
})
