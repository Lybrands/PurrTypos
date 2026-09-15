import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let AgentConversationPanel
let AgentMessageFooter
let ExecutionLog
let ExecutionLogStepGroup
let AgentTaskProgress
let resolveTaskProgressOpenChange
let buildAgentModelLabels
let formatAgentMessageTime

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ default: AgentConversationPanel } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Panel.tsx',
  ))
  ;({ default: AgentMessageFooter } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/MessageFooter.tsx',
  ))
  ;({ default: ExecutionLog, ExecutionLogStepGroup } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/ExecutionLog/index.tsx',
  ))
  ;({ default: AgentTaskProgress, resolveTaskProgressOpenChange } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/TaskProgress/index.tsx',
  ))
  ;({ buildAgentModelLabels, formatAgentMessageTime } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/messageMetadata.ts',
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

test('paused work exposes resume but not the live-generation stop action', () => {
  const controller = {
    capabilities: {
      inputDisabled: false,
      sessionNavigationDisabled: false,
      submitMode: 'send',
    },
    conversation: {
      identity: 'session:7:paused-1',
      sessions: [{ id: 7, title: 'A' }],
      activeSessionId: 7,
      messages: [],
      activities: {},
      queuedSubmissions: [],
      initializing: false,
      running: false,
      stopping: false,
      paused: true,
      resuming: false,
      resumeLabel: '立即继续',
    },
    composer: {
      value: '',
      setValue: () => undefined,
      placeholder: '输入任务',
      ariaLabel: '输入任务',
      submitDisabled: false,
      selectedModel: {
        id: 'model-1', name: 'model-1', supportsThinking: false,
        thinkingOnly: false, apiKey: 'test', baseUrl: '',
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
      resume: () => undefined,
      editMessage: () => undefined,
      resolveToolApproval: async () => ({ success: true }),
    },
  }

  const markup = renderToStaticMarkup(
    React.createElement(AgentConversationPanel, { controller, indexOpen: false }),
  )

  assert.match(markup, /立即继续/)
  assert.doesNotMatch(markup, /aria-label="停止生成"/)
})

test('message time uses local calendar-day labels before falling back to a date', () => {
  const now = new Date('2026-08-27T00:30:00')

  assert.equal(formatAgentMessageTime('2026-08-27T23:43:00', now), '23:43')
  assert.equal(formatAgentMessageTime('2026-08-26T23:43:00', now), '昨天 23:43')
  assert.equal(formatAgentMessageTime('2026-08-25T08:43:00', now), '前天 08:43')
  assert.equal(formatAgentMessageTime('2026-08-23T08:43:00', now), '8月23日 08:43')
  assert.equal(formatAgentMessageTime('2025-12-30T08:43:00', now), '2025年12月30日 08:43')
})

test('active execution log title does not append animated ellipsis', () => {
  const markup = renderToStaticMarkup(React.createElement(ExecutionLog, {
    logKey: 'active-without-dots',
    title: '正在进行',
    active: true,
    autoOpen: false,
    children: React.createElement('span', null, '执行详情'),
  }))

  assert.match(markup, /正在进行/)
  assert.doesNotMatch(markup, /a-blink-dots|\.\.\./)
  assert.equal(
    parseHTML(`<html><body>${markup}</body></html>`)
      .document.querySelector('.work-log__collapsible')
      ?.hasAttribute('hidden'),
    true,
  )
})

test('execution panel shows status while nested groups retain their execution heading', () => {
  for (const active of [false, true]) {
    const markup = renderToStaticMarkup(React.createElement(ExecutionLog, {
      logKey: `execution-headings-${active}`,
      title: active ? '正在进行' : '已完成',
      active,
      autoOpen: true,
      children: React.createElement(ExecutionLogStepGroup, {
        groupKey: `step-count-${active}`,
        stepCount: 9,
        active,
        activeLabel: '读取剧本交付物',
        completedDurationMs: 65,
        children: React.createElement('span', null, '读取剧本交付物'),
      }),
    }))
    const { document } = parseHTML(`<html><body>${markup}</body></html>`)
    const outerTitle = document.querySelector('.work-log__toggle').textContent
    const innerTitle = document.querySelector('.work-log-step-group__toggle').textContent
    assert.match(outerTitle, active ? /正在进行/ : /已完成/)
    assert.doesNotMatch(outerTitle, /个步骤|读取剧本交付物/)
    assert.match(innerTitle, active ? /正在执行 读取剧本交付物/ : /执行了9 个步骤/)
    assert.doesNotMatch(innerTitle, /正在进行|已完成/)
    if (active) {
      assert.match(markup, /读取剧本交付物/)
      assert.equal(document.querySelector('.work-log__toggle').disabled, false)
      assert.equal(document.querySelector('.work-log-step-group__toggle').disabled, true)
      assert.equal(document.querySelector('.work-log__collapsible').hasAttribute('hidden'), false)
      assert.equal(document.querySelector('.work-log-step-group__collapsible').hasAttribute('hidden'), false)
    }
    else assert.doesNotMatch(markup, /读取剧本交付物/)
  }
})

test('task progress can be toggled closed while nonterminal', () => {
  assert.equal(resolveTaskProgressOpenChange(false, false), false)
  assert.equal(resolveTaskProgressOpenChange(false, true), true)
  assert.equal(resolveTaskProgressOpenChange(true, false), false)
})

test('assistant footer places actions before hover-only time', () => {
  const today = new Date()
  const todayAt0843 = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, '0'),
    String(today.getDate()).padStart(2, '0'),
  ].join('-') + 'T08:43:00'
  const model = {
    id: 'model-1',
    name: 'deepseek-v4-flash',
    nickname: 'DeepSeek V4 Flash',
    supportsThinking: false,
    thinkingOnly: false,
    apiKey: 'test',
    baseUrl: '',
  }
  const userFooter = React.createElement(AgentMessageFooter, {
    side: 'user',
    sentAt: todayAt0843,
    actions: React.createElement('button', { 'aria-label': '复制消息' }, '复制'),
  })
  const assistantFooter = React.createElement(AgentMessageFooter, {
    side: 'assistant',
    sentAt: todayAt0843,
    model: 'deepseek-v4-flash',
    modelLabels: buildAgentModelLabels([model]),
    actionsPersistent: true,
    actions: React.createElement(
      React.Fragment,
      null,
      React.createElement('button', { 'aria-label': '收藏回复' }, '收藏'),
      React.createElement('button', { 'aria-label': '复制回复纯文本' }, '复制'),
    ),
  })
  const markup = renderToStaticMarkup(React.createElement(
    'article',
    null,
    userFooter,
    assistantFooter,
  ))
  const { document } = parseHTML(`<html><body>${markup}</body></html>`)
  const userFooterElement = document.querySelector('.agent-message-footer.is-user')
  const assistantFooterElement = document.querySelector('.agent-message-footer.is-assistant')

  assert.ok(userFooterElement)
  assert.ok(assistantFooterElement)
  assert.equal(userFooterElement.querySelector('.agent-message-footer__time')?.textContent, '08:43')
  assert.equal(
    assistantFooterElement.querySelector('.agent-message-footer__persistent')?.textContent,
    'DeepSeek V4 Flash',
  )
  assert.equal(assistantFooterElement.querySelector('.agent-message-footer__persistent time'), null)
  const assistantActions = assistantFooterElement.querySelector('.agent-message-footer__actions')
  const assistantTime = assistantFooterElement.querySelector('.agent-message-footer__time')
  const assistantFooterChildren = [...assistantFooterElement.children]

  assert.ok(assistantActions)
  assert.ok(assistantTime)
  assert.equal(assistantTime.textContent, '08:43')
  assert.ok(
    assistantFooterChildren.indexOf(assistantActions)
      < assistantFooterChildren.indexOf(assistantTime),
  )
  assert.equal(assistantActions.classList.contains('is-persistent'), true)
  assert.deepEqual(
    [...assistantActions.querySelectorAll('button')]
      .map((button) => button.getAttribute('aria-label')),
    ['收藏回复', '复制回复纯文本'],
  )
})

