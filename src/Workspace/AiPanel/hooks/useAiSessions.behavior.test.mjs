import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

let vite
let useAiSessions

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ useAiSessions } = await vite.ssrLoadModule(
    '/src/Workspace/AiPanel/hooks/useAiSessions.ts',
  ))
})

after(async () => {
  delete globalThis.document
  await vite?.close()
})

const jsonResponse = (payload) => new Response(JSON.stringify(payload), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
})

function renderSessionsHook(overrides = {}) {
  let result
  let conversations = overrides.conversations ?? []
  const params = {
    bookId: 'book-a',
    chapterId: 'chapter-a',
    scope: 'chapter',
    conversations,
    setConversations: (next) => {
      conversations = typeof next === 'function' ? next(conversations) : next
    },
    setLoading: () => undefined,
    ...overrides,
  }
  function Harness() {
    result = useAiSessions(params)
    return React.createElement('div')
  }
  renderToStaticMarkup(React.createElement(Harness))
  return { result, readConversations: () => conversations }
}

test('handleNewSession performs the real createSession step and returns the id', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const created = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input, init) => {
    const url = String(input instanceof Request ? input.url : input)
    if (url.endsWith('/api/sessions') && init?.method === 'POST') {
      created.push(JSON.parse(init.body))
      return jsonResponse({
        success: true,
        data: { id: 9, book_id: 'book-a', chapter_id: 'chapter-a', scope: 'chapter' },
      })
    }
    throw new Error(`unexpected URL ${url}`)
  }
  try {
    const { result } = renderSessionsHook()
    const id = await result.handleNewSession()
    assert.equal(id, 9, 'create step must resolve with the new session id')
    assert.deepEqual(created, [{
      bookId: 'book-a',
      chapterId: 'chapter-a',
    }])
    // activeSessionId 的更新发生在 setState 之后，静态渲染不重绘；
    // 发送链路使用返回值 id，不依赖该状态同步可见（见 useChatSubmit 测试）。
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('create step fails loudly upstream when the API rejects', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => jsonResponse({ success: false, error: 'boom' })
  try {
    const { result } = renderSessionsHook()
    const id = await result.handleNewSession()
    assert.equal(id, null, 'failed create resolves null so the caller can warn')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('toggleSessionPinned persists the optimistic flag through the sessions API', async () => {
  globalThis.document = { documentElement: { lang: 'zh-CN' } }
  const puts = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input, init) => {
    const url = String(input instanceof Request ? input.url : input)
    if (url.endsWith('/api/sessions/5/pinned') && init?.method === 'PUT') {
      puts.push(JSON.parse(init.body))
      return jsonResponse({ success: true })
    }
    throw new Error(`unexpected URL ${url}`)
  }
  try {
    const { result } = renderSessionsHook()
    await Promise.resolve(result.handleToggleSessionPinned(5, true))
    assert.deepEqual(puts, [{ pinned: true }])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('last visited session memory survives reload via localStorage', async () => {
  const store = new Map()
  const originalStorage = globalThis.localStorage
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => { store.set(key, String(value)) },
    removeItem: (key) => { store.delete(key) },
  }
  try {
    const module = await vite.ssrLoadModule(
      '/src/Workspace/AiPanel/hooks/useAiSessions.ts',
    )
    const { rememberActiveSession, loadRememberedSessionMap } = module
    rememberActiveSession('book-a-chapter-a', 7)
    rememberActiveSession('book-a-__setting__', 12)
    assert.deepEqual(loadRememberedSessionMap(), {
      'book-a-chapter-a': 7,
      'book-a-__setting__': 12,
    })
    // 损坏内容回退为空映射
    store.set('purrtypos_active_session_by_scope', '{bad-json')
    assert.deepEqual(loadRememberedSessionMap(), {})
  } finally {
    delete globalThis.localStorage
    if (originalStorage) globalThis.localStorage = originalStorage
  }
})
