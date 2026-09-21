import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
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

function blockingLayers(root) {
  return React.Children.toArray(root.props.children.props.children)
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

test('popover maxHeight clamps content to the anchor-side available height', async () => {
  const { PurrPopover } = await vite.ssrLoadModule(
    '/src/purr-components/PurrPopover/PurrPopover.tsx',
  )
  const makeContentDiv = (props) => {
    const popup = portalPositioner(PurrPopover(props)).props.children
    const inner = React.Children.toArray(popup.props.children)
      .find((child) => String(child.props.className).includes('purr-popover__inner'))
    return React.Children.toArray(inner.props.children).at(-1)
  }

  const clamped = makeContentDiv({
    children: React.createElement('button', { type: 'button' }, '打开'),
    content: '内容',
    maxHeight: 220,
  })
  assert.equal(
    clamped.props.style.maxHeight,
    'min(220px, calc(var(--available-height, 268px) - 48px))',
  )
  assert.equal(clamped.props.style.overflowY, 'auto')

  const plain = makeContentDiv({
    children: React.createElement('button', { type: 'button' }, '打开'),
    content: '内容',
  })
  assert.equal(plain.props.style?.maxHeight, undefined)
  assert.equal(plain.props.style?.overflowY, undefined)
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

test('blocking overlays apply the requested surface and derived backdrop layers', async () => {
  const { PurrDialog } = await vite.ssrLoadModule(
    '/src/purr-components/PurrDialog/PurrDialog.tsx',
  )
  const { PurrDrawer } = await vite.ssrLoadModule(
    '/src/purr-components/PurrDrawer/PurrDrawer.tsx',
  )
  const { PurrModal } = await vite.ssrLoadModule(
    '/src/purr-components/PurrModal/PurrModal.tsx',
  )
  const dialog = PurrDialog({
    children: '正文',
    onOpenChange: () => {},
    open: true,
    title: '弹窗',
    zIndex: 1250,
  })
  const drawer = PurrDrawer({
    children: '正文',
    onClose: () => {},
    open: true,
    title: '抽屉',
    zIndex: 1250,
  })
  const modal = PurrModal({
    children: '正文',
    onCancel: () => {},
    open: true,
    title: '模态框',
    zIndex: 1250,
  })

  for (const root of [dialog, drawer]) {
    const [backdrop, surface] = blockingLayers(root)
    assert.equal(backdrop.props.style?.zIndex, 1240)
    assert.equal(surface.props.style?.zIndex, 1250)
  }
  assert.equal(modal.props.zIndex, 1250)
})

test('toast provider owns the region layer and renders the shared toast classes', async () => {
  const { PurrToastProvider } = await vite.ssrLoadModule(
    '/src/purr-components/PurrToast/PurrToast.tsx',
  )
  const markup = renderToStaticMarkup(
    React.createElement(
      PurrToastProvider,
      { zIndex: 1250 },
      React.createElement('main', null, '应用'),
    ),
  )

  assert.match(markup, /class="purr-toast-region"/)
  assert.match(markup, /style="z-index:1250"/)
})
