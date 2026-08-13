import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let AgentConversationPanel

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ default: AgentConversationPanel } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Panel.tsx',
  ))
})

after(async () => {
  await vite?.close()
})

test('initializing keeps the textarea editable while both Enter and send are disabled', () => {
  const controller = {
    capabilities: {
      inputDisabled: false,
      sessionNavigationDisabled: false,
      submitMode: 'send',
    },
    conversation: {
      identity: 'session:7:loading-1',
      sessions: [{ id: 7, title: 'A' }],
      activeSessionId: 7,
      messages: [],
      activities: {},
      queuedSubmissions: [],
      initializing: true,
      running: false,
      stopping: false,
      paused: false,
      resuming: false,
    },
    composer: {
      value: '恢复期间可继续写草稿',
      setValue: () => undefined,
      placeholder: '输入任务',
      ariaLabel: '输入任务',
      submitDisabled: true,
      selectedModel: {
        id: 'model-1',
        name: 'model-1',
        supportsThinking: false,
        thinkingOnly: false,
        apiKey: 'test',
        baseUrl: '',
      },
      modelConfigs: [],
      selectModel: () => undefined,
      openModelSettings: () => undefined,
    },
    actions: {
      selectSession: () => undefined,
      createSession: () => undefined,
      closeSession: () => undefined,
      renameSession: () => undefined,
      send: () => undefined,
      abort: () => undefined,
      editMessage: () => undefined,
      resolveToolApproval: async () => ({ success: true }),
    },
  }
  const markup = renderToStaticMarkup(
    React.createElement(AgentConversationPanel, { controller, indexOpen: false }),
  )
  const textarea = markup.match(/<textarea\b[^>]*>/)?.[0]
  const send = [...markup.matchAll(/<button\b[^>]*>/g)]
    .map(([tag]) => tag)
    .find((tag) => tag.includes('aria-label="发送"'))

  assert.ok(textarea)
  assert.doesNotMatch(textarea, /\bdisabled(?:=|\s|>)/)
  assert.ok(send)
  assert.match(send, /\bdisabled(?:=|\s|>)/)
  assert.match(markup, /正在恢复对话/)
})

test('mounted initializing panel rejects real Enter and click until current session is ready', async () => {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    Event: globalThis.Event,
    Element: globalThis.Element,
    HTMLElement: globalThis.HTMLElement,
    Node: globalThis.Node,
    MutationObserver: globalThis.MutationObserver,
    getComputedStyle: globalThis.getComputedStyle,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
  }
  Object.assign(globalThis, {
    window,
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
  const { createRoot } = await import('react-dom/client')
  const act = React.act
  // React feature-detects DOM input support at module load. Linkedom exposes
  // it via setAttribute, so declare the native handler slot explicitly before
  // mounting to exercise the modern keyboard plugin path.
  window.document.oninput = null
  let sends = 0
  const controller = {
    capabilities: {
      inputDisabled: false,
      sessionNavigationDisabled: false,
      submitMode: 'send',
    },
    conversation: {
      identity: 'session:7:loading-1',
      sessions: [{ id: 7, title: 'A' }],
      activeSessionId: 7,
      messages: [],
      activities: {},
      queuedSubmissions: [],
      initializing: true,
      running: false,
      stopping: false,
      paused: false,
      resuming: false,
    },
    composer: {
      value: 'hydrating draft',
      setValue: () => undefined,
      placeholder: '输入任务',
      ariaLabel: '输入任务',
      submitDisabled: true,
      selectedModel: {
        id: 'model-1',
        name: 'model-1',
        supportsThinking: false,
        thinkingOnly: false,
        apiKey: 'test',
        baseUrl: '',
      },
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
      editMessage: () => undefined,
      resolveToolApproval: async () => ({ success: true }),
    },
  }
  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        controller,
        indexOpen: false,
      }))
    })
    const textarea = window.document.querySelector('textarea[aria-label="输入任务"]')
    const send = window.document.querySelector('button[aria-label="发送"]')
    assert.equal(textarea.disabled, false)
    assert.equal(send.disabled, true)
    send.dispatchEvent(new window.Event('click', { bubbles: true }))
    assert.equal(sends, 0)

    const ready = {
      ...controller,
      conversation: {
        ...controller.conversation,
        identity: 'session:7:ready-1',
        initializing: false,
      },
      composer: { ...controller.composer, submitDisabled: false },
    }
    await act(async () => {
      root.render(React.createElement(AgentConversationPanel, {
        controller: ready,
        indexOpen: false,
      }))
    })
    window.document.querySelector('button[aria-label="发送"]')
      .dispatchEvent(new window.Event('click', { bubbles: true }))
    assert.equal(sends, 1)
  } finally {
    await act(async () => root.unmount())
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})
