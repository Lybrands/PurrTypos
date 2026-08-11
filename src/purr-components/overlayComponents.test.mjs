import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { createServer } from 'vite'

let vite

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
})

after(async () => {
  await vite?.close()
})

function portalPositioner(root) {
  const children = React.Children.toArray(root.props.children)
  const portal = children.at(-1)
  return portal.props.children
}

test('anchored overlays apply explicit z-index to their positioners', async () => {
  const { PurrDropdown } = await vite.ssrLoadModule(
    '/src/purr-components/PurrDropdown/PurrDropdown.tsx',
  )
  const { PurrPopover } = await vite.ssrLoadModule(
    '/src/purr-components/PurrPopover/PurrPopover.tsx',
  )
  const { PurrSelect } = await vite.ssrLoadModule(
    '/src/purr-components/PurrSelect/PurrSelect.tsx',
  )
  const { PurrTooltip } = await vite.ssrLoadModule(
    '/src/purr-components/PurrTooltip/PurrTooltip.tsx',
  )
  const trigger = React.createElement('button', { type: 'button' }, '打开')

  const roots = [
    PurrDropdown({
      children: trigger,
      menu: { items: [] },
      zIndex: 1250,
    }),
    PurrDropdown({
      children: trigger,
      menu: { items: [] },
      trigger: ['contextMenu'],
      zIndex: 1250,
    }),
    PurrPopover({
      children: trigger,
      content: '内容',
      zIndex: 1250,
    }),
    PurrSelect({
      options: [{ value: 'one', label: '一' }],
      zIndex: 1250,
    }),
    PurrTooltip({
      children: trigger,
      title: '提示',
      zIndex: 1250,
    }),
  ]

  for (const root of roots) {
    assert.equal(portalPositioner(root).props.style?.zIndex, 1250)
  }
})

test('tooltip content does not create the global stacking layer', async () => {
  const { PurrTooltip } = await vite.ssrLoadModule(
    '/src/purr-components/PurrTooltip/PurrTooltip.tsx',
  )
  const root = PurrTooltip({
    children: React.createElement('button', { type: 'button' }, '打开'),
    title: '提示',
    zIndex: 1250,
  })
  const popup = portalPositioner(root).props.children

  assert.equal(popup.props.style?.zIndex, undefined)
})
