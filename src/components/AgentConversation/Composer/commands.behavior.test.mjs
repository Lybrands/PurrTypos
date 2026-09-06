import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

let vite
let Composer
let composerCommandQuery

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ default: Composer, composerCommandQuery } = await vite.ssrLoadModule(
    '/src/components/AgentConversation/Composer/index.tsx',
  ))
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
