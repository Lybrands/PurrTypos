import type { WindowChromeState } from './types'

interface WindowControlsOverlay extends EventTarget {
  visible: boolean
  getTitlebarAreaRect: () => DOMRect
}

/** Run before React renders so native window controls have reserved space immediately. */
export function initializeWindowChrome() {
  const bridge = window.purrDesktop
  if (bridge?.platform !== 'darwin' && bridge?.platform !== 'win32') return () => undefined

  const root = document.documentElement
  root.dataset.desktopPlatform = bridge.platform
  root.dataset.windowFullscreen = 'false'

  let active = true
  let receivedState = false
  const applyState = (state: WindowChromeState) => {
    if (active) root.dataset.windowFullscreen = String(state.fullscreen)
  }
  const unsubscribe = bridge.onWindowChromeStateChanged?.((state) => {
    receivedState = true
    applyState(state)
  })
  void bridge.getWindowChromeState?.().then((state) => {
    // A native transition can arrive while the initial IPC request is pending.
    if (!receivedState) applyState(state)
  }).catch(() => undefined)

  const overlay = (navigator as Navigator & {
    windowControlsOverlay?: WindowControlsOverlay
  }).windowControlsOverlay
  const updateGeometry = () => {
    const rect = overlay?.getTitlebarAreaRect()
    root.dataset.windowControlsOverlay = String(
      Boolean(overlay?.visible && rect && rect.width > 0 && rect.height > 0),
    )
  }
  updateGeometry()
  overlay?.addEventListener('geometrychange', updateGeometry)

  return () => {
    active = false
    unsubscribe?.()
    overlay?.removeEventListener('geometrychange', updateGeometry)
    delete root.dataset.desktopPlatform
    delete root.dataset.windowFullscreen
    delete root.dataset.windowControlsOverlay
  }
}
