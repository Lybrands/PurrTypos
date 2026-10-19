const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { test } = require('node:test')
const { createWindowChrome, windowChromeOptions } = require('./window_chrome')

function harness(platform = 'win32') {
  const handlers = new Map()
  const updates = []
  const window = new EventEmitter()
  let fullscreen = false
  let destroyed = false
  Object.assign(window, {
    isDestroyed: () => destroyed,
    isFullScreen: () => fullscreen,
    setBackgroundColor: (color) => updates.push({ background: color }),
    setTitleBarOverlay: (overlay) => updates.push({ overlay }),
    webContents: {
      mainFrame: {},
      isDestroyed: () => destroyed,
      send: (channel, state) => updates.push({ channel, state }),
    },
  })
  const nativeTheme = { themeSource: 'system' }
  const chrome = createWindowChrome({
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    nativeTheme,
    getWindow: () => window,
    platform,
  })
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame }
  return {
    chrome, window, updates, nativeTheme,
    invoke: (channel, value, sender = event) => handlers.get(channel)(sender, value),
    setFullscreen: (value) => { fullscreen = value },
    destroy: () => { destroyed = true },
  }
}

test('uses native controls on macOS and Windows, keeping the default Linux frame', () => {
  const mac = windowChromeOptions('darwin')
  assert.equal(mac.titleBarStyle, 'hidden')
  assert.equal(mac.titleBarOverlay, true)
  assert.deepEqual(mac.trafficLightPosition, { x: 14, y: 18 })
  assert.equal(mac.frame, undefined)
  const win = windowChromeOptions('win32')
  assert.equal(win.titleBarStyle, 'hidden')
  assert.deepEqual(win.titleBarOverlay, { color: '#00000000', symbolColor: '#292421', height: 52 })
  assert.equal(win.autoHideMenuBar, true)
  assert.equal(windowChromeOptions('linux').titleBarStyle, 'default')
  assert.equal(windowChromeOptions('linux').titleBarOverlay, undefined)
})

test('Windows controls follow both application themes and retain theme on a recreated window', () => {
  const h = harness()
  h.invoke('set-window-theme', 'dark')
  assert.equal(h.nativeTheme.themeSource, 'dark')
  assert.deepEqual(h.updates.at(-1), {
    overlay: { color: '#00000000', symbolColor: '#f6f0eb', height: 52 },
  })
  assert.equal(h.chrome.getWindowOptions().backgroundColor, '#171513')
  assert.equal(h.chrome.getWindowOptions().titleBarOverlay.symbolColor, '#f6f0eb')
  h.invoke('set-window-theme', 'light')
  assert.equal(h.nativeTheme.themeSource, 'light')
  assert.equal(h.updates.at(-1).overlay.symbolColor, '#292421')
  assert.equal(h.chrome.getWindowOptions().backgroundColor, '#f4f1ed')
})

test('macOS theme synchronization does not call the Windows overlay updater', () => {
  const h = harness('darwin')
  h.invoke('set-window-theme', 'dark')
  assert.equal(h.nativeTheme.themeSource, 'dark')
  assert.deepEqual(h.updates, [{ background: '#171513' }])
})

test('only accepts known themes and IPC from the main application frame', () => {
  const h = harness()
  for (const value of [null, {}, '__proto__', 'system', 'DARK']) {
    assert.throws(() => h.invoke('set-window-theme', value), /Invalid window theme/)
  }
  for (const event of [
    { sender: {}, senderFrame: h.window.webContents.mainFrame },
    { sender: h.window.webContents, senderFrame: {} },
  ]) {
    assert.throws(() => h.invoke('set-window-theme', 'dark', event), /main application frame/)
    assert.throws(() => h.invoke('get-window-chrome-state', undefined, event), /main application frame/)
  }
  assert.deepEqual(h.updates, [])
  h.destroy()
  assert.throws(() => h.invoke('set-window-theme', 'dark'), /main application frame/)
})

test('publishes native fullscreen transitions and restores initial state after reload', () => {
  const h = harness('darwin')
  h.chrome.attachWindow(h.window)
  assert.deepEqual(h.invoke('get-window-chrome-state'), { fullscreen: false })
  h.setFullscreen(true)
  h.window.emit('enter-full-screen')
  assert.deepEqual(h.updates.at(-1), {
    channel: 'window-chrome-state-changed', state: { fullscreen: true },
  })
  assert.deepEqual(h.invoke('get-window-chrome-state'), { fullscreen: true })
  h.setFullscreen(false)
  h.window.emit('leave-full-screen')
  assert.deepEqual(h.updates.at(-1).state, { fullscreen: false })
  h.destroy()
  h.window.emit('leave-full-screen')
  assert.equal(h.updates.length, 2)
})
