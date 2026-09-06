import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

const model = {
  id: 'model-1',
  name: 'model-1',
  supportsThinking: false,
  thinkingOnly: false,
  apiKey: 'test',
  baseUrl: '',
}

test('mounted A to B hydration blocks Enter and retires the A editor identity', async () => {
  const vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    plugins: [{
      name: 'mounted-panel-virtuoso-test-double',
      enforce: 'pre',
      resolveId(id) {
        return id === 'react-virtuoso' ? '\0mounted-panel-virtuoso' : null
      },
      load(id) {
        if (id !== '\0mounted-panel-virtuoso') return null
        return `
          import React from 'react'
          export const Virtuoso = React.forwardRef(function TestVirtuoso(props, ref) {
            const scroller = React.useRef(null)
            React.useImperativeHandle(ref, () => ({ scrollToIndex() {} }), [])
            React.useEffect(() => {
              props.scrollerRef?.(scroller.current)
              return () => props.scrollerRef?.(null)
            }, [props.scrollerRef])
            return React.createElement('div', { ref: scroller, 'data-testid': 'virtuoso' },
              props.data.map((item, index) => React.createElement(
                React.Fragment,
                { key: props.computeItemKey?.(index, item) ?? index },
                props.itemContent(index, item),
              )),
            )
          })
        `
      },
    }],
    ssr: { noExternal: ['react-virtuoso'] },
    server: { middlewareMode: true },
  })
  // Sass probes for a browser global at module initialization; prime its Node
  // implementation before installing linkedom, then load React DOM with the
  // mounted document already present.
  await import('sass')
  const React = await import('react')
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = {
    window: globalThis.window,
    Window: globalThis.Window,
    document: globalThis.document,
    Event: globalThis.Event,
    Element: globalThis.Element,
    HTMLElement: globalThis.HTMLElement,
    Node: globalThis.Node,
    MutationObserver: globalThis.MutationObserver,
    ResizeObserver: globalThis.ResizeObserver,
    getComputedStyle: globalThis.getComputedStyle,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
  }
  Object.assign(globalThis, {
    window,
    Window: window.constructor,
    document: window.document,
    Event: window.Event,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    Node: window.Node,
    MutationObserver: window.MutationObserver,
    getComputedStyle: () => ({ lineHeight: '22px' }),
    requestAnimationFrame: (callback) => window.setTimeout(callback, 0),
    cancelAnimationFrame: (id) => window.clearTimeout(id),
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  window.document.oninput = null
  Object.defineProperty(window.HTMLElement.prototype, 'getBoundingClientRect', {
    configurable: true,
    value() {
      return {
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 800,
        bottom: 600,
        width: 800,
        height: 600,
      }
    },
  })
  class ResizeObserver {
    constructor(callback) {
      this.callback = callback
      this.observed = new WeakSet()
    }
    observe(target) {
      if (this.observed.has(target)) return
      this.observed.add(target)
      window.setTimeout(() => {
        this.callback([{ target, contentRect: target.getBoundingClientRect() }])
      }, 0)
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserver
  window.ResizeObserver = ResizeObserver

  const { default: AgentConversationPanel } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Panel.tsx',
  )
  const { createScreenplayConversationController } = await vite.ssrLoadModule(
    '/src/ScreenplayAgentPage/useScreenplayConversationController.ts',
  )
  const { createScreenplayConversationSessionLifecycle } = await vite.ssrLoadModule(
    '/src/ScreenplayAgentPage/screenplayConversationSessionLifecycle.ts',
  )
  const { createRoot } = await import('react-dom/client')
  const lifecycle = createScreenplayConversationSessionLifecycle()
  let sends = 0
  const edits = []
  const sessions = [
    { id: 1, title: 'A', create_time: '2026-08-13 01:00:00' },
    { id: 2, title: 'B', create_time: '2026-08-13 01:01:00' },
  ]
  const controller = (token, initializing, messages) => (
    createScreenplayConversationController({
      project: { id: 'project-1', title: '剧本', status: 'active' },
      sessions,
      activeSessionId: token.sessionId,
      conversationIdentity: token.identity,
      messages,
      activities: {},
      queuedSubmissions: [],
      prompt: 'B draft',
      setPrompt: () => undefined,
      initializing,
      running: false,
      stopping: false,
      paused: false,
      resuming: false,
      modelConfigs: [model],
      selectedModelId: model.id,
      setSelectedModelId: () => undefined,
      openModelSettings: () => undefined,
      actions: {
      selectSession: () => undefined,
      createSession: () => undefined,
      closeSession: () => undefined,
      renameSession: () => undefined,
      send: () => { if (lifecycle.canAct(token)) sends += 1 },
      abort: () => undefined,
      resume: () => undefined,
      editMessage: (index, content) => {
        if (lifecycle.canAct(token)) edits.push({ index, content })
      },
      resolveToolApproval: async () => ({ success: true }),
      onSubmitErrorReport: async () => ({ success: true }),
      },
    })
  )
  let resolveBHydration
  const bHydration = new Promise((resolve) => {
    resolveBHydration = resolve
  })
  const a = lifecycle.beginLoad('project-1', 1)
  lifecycle.finishLoad(a)
  const aMessages = [
    { role: 'user', content: 'A question', clientTurnId: 'turn-a' },
    { role: 'assistant', content: 'A answer', clientTurnId: 'turn-a' },
  ]
  const bMessages = [
    { role: 'user', content: 'B question', clientTurnId: 'turn-b' },
    { role: 'assistant', content: 'B answer', clientTurnId: 'turn-b' },
  ]
  const root = createRoot(window.document.getElementById('root'))
  const act = React.act
  const keydown = (target, fields) => {
    const event = new window.Event('keydown', { bubbles: true, cancelable: true })
    for (const [key, value] of Object.entries(fields)) {
      Object.defineProperty(event, key, { value })
    }
    target.dispatchEvent(event)
  }
  try {
    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false,
        controller: controller(a, false, aMessages),
      }))
      await new Promise((resolve) => window.setTimeout(resolve, 10))
    })
    const aEditButton = window.document.querySelector('button[aria-label="编辑提问"]')
    assert.ok(aEditButton, window.document.body.innerHTML)
    await act(async () => aEditButton.dispatchEvent(
      new window.Event('click', { bubbles: true }),
    ))
    const retiredAEditor = window.document.querySelector(
      'textarea[aria-label="编辑历史提问"]',
    )
    assert.ok(retiredAEditor)

    const b = lifecycle.beginLoad('project-1', 2)
    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false,
        controller: controller(b, true, []),
      }))
    })
    assert.equal(
      window.document.querySelector('textarea[aria-label="编辑历史提问"]'),
      null,
    )
    const composer = window.document.querySelector(
      'textarea[aria-label="输入希望剧本 Agent 完成的任务"]',
    )
    await act(async () => {
      keydown(composer, { key: 'Enter', shiftKey: false, isComposing: false })
      window.document.querySelector('button[aria-label="发送"]')
        .dispatchEvent(new window.Event('click', { bubbles: true }))
    })
    assert.equal(sends, 0)
    retiredAEditor.dispatchEvent(new window.Event('keydown', { bubbles: true }))
    assert.deepEqual(edits, [])

    await act(async () => {
      resolveBHydration()
      await bHydration
      lifecycle.finishLoad(b)
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false,
        controller: controller(b, false, bMessages),
      }))
      await new Promise((resolve) => window.setTimeout(resolve, 10))
    })
    await act(async () => keydown(
        window.document.querySelector(
          'textarea[aria-label="输入希望剧本 Agent 完成的任务"]',
        ),
        { key: 'Enter', shiftKey: false, isComposing: false },
      ))
    assert.equal(sends, 1)
    for (const state of ['initializing', 'stopping', 'paused', 'resuming', 'missing-model', 'missing-session', 'read-only']) {
      const blocked = controller(b, false, bMessages)
      blocked.composer.submitDisabled = false
      if (state === 'missing-model') blocked.composer.selectedModel = null
      else if (state === 'missing-session') blocked.conversation.activeSessionId = null
      else if (state === 'read-only') blocked.capabilities.inputDisabled = true
      else blocked.conversation[state] = true
      if (state === 'initializing') blocked.conversation.running = true
      await act(async () => root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false, controller: blocked,
      })))
      await act(async () => {
        keydown(window.document.querySelector('textarea.agent-composer__input'),
          { key: 'Enter', shiftKey: false, isComposing: false })
        window.document.querySelector('button[aria-label="发送"]')
          .dispatchEvent(new window.Event('click', { bubbles: true }))
      })
      assert.equal(sends, 1, `${state}: Enter and click must use the same admission`)
      if (state === 'initializing') assert.equal(
        window.document.querySelector('button[aria-label="停止生成"]').disabled, true,
        'loading the next scope must not allow stopping a stale run',
      )
    }
    await act(async () => root.render(React.createElement(AgentConversationPanel, {
      indexOpen: false, controller: controller(b, false, bMessages),
    })))
    await act(async () => {
      const input = window.document.querySelector('textarea.agent-composer__input')
      keydown(input, { key: 'Enter', shiftKey: true, isComposing: false })
      keydown(input, { key: 'Enter', shiftKey: false, isComposing: true })
    })
    assert.equal(sends, 1, 'newline and IME confirmation must not submit')
    const held = controller(b, false, bMessages)
    let retries = 0
    let clears = 0
    held.conversation.queuePaused = true
    held.conversation.queuedSubmissions = [{ id: 'q', sessionId: 2, content: '待重试' }]
    held.actions.retryQueued = () => { retries += 1 }
    held.actions.clearQueued = () => { clears += 1 }
    await act(async () => root.render(React.createElement(AgentConversationPanel, {
      indexOpen: false, controller: held,
    })))
    assert.match(window.document.querySelector('[aria-label="待发送消息"]').textContent, /队列已暂停/)
    await act(async () => {
      const buttons = [...window.document.querySelectorAll('[aria-label="待发送消息"] button')]
      buttons.find(button => button.textContent === '重试发送').click()
      buttons.find(button => button.textContent === '清空队列').click()
    })
    assert.equal(retries, 1)
    assert.equal(clears, 1)
    const { changeQueuedSubmission } = await vite.ssrLoadModule('/src/agent-runtime/queuedSubmission.ts')
    let queue = ['one', 'two', 'three', 'four'].map(id => ({ id, sessionId: 2, content: id }))
    let queueVisible = true
    const drawQueue = () => {
      const current = controller(b, false, bMessages)
      current.conversation.queuedSubmissions = queue
      current.actions.updateQueuedSubmission = (id, patch) => {
        const next = changeQueuedSubmission(queue, id, patch, item => item.sessionId === 2)
        if (next === queue) return false
        queue = next
        if (queueVisible) drawQueue()
        return true
      }
      root.render(React.createElement(AgentConversationPanel, { indexOpen: false, controller: current }))
    }
    await act(async () => drawQueue())
    assert.equal(window.document.querySelectorAll('[data-queue-id]').length, 4)
    await act(async () => window.document.querySelector('button[aria-label="编辑待发送消息 4"]').click())
    assert.equal(queue[3].editing, true)
    const queueInput = window.document.querySelector('textarea[aria-label="编辑待发送消息 4"]')
    const changeInput = value => {
      Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(queueInput, value)
      queueInput.dispatchEvent(new window.Event('input', { bubbles: true }))
    }
    await act(async () => changeInput(''))
    assert.equal([...window.document.querySelectorAll('[data-queue-id="four"] button')]
      .find(button => button.textContent === '保存').disabled, true)
    await act(async () => changeInput('edited four'))
    await act(async () => keydown(queueInput, { key: 'Enter', isComposing: true }))
    assert.equal(queue[3].content, 'four')
    await act(async () => keydown(queueInput, { key: 'Enter', isComposing: false, shiftKey: false }))
    assert.equal(queue[3].content, 'edited four')
    assert.equal(queue[3].editing, false)
    await act(async () => window.document.querySelector('button[aria-label="删除待发送消息 1"]').click())
    assert.deepEqual(queue.map(item => item.id), ['two', 'three', 'four'])
    await act(async () => window.document.querySelector('button[aria-label="编辑待发送消息 1"]').click())
    await act(async () => keydown(window.document.querySelector('textarea[aria-label="编辑待发送消息 1"]'), { key: 'Escape' }))
    assert.equal(queue[0].content, 'two')
    assert.equal(queue[0].editing, false)
    await act(async () => window.document.querySelector('button[aria-label="编辑待发送消息 1"]').click())
    // Switching the conversation releases the old queue edit without copying its draft.
    queueVisible = false
    await act(async () => root.render(React.createElement(AgentConversationPanel, {
      indexOpen: false, controller: controller(a, false, aMessages),
    })))
    assert.equal(queue[0].editing, false)
    await act(async () => root.render(React.createElement(AgentConversationPanel, {
      indexOpen: false, controller: controller(b, false, bMessages),
    })))
    const bEditButton = window.document.querySelector('button[aria-label="编辑提问"]')
    await act(async () => bEditButton.dispatchEvent(
      new window.Event('click', { bubbles: true }),
    ))
    const bEditor = window.document.querySelector('textarea[aria-label="编辑历史提问"]')
    await act(async () => keydown(
      bEditor,
      { key: 'Enter', shiftKey: false, isComposing: false },
    ))
    assert.deepEqual(edits, [{ index: 0, content: 'B question' }])
    const { default: AssistantOutput } = await vite.ssrLoadModule('/src/components/AgentConversation/AssistantOutput/index.tsx')
    const { initialCanonicalOutputState } = await vite.ssrLoadModule('/src/agent-runtime/canonicalOutput.ts')
    const description = '已读取指定材料，现提交分析候选。'
    const standbyMessage = { role: 'assistant', content: '', canonicalOutput: {
      ...initialCanonicalOutputState(),
      commentaryBlocks: [{ outputStreamId: 'description', invocationId: 'current', text: description,
        firstSequence: 2, lastSequence: 3, startedAt: '2026-09-06T09:00:00Z', committed: false, aborted: false }],
      operationOrder: ['model'], operations: { model: { operationId: 'model', runId: 'child', invocationId: 'current',
        kind: 'model', firstSequence: 1, status: 'running', startedAt: '2026-09-06T09:00:00Z',
        display: { labelParams: {} } } },
    } }
    const renderStandby = message => root.render(React.createElement(AssistantOutput, {
      index: 0, message, loading: true, isLastAssistant: true, showPlaceholder: false,
      setScrolledUpByReason() {}, onResolveToolApproval: async () => ({ success: true }),
    }))
    await act(async () => renderStandby(standbyMessage))
    assert.equal(window.document.querySelector('.work-log__commentary').textContent, description)
    await act(async () => new Promise(resolve => window.setTimeout(resolve, 1100)))
    assert.equal(window.document.querySelector('.bubble-processing-standby').textContent, '正在思考')
    assert.equal(window.document.body.textContent.split(description).length - 1, 1)
    const continued = description + '继续补充公开说明。'
    const next = { ...standbyMessage, canonicalOutput: { ...standbyMessage.canonicalOutput,
      commentaryBlocks: [{ ...standbyMessage.canonicalOutput.commentaryBlocks[0], text: continued, lastSequence: 4 }],
    } }
    await act(async () => renderStandby(next))
    assert.equal(window.document.querySelector('.work-log__commentary').textContent, continued,
      'real public text updates immediately, independently of the standby timer')
    await act(async () => new Promise(resolve => window.setTimeout(resolve, 1100)))
    assert.equal(window.document.querySelector('.bubble-processing-standby').textContent, '正在思考')
    assert.equal(window.document.body.textContent.split(continued).length - 1, 1)
    await act(async () => renderStandby({ ...next, canonicalOutput: { ...next.canonicalOutput, runTerminal: true } }))
    assert.equal(window.document.querySelector('.bubble-processing-standby'), null,
      'a terminal run must not keep a standby label even if the host loading flag lags')
  } finally {
    await act(async () => root.unmount())
    await vite.close()
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})
