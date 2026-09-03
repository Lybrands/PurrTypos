import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('tool IO loads on expansion, merges late pages, and ignores responses from an old Run', async () => {
  const vite = await createServer({
    configFile: false,
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true, hmr: false },
  })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  window.location = { protocol: 'http:', port: '18321', origin: 'http://127.0.0.1:18321' }
  const globals = { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true }
  const previous = Object.fromEntries(Object.keys(globals).map((key) => [key, globalThis[key]]))
  Object.assign(globalThis, globals)
  const React = await import('react')
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(window.document.getElementById('root'))
  let restoreService = () => {}
  try {
    const { default: Card } = await vite.ssrLoadModule('/src/components/AiDevInspector/ToolDiagnosticsCard.tsx')
    const { services } = await vite.ssrLoadModule('/src/services/index.ts')
    const original = services.ai.getAgentRunToolDiagnostics
    restoreService = () => { services.ai.getAgentRunToolDiagnostics = original }
    const requests = []
    services.ai.getAgentRunToolDiagnostics = (args) => new Promise((resolve) => {
      requests.push({ args, resolve })
    })
    const render = (runId, revision) => React.act(async () => {
      root.render(React.createElement(Card, { key: runId, runId, revision }))
    })
    const open = () => React.act(async () => {
      const details = window.document.querySelector('details')
      details.open = true
      details.dispatchEvent(new window.Event('toggle'))
    })
    const finish = (index, data) => React.act(async () => {
      requests[index].resolve({ success: true, data })
    })
    const preview = (text) => ({ text, characters: text.length, truncated: false })
    await render('run-a', 'running')
    assert.equal(requests.length, 0)
    await open()
    assert.deepEqual(requests[0].args, { runId: 'run-a', after: 0 })
    await finish(0, {
      runId: 'run-a', nextCursor: 50, hasMore: true,
      calls: [{ runId: 'run-a', toolCallId: 'call', eventRowId: 1, name: 'readA', arguments: preview('ARG_A') }],
    })
    assert.match(window.document.body.textContent, /ARG_A/)
    assert.match(window.document.body.textContent, /未读取到返回记录/)
    await React.act(async () => { window.document.querySelector('button').click() })
    assert.equal(requests[1].args.after, 50)
    await finish(1, {
      runId: 'run-a', nextCursor: 51, hasMore: false,
      calls: [{ runId: 'run-a', toolCallId: 'call', eventRowId: 51, status: 'completed', result: preview('RESULT_A') }],
    })
    assert.match(window.document.body.textContent, /RESULT_A/)
    assert.match(window.document.body.textContent, /ARG_A/)
    assert.equal(window.document.querySelectorAll('.ai-dev-inspector__tool').length, 1)
    await render('run-a', 'completed')
    assert.equal(requests.length, 3)
    await render('run-b', 'running')
    await finish(2, {
      runId: 'run-a', nextCursor: 52, hasMore: false,
      calls: [{ runId: 'run-a', toolCallId: 'stale', eventRowId: 52, result: preview('STALE_A') }],
    })
    assert.doesNotMatch(window.document.body.textContent, /ARG_A|RESULT_A|STALE_A/)
    await open()
    assert.deepEqual(requests[3].args, { runId: 'run-b', after: 0 })
    await React.act(async () => {
      requests[3].resolve({ success: false, error: '工具调用诊断只在开发环境启用' })
    })
    assert.match(window.document.body.textContent, /只在开发环境启用/)
    await React.act(async () => { window.document.querySelector('button').click() })
    await finish(4, {
      runId: 'run-b', nextCursor: 53, hasMore: false,
      calls: [{ runId: 'run-b', toolCallId: 'call', eventRowId: 53, result: preview('RESULT_B') }],
    })
    assert.match(window.document.body.textContent, /RESULT_B/)
    assert.doesNotMatch(window.document.body.textContent, /RESULT_A|STALE_A/)
  } finally {
    restoreService()
    await React.act(async () => { root.unmount() })
    await vite.close()
    Object.assign(globalThis, previous)
  }
})
