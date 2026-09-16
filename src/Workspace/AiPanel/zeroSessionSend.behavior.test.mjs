import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let AgentConversationPanel
let createBookConversationController

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    plugins: [{
      name: 'zero-session-virtuoso-test-double',
      enforce: 'pre',
      resolveId(id) {
        return id === 'react-virtuoso' ? '\0zero-session-virtuoso' : null
      },
      load(id) {
        if (id !== '\0zero-session-virtuoso') return null
        return `
          import React from 'react'
          export const Virtuoso = React.forwardRef(function TestVirtuoso(props, ref) {
            const scroller = React.useRef(null)
            React.useImperativeHandle(ref, () => ({ scrollToIndex() {} }), [])
            React.useEffect(() => {
              props.scrollerRef?.(scroller.current)
              return () => props.scrollerRef?.(null)
            }, [props.scrollerRef])
            return React.createElement('div', { ref: scroller },
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
  await import('sass')
  ;({ default: AgentConversationPanel } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Panel.tsx',
  ))
  ;({ createBookConversationController } = await vite.ssrLoadModule(
    '/src/Workspace/AiPanel/useBookConversationController.ts',
  ))
})

after(async () => { await vite?.close() })

test('zero-session send button click reaches actions.send for the writing panel', async () => {
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
  class ResizeObserver {
    constructor(callback) { this.callback = callback; this.observed = new WeakSet() }
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
  Object.assign(globalThis, {
    window,
    Window: window.constructor,
    document: window.document,
    Event: window.Event,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    Node: window.Node,
    MutationObserver: window.MutationObserver,
    ResizeObserver,
    getComputedStyle: () => ({ lineHeight: '22px' }),
    requestAnimationFrame: (callback) => window.setTimeout(callback, 0),
    cancelAnimationFrame: (id) => window.clearTimeout(id),
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  const { createRoot } = await import('react-dom/client')
  const act = React.act

  const model = {
    id: 'm', name: 'model', apiKey: 'test', baseUrl: '',
    supportsThinking: false, thinkingOnly: false,
  }
  let sends = 0
  const controller = createBookConversationController({
    conversationIdentity: 'book-session:none',
    sessions: [],
    activeSessionId: null,
    messages: [],
    prependedHistory: [],
    activities: {},
    queuedSubmissions: [],
    prompt: '开始新的写作任务',
    setPrompt: () => undefined,
    initializing: false,
    running: false,
    stopping: false,
    paused: false,
    resuming: false,
    modelConfigs: [model],
    selectedModelId: 'm',
    setSelectedModelId: () => undefined,
    updateModel: () => undefined,
    openModelSettings: () => undefined,
    scopeAvailable: true,
    taskPlan: null,
    actions: {
      selectSession: () => undefined,
      createSession: async () => undefined,
      closeSession: () => undefined,
      renameSession: () => undefined,
      send: () => { sends += 1 },
      abort: () => undefined,
      resume: () => undefined,
      editMessage: () => undefined,
      resolveToolApproval: async () => ({ success: true }),
      onSubmitErrorReport: async () => ({ success: true }),
    },
    historySessions: [],
    historyLoading: false,
    attachmentsVersion: 0,
    onOpenFromHistory: () => undefined,
    onDeleteFromHistory: () => undefined,
  })

  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        indexOpen: true,
        controller,
      }))
      await new Promise((resolve) => window.setTimeout(resolve, 10))
    })
    const sendButton = window.document.querySelector('button[aria-label="发送"]')
    assert.ok(sendButton, 'send button must render with zero sessions')
    assert.equal(sendButton.disabled, false, 'send must be enabled with zero sessions')
    await act(async () => {
      sendButton.dispatchEvent(new window.Event('click', { bubbles: true }))
    })
    assert.equal(sends, 1, 'clicking send must call actions.send exactly once')
  } finally {
    await act(async () => root.unmount())
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})