test('historical assistant actions are not persistent', () => {
  const markup = renderToStaticMarkup(React.createElement(AgentMessageFooter, {
    side: 'assistant',
    sentAt: '2024-01-02T08:43:00',
    actions: React.createElement('button', { 'aria-label': '复制回复纯文本' }, '复制'),
  }))
  const { document } = parseHTML(`<html><body>${markup}</body></html>`)
  const actions = document.querySelector('.agent-message-footer__actions')

  assert.ok(actions)
  assert.equal(actions.classList.contains('is-persistent'), false)
})

test('active assistant footer omits metadata without hiding actions', () => {
  const markup = renderToStaticMarkup(React.createElement(AgentMessageFooter, {
    side: 'assistant',
    sentAt: '2024-01-02T08:43:00',
    model: 'deepseek-v4-flash',
    modelLabels: { 'deepseek-v4-flash': 'DeepSeek V4 Flash' },
    metadataVisible: false,
    actionsPersistent: true,
    actions: React.createElement('button', { 'aria-label': '收藏回复' }, '收藏'),
  }))
  const { document } = parseHTML(`<html><body>${markup}</body></html>`)
  const footer = document.querySelector('.agent-message-footer.is-assistant')

  assert.ok(footer)
  assert.equal(footer.querySelector('.agent-message-footer__persistent'), null)
  assert.equal(footer.querySelector('.agent-message-footer__time'), null)
  assert.equal(
    footer.querySelector('.agent-message-footer__actions button')?.getAttribute('aria-label'),
    '收藏回复',
  )
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
