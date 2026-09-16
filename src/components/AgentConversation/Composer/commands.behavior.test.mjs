import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let Composer
let composerCommandQuery
let composerActionTrigger
let composerActionDismissal
let composerActionCloseValue

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ default: Composer, composerCommandQuery, composerActionTrigger, composerActionDismissal, composerActionCloseValue } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Composer/index.tsx',
  ))
})

test('slash action menu distinguishes cancellation from literal slash input', () => {
  const range = { start: 2, token: '/' }
  assert.equal(composerActionDismissal('写作/', range), 'none')
  assert.equal(composerActionDismissal('写作/ ', range), 'literal')
  assert.equal(composerActionDismissal('写作', range), 'deleted')
  assert.equal(composerActionDismissal('写作/技法', range), 'none')
  assert.equal(composerActionCloseValue('写作/', range, false), '写作/')
  assert.equal(composerActionCloseValue('写作/技法', range, true), '写作')
})

after(async () => { await vite?.close() })

test('shared composer opens only generic commands matching a lone slash query', () => {
  assert.equal(composerCommandQuery('/'), '')
  assert.equal(composerCommandQuery('/悬念'), '悬念')
  assert.equal(composerCommandQuery('/悬念 继续写'), null)

  const markup = renderToStaticMarkup(React.createElement(Composer, {
    value: '/悬念',
    onChange: () => undefined,
    onSubmit: () => undefined,
    footer: React.createElement('span'),
    commands: [
      { id: 'bound', label: '悬念递进', description: '本轮强制', onSelect: () => undefined },
      { id: 'other', label: '对白节奏', onSelect: () => undefined },
    ],
  }))

  assert.match(markup, /悬念递进/)
  assert.doesNotMatch(markup, /对白节奏/)
  assert.match(markup, /role="listbox"/)
})

test('business-configured action triggers respect caret, IME and literal paths', () => {
  assert.equal(composerActionTrigger('', '\\', 1, ['\\']), '\\')
  assert.equal(composerActionTrigger('草稿 ', '草稿 \\', 4, ['\\']), '\\')
  assert.equal(composerActionTrigger('草稿 尾文', '草稿 @@尾文', 5, ['@', '@@']), '@@')
  assert.equal(composerActionTrigger('', '/', 1, ['\\']), undefined)
  assert.equal(composerActionTrigger('', '/', 1, ['/']), '/')
  assert.equal(composerActionTrigger('', '\\', 1, ['\\'], true), undefined)
  assert.equal(composerActionTrigger('C:', 'C:\\', 3, ['\\']), undefined)
  assert.equal(composerActionTrigger('xx', 'x', 1, ['x']), undefined)
})

test('plus button toggles the action menu closed on a second click', async () => {
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
  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => {
      root.render(React.createElement(Composer, {
        value: '',
        onChange: () => undefined,
        onSubmit: () => undefined,
        footer: React.createElement('span'),
        actionMenu: {
          triggers: ['/'],
          title: '对话操作',
          render: () => React.createElement('span', null, 'ACTION-CONTENT'),
        },
      }))
    })
    const plus = () => window.document.querySelector('button[aria-label="对话操作"]')
    const menu = () => window.document.querySelector('[role="region"][aria-label="对话操作"]')
    assert.equal(plus().getAttribute('aria-expanded'), 'false')
    assert.equal(menu(), null)

    await act(async () => plus().dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.equal(plus().getAttribute('aria-expanded'), 'true')
    assert.match(menu().textContent, /ACTION-CONTENT/)

    await act(async () => plus().dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.equal(plus().getAttribute('aria-expanded'), 'false')
    assert.equal(menu(), null)
  } finally {
    await act(async () => root.unmount())
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})
