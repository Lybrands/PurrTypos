import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

test('tool IO loads on expansion, merges late pages, and ignores responses from an old Run', async () => {
  await import('sass')
  const vite = await createServer({
    configFile: false,
    appType: 'custom',
    logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../', import.meta.url)) } },
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
    const revealText = async () => {
      await React.act(async () => {
        for (const element of window.document.querySelectorAll('.ai-dev-inspector__text-block')) {
          element.open = true
          element.dispatchEvent(new window.Event('toggle'))
        }
      })
    }
    await render('run-a', 'running')
    assert.equal(requests.length, 0)
    await open()
    assert.deepEqual(requests[0].args, { runId: 'run-a', after: 0 })
    await finish(0, {
      runId: 'run-a', nextCursor: 50, hasMore: true,
      calls: [{
        runId: 'run-a', toolCallId: 'call', eventRowId: 1,
        name: 'readScreenplayTaskDependencies',
        displayName: '读取第 1 集第 2 场已完成剧本',
        arguments: preview('ARG_A'),
      }],
    })
    assert.match(window.document.body.textContent, /读取第 1 集第 2 场已完成剧本/)
    assert.match(window.document.body.textContent, /readScreenplayTaskDependencies/)
    assert.doesNotMatch(window.document.body.textContent, /ARG_A/)
    assert.match(window.document.body.textContent, /未读取到返回记录/)
    assert.equal(window.document.querySelector('.ai-dev-inspector__tool').hasAttribute('open'), false)
    const argumentsPreview = window.document.querySelector('.ai-dev-inspector__tool .ai-dev-inspector__text-block')
    assert.equal(argumentsPreview.hasAttribute('open'), false)
    assert.match(argumentsPreview.querySelector('summary').textContent, /5 字符/)
    await revealText()
    assert.match(window.document.body.textContent, /ARG_A/)
    await React.act(async () => { window.document.querySelector('button').click() })
    assert.equal(requests[1].args.after, 50)
    await finish(1, {
      runId: 'run-a', nextCursor: 51, hasMore: false,
      calls: [{ runId: 'run-a', toolCallId: 'call', eventRowId: 51, status: 'completed', result: preview('RESULT_A') }],
    })
    await revealText()
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
    await revealText()
    assert.match(window.document.body.textContent, /RESULT_B/)
    assert.doesNotMatch(window.document.body.textContent, /RESULT_A|STALE_A/)
    const { default: Text, characterCount } = await vite.ssrLoadModule('/src/components/AiDevInspector/DiagnosticText.tsx')
    assert.equal(characterCount('场😀'), 2)
    await React.act(async () => { root.render(React.createElement(Text, { label: '输入', text: '场😀', characters: 123, truncated: true })) })
    assert.equal(window.document.querySelector('details').hasAttribute('open'), false)
    assert.equal(window.document.querySelector('pre'), null)
    await revealText()
    assert.match(window.document.body.textContent, /预览 2 字符/)
    const { ModelInputDiagnosticsCard, tokenUsageText } = await vite.ssrLoadModule('/src/components/AiDevInspector/index.tsx')
    assert.equal(tokenUsageText({ totalTokens: 18, inputTokens: 12, generationTokens: 6, complete: true, unreportedAttempts: 0 }), '输入 12 / 输出 6 Token')
    const originalInputs = services.ai.getAgentRunModelInputDiagnostics
    const inputRequests = []
    services.ai.getAgentRunModelInputDiagnostics = (args) => new Promise((resolve) => inputRequests.push({ args, resolve }))
    const restoreTools = restoreService
    restoreService = () => { restoreTools(); services.ai.getAgentRunModelInputDiagnostics = originalInputs }
    await React.act(async () => { root.render(React.createElement(ModelInputDiagnosticsCard, { runId: 'run-input', status: 'completed' })) })
    assert.equal(inputRequests.length, 0)
    await open()
    await React.act(async () => { inputRequests[0].resolve({ success: true, data: { calls: [
      { eventRowId: 1, captured: true, phase: 'planning', messages: [{ role: 'user', content: '场😀' }] },
      { eventRowId: 2, captured: false, phase: 'private', messages: [] },
    ] } }) })
    const inputRows = window.document.querySelectorAll('.ai-dev-inspector__planner-attempt')
    assert.equal(inputRows.length, 2)
    for (const row of inputRows) assert.equal(row.hasAttribute('open'), false)
    assert.match(inputRows[0].querySelector('summary').textContent, /1 条消息/)
    const messageDetails = inputRows[0].querySelector('.ai-dev-inspector__text-block')
    assert.equal(messageDetails.hasAttribute('open'), false)
    assert.match(messageDetails.querySelector('summary').textContent, /user/)
  } finally {
    restoreService()
    await React.act(async () => { root.unmount() })
    await vite.close()
    Object.assign(globalThis, previous)
  }
})


test('history replay stays closed, collapsed Runs mount no log bodies, and close unsubscribes the view', async () => {
  await import('sass')
  const vite = await createServer({ configFile: false, appType: 'custom', logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  window.innerWidth = 1440
  window.innerHeight = 900
  window.location = { protocol: 'http:', port: '18321', origin: 'http://127.0.0.1:18321' }
  const globals = { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true }
  const previous = Object.fromEntries(Object.keys(globals).map(key => [key, globalThis[key]]))
  Object.assign(globalThis, globals)
  const React = await import('react')
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(window.document.getElementById('root'))
  const intervals = new Map()
  const originalSetInterval = window.setInterval
  const originalClearInterval = window.clearInterval
  let intervalId = 0
  window.setInterval = callback => { intervals.set(++intervalId, callback); return intervalId }
  window.clearInterval = id => { intervals.delete(id) }
  let restoreService = () => {}
  try {
    const { default: Inspector } = await vite.ssrLoadModule('/src/components/AiDevInspector/index.tsx')
    const store = await vite.ssrLoadModule('/src/components/AiDevInspector/store.ts')
    const { services } = await vite.ssrLoadModule('/src/services/index.ts')
    const original = services.ai.getAgentRunPlannerDiagnostics
    let plannerRequests = 0
    services.ai.getAgentRunPlannerDiagnostics = async () => {
      plannerRequests++
      return { success: true, data: { outputs: [] } }
    }
    restoreService = () => { services.ai.getAgentRunPlannerDiagnostics = original }
    const { createAgentReplayPageCommit } = await vite.ssrLoadModule('/src/agent-runtime/chunkHandlers/commitScheduler.ts')
    for (const history of [true, false]) {
      let updates = 0
      const page = createAgentReplayPageCommit(history, () => updates++)
      for (let event = 0; event < 500; event++) {
        page.change()
        assert.equal(updates, history ? 0 : event + 1)
      }
      page.finish()
      page.finish()
      assert.equal(updates, history ? 1 : 500)
    }
    store.clearAiDebugRuns()
    store.setAiDebugInspectorVisible(false)
    let commits = 0
    await React.act(async () => root.render(React.createElement(React.Profiler,
      { id: 'inspector', onRender: () => commits++ }, React.createElement(Inspector))))
    const initialCommits = commits
    const replay = (turn, run, sequence) => store.recordAgentConversationDebugChunk({
      runId: `run-${turn}-${run}`, turnId: `turn-${turn}`, sessionId: 548,
      source: '剧本 Agent', prompt: '创作下一集',
      chunk: { eventId: `event-${turn}-${run}-${sequence}`, runId: `run-${turn}-${run}`,
        turnId: `turn-${turn}`, outputStreamId: null, invocationId: null, sequence,
        source: 'runtime', kind: 'runtime.event', channel: 'lifecycle', visibility: 'public',
        payload: { eventType: 'diagnostic.test', data: { text: 'HISTORY_LOG_BODY'.repeat(100) } },
        occurredAt: '2026-09-07T00:00:00Z', emittedAt: '2026-09-07T00:00:00Z' },
    })
    await React.act(async () => {
      for (let turn = 0; turn < 13; turn++) for (let run = 0; run < 17; run++) {
        for (let event = 0; event < 20; event++) replay(turn, run, event)
      }
      await new Promise(resolve => setTimeout(resolve, 10))
    })
    assert.equal(commits, initialCommits, 'history must not refresh the closed inspector')
    assert.equal(window.document.querySelector('.ai-dev-inspector'), null)
    assert.equal(intervals.size, 0)
    await React.act(async () => window.document.querySelector('[aria-label="打开 AI 对话诊断"]').click())
    assert.equal(window.document.querySelectorAll('.ai-dev-inspector__run-card').length, 17)
    assert.equal(window.document.querySelectorAll('.ai-dev-inspector__run-card-body').length, 0)
    assert.doesNotMatch(window.document.body.textContent, /HISTORY_LOG_BODY/)
    assert.equal(plannerRequests, 0, 'collapsed Planner must not fetch diagnostics')
    assert.equal(intervals.size, 1)
    const toggle = element => React.act(async () => {
      element.open = !element.open
      element.dispatchEvent(new window.Event('toggle'))
    })
    const card = window.document.querySelector('.ai-dev-inspector__run-card')
    await toggle(card)
    assert.equal(window.document.querySelectorAll('.ai-dev-inspector__run-card-body').length, 1)
    assert.equal(window.document.querySelector('.ai-dev-inspector__technical-body'), null)
    await toggle(window.document.querySelector('.ai-dev-inspector__technical'))
    assert.match(window.document.body.textContent, /HISTORY_LOG_BODY/)
    await toggle(card)
    assert.equal(window.document.querySelector('.ai-dev-inspector__technical-body'), null)
    await React.act(async () => window.document.querySelector('[aria-label="折叠调试面板"]').click())
    assert.equal(intervals.size, 0)
    await React.act(async () => window.document.querySelector('[aria-label="展开调试面板"]').click())
    await React.act(async () => window.document.querySelector('[aria-label="关闭 AI 对话诊断"]').click())
    assert.equal(intervals.size, 0)
    const closedCommits = commits
    await React.act(async () => {
      replay(12, 16, 21)
      replay(13, 0, 1)
      await new Promise(resolve => setTimeout(resolve, 10))
    })
    assert.equal(commits, closedCommits)
    assert.equal(window.document.querySelector('.ai-dev-inspector'), null)
    await React.act(async () => root.render(null))
    await React.act(async () => root.render(React.createElement(Inspector)))
    assert.equal(window.document.querySelector('.ai-dev-inspector'), null, 'remount must retain dismissal')
    await React.act(async () => store.startAiDebugRun('new-user-task', {
      streamId: 'new-user-task', apiKey: '', messages: [{ role: 'user', content: '新任务' }],
    }))
    assert.ok(window.document.querySelector('.ai-dev-inspector'), 'a new submitted task may open diagnostics')
  } finally {
    restoreService()
    await React.act(async () => root.unmount())
    window.setInterval = originalSetInterval
    window.clearInterval = originalClearInterval
    await vite.close()
    Object.assign(globalThis, previous)
  }
})
