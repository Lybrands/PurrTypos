import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
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
