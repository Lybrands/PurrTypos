import assert from 'node:assert/strict'
import { after, before, beforeEach, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// node --test 环境没有 localStorage；用最小 shim 覆盖 getItem/setItem。
const store = new Map()
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => { store.set(key, String(value)) },
  removeItem: (key) => { store.delete(key) },
}

let vite
let useChatScopeMemory
let loadChatScopeMap
let rememberChatScope

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({
    useChatScopeMemory,
    loadChatScopeMap,
    rememberChatScope,
  } = await vite.ssrLoadModule(
    '/src/Workspace/AiPanel/hooks/useChatScopeMemory.ts',
  ))
})

after(async () => {
  delete globalThis.localStorage
  await vite?.close()
})

beforeEach(() => {
  store.clear()
})

/** 单次渲染读取 hook 初始值（恢复语义发生在 useState 初始化里） */
function renderHook(bookId, chapterId) {
  let result
  function Harness() {
    result = useChatScopeMemory(bookId, chapterId)
    return React.createElement('div')
  }
  renderToStaticMarkup(React.createElement(Harness))
  return result
}

test('手动切换的作用域按书记忆并可跨挂载恢复', () => {
  rememberChatScope('book-a', 'setting')
  rememberChatScope('book-b', 'chapter')

  assert.equal(loadChatScopeMap()['book-a'], 'setting')
  assert.equal(loadChatScopeMap()['book-b'], 'chapter')

  // 再次进入工作台（重新挂载）：恢复该书上次的手动选择
  assert.equal(renderHook('book-a', 'chapter-1').chatScope, 'setting')
  assert.equal(renderHook('book-b', 'chapter-1').chatScope, 'chapter')
  // 没有记忆过的书默认章节作用域
  assert.equal(renderHook('book-c', 'chapter-1').chatScope, 'chapter')
  // bookId 未知时不猜测
  assert.equal(renderHook(null, null).chatScope, 'chapter')
})

test('handleChatScopeChange 写入记忆，手动切回章节同样落盘', () => {
  const first = renderHook('book-a', 'chapter-1')
  assert.equal(first.chatScope, 'chapter')

  first.handleChatScopeChange('setting')
  assert.equal(loadChatScopeMap()['book-a'], 'setting')
  assert.equal(renderHook('book-a', 'chapter-1').chatScope, 'setting')

  const second = renderHook('book-a', 'chapter-1')
  second.handleChatScopeChange('chapter')
  assert.equal(loadChatScopeMap()['book-a'], 'chapter')
  assert.equal(renderHook('book-a', 'chapter-1').chatScope, 'chapter')
})

test('损坏的存储内容回退为空映射而不是抛错', () => {
  store.set('purrtypos_chat_scope_by_book', '{not-json')
  assert.deepEqual(loadChatScopeMap(), {})
  assert.equal(renderHook('book-a', 'chapter-1').chatScope, 'chapter')
})
