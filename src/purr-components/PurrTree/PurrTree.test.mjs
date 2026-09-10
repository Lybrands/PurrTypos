import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('多级目录可展开折叠，空目录仍为分支，键盘导航与文件选择独立', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const saved = Object.fromEntries(['window', 'document', 'Element', 'HTMLElement', 'Node', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  let root
  try {
    const { PurrTree, resolveTreeFileName } = await vite.ssrLoadModule('/src/purr-components/PurrTree/PurrTree.tsx')
    Object.assign(globalThis, { window, document: window.document, Element: window.Element, HTMLElement: window.HTMLElement, Node: window.Node, IS_REACT_ACT_ENVIRONMENT: true })
    const { createRoot } = await import('react-dom/client')
    const nodes = [
      { key: 'entry', label: 'SKILL.md' },
      { key: 'scenes', label: '场景', selectable: false, children: [
        { key: 'dialogue', label: '对白', selectable: false, children: [{ key: 'example', label: '示例.md' }] },
      ] },
      { key: 'empty', label: '空目录', children: [], selectable: false },
      { key: 'disabled', label: '禁用文件', disabled: true },
    ]
    let selection
    root = createRoot(window.document.getElementById('root'))
    let created
    let renamed
    let deleted
    await act(async () => root.render(React.createElement(PurrTree, { nodes, 'aria-label': '文件树', onSelect: key => { selection = key }, fileActions: {
      heading: '文件目录', onCreateNode: (kind, parent, name) => { created = [kind, parent, name] },
      allowedFileExtensions: ['.md', 'txt'], defaultFileExtension: 'md',
      onRenameNode: (key, name) => { renamed = [key, name] }, onDeleteNode: key => { deleted = key },
      canRenameNode: key => key !== 'entry', canDeleteNode: key => key !== 'entry',
    } })))
    const item = label => window.document.querySelector(`[role="treeitem"][aria-label="${label}"]`)
    const click = async label => act(async () => item(label).querySelector('.purr-tree__row').dispatchEvent(new window.Event('click', { bubbles: true })))
    const button = label => window.document.querySelector(`button[aria-label="${label}"]`)
    assert.equal(window.document.querySelector('.purr-tree__heading').textContent.includes('文件目录'), true)
    await act(async () => button('新建文件夹').dispatchEvent(new window.Event('click', { bubbles: true })))
    const folderInput = window.document.querySelector('input[aria-label="文件夹名称"]')
    assert.ok(folderInput)
    assert.equal(folderInput.value, '')
    await act(async () => {
      const event = new window.Event('keydown', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'key', { value: 'Enter' })
      folderInput.dispatchEvent(event)
    })
    assert.equal(created, undefined)
    assert.equal(window.document.querySelector('input[aria-label="文件夹名称"]'), null)
    assert.equal(resolveTreeFileName('节奏', ['.md', 'txt'], '.md'), '节奏.md')
    assert.equal(resolveTreeFileName('节奏.TXT', ['.md', 'txt'], '.md'), '节奏.TXT')
    assert.throws(() => resolveTreeFileName('节奏.json', ['.md', 'txt'], '.md'), /仅支持以下文件后缀/)
    assert.equal(button('重命名 SKILL.md'), null)
    await act(async () => button('重命名 场景').dispatchEvent(new window.Event('click', { bubbles: true })))
    const renameInput = window.document.querySelector('input[aria-label="重命名 场景"]')
    await act(async () => {
      const event = new window.Event('keydown', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'key', { value: 'Enter' })
      renameInput.dispatchEvent(event)
    })
    await act(async () => button('删除 场景').dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.deepEqual(renamed, ['scenes', '场景'])
    assert.equal(deleted, 'scenes')
    const key = async (label, value) => act(async () => {
      const event = new window.Event('keydown', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'key', { value })
      item(label).dispatchEvent(event)
    })
    assert.equal(item('示例.md'), null)
    await click('场景')
    assert.equal(item('场景').getAttribute('aria-expanded'), 'true')
    assert.equal(selection, undefined)
    await key('对白', 'ArrowRight')
    assert.equal(item('示例.md').getAttribute('aria-level'), '3')
    await key('对白', 'ArrowRight')
    assert.equal(item('示例.md').getAttribute('tabindex'), '0')
    await key('示例.md', 'Enter')
    assert.equal(selection, 'example')
    await key('示例.md', 'ArrowLeft')
    assert.equal(item('对白').getAttribute('tabindex'), '0')
    await key('对白', 'ArrowLeft')
    assert.equal(item('示例.md'), null)
    await click('场景')
    assert.equal(item('对白'), null)
    assert.equal(item('空目录').getAttribute('aria-expanded'), 'false')
    await click('空目录')
    assert.equal(item('空目录').getAttribute('aria-expanded'), 'true')
    await click('禁用文件')
    assert.equal(selection, 'example')
    assert.equal(window.document.querySelectorAll('[role="treeitem"][tabindex="0"]').length, 1)
  } finally {
    if (root) await act(async () => root.unmount())
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
    await vite.close()
  }
})
