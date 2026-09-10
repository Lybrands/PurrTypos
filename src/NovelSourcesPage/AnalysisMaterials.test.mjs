import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('background is one Markdown document with a single evidence footer', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  try {
    const { default: Materials } = await vite.ssrLoadModule('/src/NovelSourcesPage/AnalysisMaterials.tsx')
    const facts = ['清河镇靠水运维生。', '雨季让渡口停航。'].map((value, index) => ({factKind: 'background', subjectKey: '清河镇', predicate: `背景${index}`, value, lifecycleStatus: 'active', evidence: [{excerpt:value}]}))
    const html = renderToStaticMarkup(React.createElement(Materials, {facts, onEvidence() {}, onChange() {}}))
    assert.equal((html.match(/class="agent-markdown/g) || []).length, 1)
    assert.ok(html.includes('<p>清河镇靠水运维生。</p>'))
    assert.ok(html.includes('<p>雨季让渡口停航。</p>'))
    assert.equal((html.match(/来源依据/g) || []).length, 1)
    assert.ok(html.indexOf('来源依据') > html.indexOf('雨季让渡口停航。'))
    assert.ok(!html.includes('<details'))
  } finally { await vite.close() }
})
