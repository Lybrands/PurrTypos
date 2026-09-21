import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('折叠项支持默认展开、独立切换与变更回调', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const globalKeys = ['window', 'document', 'Element', 'HTMLElement', 'Node', 'Event', 'IS_REACT_ACT_ENVIRONMENT']
  const saved = Object.fromEntries(globalKeys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  let root
  try {
    const { PurrCollapse } = await vite.ssrLoadModule('/src/purr-components/PurrCollapse/PurrCollapse.tsx')
    Object.assign(globalThis, {
      window,
      document: window.document,
      Element: window.Element,
      HTMLElement: window.HTMLElement,
      Node: window.Node,
      Event: window.Event,
      IS_REACT_ACT_ENVIRONMENT: true,
    })
    const { createRoot } = await import('react-dom/client')
    const changes = []
    root = createRoot(window.document.getElementById('root'))
    await act(async () => root.render(React.createElement(PurrCollapse, {
      defaultActiveKeys: ['scope'],
      onChange: keys => changes.push(keys),
      items: [
        { key: 'scope', label: '适用范围', children: React.createElement('p', null, '范围内容') },
        { key: 'source', label: '来源信息', children: React.createElement('p', null, '来源内容') },
      ],
    })))

    const trigger = label => [...window.document.querySelectorAll('button')].find(button => button.textContent.includes(label))
    assert.equal(trigger('适用范围').getAttribute('aria-expanded'), 'true')
    assert.equal(trigger('来源信息').getAttribute('aria-expanded'), 'false')
    assert.match(window.document.body.textContent, /范围内容/)

    await act(async () => trigger('来源信息').dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.equal(trigger('来源信息').getAttribute('aria-expanded'), 'true')
    assert.deepEqual(changes.at(-1), ['scope', 'source'])

    await act(async () => trigger('适用范围').dispatchEvent(new window.Event('click', { bubbles: true })))
    assert.equal(trigger('适用范围').getAttribute('aria-expanded'), 'false')
    assert.deepEqual(changes.at(-1), ['source'])
  } finally {
    if (root) await act(async () => root.unmount())
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
    await vite.close()
  }
})
