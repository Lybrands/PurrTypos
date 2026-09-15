import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('分析结果使用目录树，并按完整子目录路径读取选中文件', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const saved = Object.fromEntries(['window', 'document', 'Element', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  let root, service, originalManifest, originalRead
  try {
    const { WritingSkillReview } = await vite.ssrLoadModule('/src/NovelSourcesPage/WritingSkillReview.tsx')
    const { services } = await vite.ssrLoadModule('/src/services/index.ts')
    service = services.writingTechniques
    originalManifest = service.versionManifest
    originalRead = service.readVersionFile
    const reads = []
    service.versionManifest = async () => ({ success: true, data: {
      metadata: { name: '测试技法', description: '测试用途', retrieval: {
        intents: ['制造持续压力'], contexts: ['人物必须作出选择'], objectives: ['推动冲突升级'],
        keywords: ['环境压力'], exclusions: ['一次性突发事件'],
      } },
      files: ['SKILL.md', '场景/对白/示例.md', '场景/说明.md'].map(path => ({ path })),
    } })
    service.readVersionFile = async (id, version, path) => {
      reads.push([id, version, path])
      return { success: true, data: { content: `文件正文：${path}` } }
    }
    Object.assign(globalThis, { window, document: window.document, Element: window.Element, HTMLElement: window.HTMLElement, Node: window.Node, IS_REACT_ACT_ENVIRONMENT: true })
    const { createRoot } = await import('react-dom/client')
    root = createRoot(window.document.getElementById('root'))
    await act(async () => root.render(React.createElement(WritingSkillReview, { artifact: {
      techniqueResult: { status: 'completed', scopeNotes: [], candidate: { techniqueId: 'fixture', versionId: 'v1' } },
    } })))
    const item = name => window.document.querySelector(`[role="treeitem"][aria-label="${name}"]`)
    assert.ok(window.document.querySelector('nav[aria-label="写作技法文件目录"] [role="tree"]'))
    const metadata = window.document.querySelector('[aria-label="Skill 检索源数据"]')
    assert.match(metadata.textContent, /制造持续压力/)
    assert.match(metadata.textContent, /一次性突发事件/)
    assert.equal(item('场景').getAttribute('aria-expanded'), 'true')
    assert.equal(item('对白').getAttribute('aria-level'), '3')
    assert.equal(item('示例.md').getAttribute('aria-level'), '4')
    await act(async () => item('对白').querySelector('.purr-tree__row').dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.equal(item('示例.md'), null)
    await act(async () => item('对白').querySelector('.purr-tree__row').dispatchEvent(new window.Event('click', { bubbles: true })))
    await act(async () => item('示例.md').querySelector('.purr-tree__row').dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.deepEqual(reads.at(-1), ['fixture', 'v1', '场景/对白/示例.md'])
    assert.match(window.document.querySelector('article').textContent, /文件正文：场景\/对白\/示例.md/)
    assert.equal(item('示例.md').getAttribute('aria-selected'), 'true')
  } finally {
    if (root) await act(async () => root.unmount())
    if (service) { service.versionManifest = originalManifest; service.readVersionFile = originalRead }
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
    await vite.close()
  }
})
