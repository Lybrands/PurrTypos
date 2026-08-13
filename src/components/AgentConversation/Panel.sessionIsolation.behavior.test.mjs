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
  const { createRoot } = await import('react-dom/client')
  let sends = 0
  const edits = []
  const baseController = {
    capabilities: {
      inputDisabled: false,
      sessionNavigationDisabled: false,
      submitMode: 'send',
    },
    composer: {
      value: 'B draft',
      setValue: () => undefined,
      placeholder: '输入任务',
      ariaLabel: '输入任务',
      submitDisabled: false,
      selectedModel: model,
      modelConfigs: [],
      selectModel: () => undefined,
      openModelSettings: () => undefined,
    },
    actions: {
      selectSession: () => undefined,
      createSession: () => undefined,
      closeSession: () => undefined,
      renameSession: () => undefined,
      send: () => { sends += 1 },
      abort: () => undefined,
      editMessage: (index, content) => { edits.push({ index, content }) },
      resolveToolApproval: async () => ({ success: true }),
    },
  }
  const conversation = (identity, sessionId, initializing, messages) => ({
    identity,
    sessions: [{ id: 1, title: 'A' }, { id: 2, title: 'B' }],
    activeSessionId: sessionId,
    messages,
    activities: {},
    queuedSubmissions: [],
    initializing,
    running: false,
    stopping: false,
    paused: false,
    resuming: false,
  })
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
        controller: {
          ...baseController,
          conversation: conversation('session:1:ready', 1, false, [
            { role: 'user', content: 'A question', clientTurnId: 'turn-a' },
            { role: 'assistant', content: 'A answer', clientTurnId: 'turn-a' },
          ]),
        },
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

    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false,
        controller: {
          ...baseController,
          conversation: conversation('session:2:loading', 2, true, []),
          composer: { ...baseController.composer, submitDisabled: true },
        },
      }))
    })
    assert.equal(
      window.document.querySelector('textarea[aria-label="编辑历史提问"]'),
      null,
    )
    const composer = window.document.querySelector('textarea[aria-label="输入任务"]')
    keydown(composer, { key: 'Enter', shiftKey: false, isComposing: false })
    window.document.querySelector('button[aria-label="发送"]')
      .dispatchEvent(new window.Event('click', { bubbles: true }))
    assert.equal(sends, 0)
    retiredAEditor.dispatchEvent(new window.Event('keydown', { bubbles: true }))
    assert.deepEqual(edits, [])

    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: false,
        controller: {
          ...baseController,
          conversation: conversation('session:2:ready', 2, false, [
            { role: 'user', content: 'B question', clientTurnId: 'turn-b' },
            { role: 'assistant', content: 'B answer', clientTurnId: 'turn-b' },
          ]),
        },
      }))
      await new Promise((resolve) => window.setTimeout(resolve, 10))
    })
    keydown(
      window.document.querySelector('textarea[aria-label="输入任务"]'),
      { key: 'Enter', shiftKey: false, isComposing: false },
    )
    assert.equal(sends, 1)
    const bEditButton = window.document.querySelector('button[aria-label="编辑提问"]')
    await act(async () => bEditButton.dispatchEvent(
      new window.Event('click', { bubbles: true }),
    ))
    const bEditor = window.document.querySelector('textarea[aria-label="编辑历史提问"]')
    keydown(bEditor, { key: 'Enter', shiftKey: false, isComposing: false })
    assert.deepEqual(edits, [{ index: 0, content: 'B question' }])
  } finally {
    await act(async () => root.unmount())
    await vite.close()
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})
