import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('background is one Markdown document without source-evidence UI', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  try {
    const { default: Materials } = await vite.ssrLoadModule('/src/NovelSourcesPage/AnalysisMaterials.tsx')
    const facts = ['清河镇靠水运维生。', '雨季让渡口停航。'].map((value, index) => ({factKind: 'background', subjectKey: '清河镇', predicate: `背景${index}`, value, lifecycleStatus: 'active'}))
    const html = renderToStaticMarkup(React.createElement(Materials, {facts, onChange() {}}))
    assert.equal((html.match(/class="agent-markdown/g) || []).length, 1)
    assert.ok(html.includes('<p>清河镇靠水运维生。</p>'))
    assert.ok(html.includes('<p>雨季让渡口停航。</p>'))
    assert.equal((html.match(/来源依据/g) || []).length, 0)
    assert.ok(!html.includes('<details'))
  } finally { await vite.close() }
})

test('canonical character and background use creation editors instead of a generic fact form', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  try {
    const { default: Materials } = await vite.ssrLoadModule('/src/NovelSourcesPage/AnalysisMaterials.tsx')
    const character = { id: 'character-linyue', factKind: 'character_summary', subjectKey: '林月', predicate: '人物归纳', value: { name: '林月', tags: '主角, 调查者', profile_md: '## 基本信息\n调查旧城异象。' }, lifecycleStatus: 'active' }
    const background = { id: 'background', factKind: 'background', subjectKey: '故事背景', predicate: '背景归纳', value: { content: '## 时空\n潮水侵袭旧城。' }, lifecycleStatus: 'active' }
    const characterHtml = renderToStaticMarkup(React.createElement(Materials, { facts: [character], onChange() {} }))
    const backgroundHtml = renderToStaticMarkup(React.createElement(Materials, { facts: [background], onChange() {} }))
    assert.ok(characterHtml.includes('novel-analysis-material-collapse'))
    assert.ok(characterHtml.includes('林月'))
    assert.ok(characterHtml.includes('主角'))
    assert.ok(characterHtml.includes('aria-label="编辑林月资料"'))
    assert.ok(backgroundHtml.includes('编辑故事背景'))
    assert.ok(backgroundHtml.includes('潮水侵袭旧城'))
  } finally { await vite.close() }
})
