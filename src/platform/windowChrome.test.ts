import assert from 'node:assert/strict'
import { test, type TestContext } from 'node:test'
import { initializeWindowChrome } from './windowChrome.ts'
import type { DesktopBridge, WindowChromeState } from './types'

type ChromeBridge = Pick<DesktopBridge, 'platform' | 'getWindowChromeState' | 'onWindowChromeStateChanged'>

function installEnvironment(t: TestContext, bridge?: ChromeBridge, overlay?: EventTarget) {
  const root = { dataset: {} as Record<string, string> }
  const values = {
    window: { purrDesktop: bridge },
    document: { documentElement: root },
    navigator: { windowControlsOverlay: overlay },
  }
  for (const [name, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { configurable: true, value })
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, name, previous)
      else Reflect.deleteProperty(globalThis, name)
    })
  }
  return root
}

test('browser pages do not reserve native control space', (t) => {
  const root = installEnvironment(t)
  initializeWindowChrome()()
  assert.deepEqual(root.dataset, {})
})

test('an initial native fullscreen reply releases reserved space', async (t) => {
  const root = installEnvironment(t, {
    platform: 'darwin',
    getWindowChromeState: async () => ({ fullscreen: true }),
  })
  const dispose = initializeWindowChrome()
  assert.equal(root.dataset.desktopPlatform, 'darwin')
  assert.equal(root.dataset.windowFullscreen, 'false')
  await Promise.resolve()
  assert.equal(root.dataset.windowFullscreen, 'true')
  dispose()
  assert.deepEqual(root.dataset, {})
})

test('a newer fullscreen event wins over a pending initial reply', async (t) => {
  let listener: ((state: WindowChromeState) => void) | undefined
  let resolveInitial: ((state: WindowChromeState) => void) | undefined
  let unsubscribed = false
  const root = installEnvironment(t, {
    platform: 'win32',
    getWindowChromeState: () => new Promise((resolve) => { resolveInitial = resolve }),
    onWindowChromeStateChanged: (callback) => {
      listener = callback
      return () => { unsubscribed = true }
    },
  })
  const dispose = initializeWindowChrome()
  listener?.({ fullscreen: true })
  resolveInitial?.({ fullscreen: false })
  await Promise.resolve()
  assert.equal(root.dataset.windowFullscreen, 'true')
  dispose()
  assert.equal(unsubscribed, true)
  listener?.({ fullscreen: false })
  assert.deepEqual(root.dataset, {})
})

test('CSS environment geometry is enabled only for a visible nonempty overlay', (t) => {
  const overlay = Object.assign(new EventTarget(), {
    visible: false,
    getTitlebarAreaRect: () => ({ width: 800, height: 52 }),
  })
  const root = installEnvironment(t, { platform: 'win32' }, overlay)
  const dispose = initializeWindowChrome()
  assert.equal(root.dataset.windowControlsOverlay, 'false')
  overlay.visible = true
  overlay.dispatchEvent(new Event('geometrychange'))
  assert.equal(root.dataset.windowControlsOverlay, 'true')
  overlay.getTitlebarAreaRect = () => ({ width: 0, height: 0 })
  overlay.dispatchEvent(new Event('geometrychange'))
  assert.equal(root.dataset.windowControlsOverlay, 'false')
  dispose()
  overlay.dispatchEvent(new Event('geometrychange'))
  assert.deepEqual(root.dataset, {})
})
