import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('current model persists by book, follows switching, and restores on remount', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const keys = ['window', 'document', 'localStorage', 'IS_REACT_ACT_ENVIRONMENT']
  const saved = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  const storage = new Map()
  let root
  try {
    window.location = { protocol: 'http:', hostname: 'localhost', port: '5173' }
    Object.assign(globalThis, { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true,
      localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) } })
    const { useAiModelPrefs } = await vite.ssrLoadModule('/src/Workspace/AiPanel/hooks/useAiModelPrefs.ts')
    const { services } = await vite.ssrLoadModule('/src/services/index.ts')
    const settings = {}
    const writes = []
    services.settings.setSettings = async data => { writes.push(data); Object.assign(settings, data); return { success: true } }
    const { createRoot } = await import('react-dom/client')
    const models = [{ id: 'a' }, { id: 'b' }]
    let hook
    function Harness({ bookId }) { hook = useAiModelPrefs(bookId, models); return null }
    root = createRoot(document.getElementById('root'))
    await act(async () => root.render(React.createElement(Harness, { bookId: 'one' })))
    assert.equal(settings['writing_current_model:one'], 'a')
    await act(async () => hook.setSelectedModel('b'))
    assert.equal(settings['writing_current_model:one'], 'b')
    await act(async () => root.render(React.createElement(Harness, { bookId: 'two' })))
    assert.equal(hook.selectedModel, 'a')
    assert.equal(settings['writing_current_model:two'], 'a')
    assert.ok(!writes.some(data => data['writing_current_model:two'] === 'b'))
    await act(async () => root.render(React.createElement(Harness, { bookId: 'one' })))
    assert.equal(hook.selectedModel, 'b')
    await act(async () => root.unmount())
    root = createRoot(document.getElementById('root'))
    await act(async () => root.render(React.createElement(Harness, { bookId: 'one' })))
    assert.equal(hook.selectedModel, 'b')
    assert.equal(settings['writing_current_model:one'], 'b')
  } finally {
    if (root) await act(async () => root.unmount())
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
    await vite.close()
  }
})
