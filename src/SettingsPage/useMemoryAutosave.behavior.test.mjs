import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('memory autosave separates model changes, validates drafts, flushes and reports failures', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const saved = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  let root
  try {
    const { useMemoryAutosave } = await vite.ssrLoadModule('/src/SettingsPage/useMemoryAutosave.ts')
    Object.assign(globalThis, { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true })
    const { createRoot } = await import('react-dom/client')
    const writes = []
    let fail = false
    const save = async patch => { if (fail) throw Error('offline'); writes.push(patch) }
    let hook
    function Harness({ modelId = '', embedding = null }) { hook = useMemoryAutosave(modelId, embedding, save); return null }
    root = createRoot(document.getElementById('root'))
    await act(async () => root.render(React.createElement(Harness)))
    assert.deepEqual(writes, [])
    await act(async () => hook.changeModel('other'))
    assert.deepEqual(writes, [{ modelId: 'other' }])
    await act(async () => hook.toggle(true))
    assert.equal(writes.length, 1)
    assert.match(hook.status, /未完整填写/)
    const valid = { apiProvider: 'openai', model: 'embed', apiKey: 'test', baseUrl: 'http://localhost:1', dimensions: 4 }
    await act(async () => hook.changeEmbedding(valid))
    await act(async () => root.render(React.createElement(Harness, { modelId: 'other' })))
    assert.equal(hook.draft.model, 'embed')
    assert.equal(hook.enabled, true)
    await act(async () => hook.flush())
    assert.deepEqual(writes.at(-1), { embedding: valid })
    await act(async () => hook.changeEmbedding({ ...valid, dimensions: 0 }))
    await act(async () => hook.flush())
    assert.equal(writes.length, 2)
    await act(async () => hook.changeEmbedding({ ...valid, model: 'pending' }))
    await act(async () => hook.toggle(false))
    await act(async () => hook.flush())
    assert.deepEqual(writes.at(-1), { embedding: null })
    assert.equal(writes.length, 3)
    await act(async () => hook.changeModel(''))
    assert.deepEqual(writes.at(-1), { modelId: '' })
    fail = true
    await act(async () => hook.changeModel('failed'))
    assert.match(hook.status, /保存失败/)
    fail = false
    await act(async () => hook.toggle(true))
    await act(async () => root.unmount())
    root = null
    assert.equal(writes.at(-1).embedding.model, 'pending')
  } finally {
    if (root) await act(async () => root.unmount())
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
    await vite.close()
  }
})
