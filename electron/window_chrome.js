// Keep the native controls aligned with --header-height in src/index.scss.
const HEADER_HEIGHT = 52
const THEMES = {
  light: { background: '#f4f1ed', foreground: '#292421' },
  dark: { background: '#171513', foreground: '#f6f0eb' },
}

function overlayOptions(theme) {
  return {
    color: '#00000000',
    symbolColor: THEMES[theme].foreground,
    height: HEADER_HEIGHT,
  }
}

function windowChromeOptions(platform, theme = 'light') {
  const backgroundColor = THEMES[theme].background
  if (platform === 'darwin') {
    return {
      backgroundColor,
      titleBarStyle: 'hidden',
      titleBarOverlay: true,
      trafficLightPosition: { x: 14, y: 18 },
    }
  }
  if (platform === 'win32') {
    return {
      backgroundColor,
      titleBarStyle: 'hidden',
      titleBarOverlay: overlayOptions(theme),
      autoHideMenuBar: true,
    }
  }
  return { backgroundColor, titleBarStyle: 'default' }
}

function createWindowChrome({ ipcMain, nativeTheme, getWindow, platform = process.platform }) {
  let theme = 'light'

  const senderWindow = (event) => {
    const window = getWindow()
    if (!window || window.isDestroyed()
      || event.sender !== window.webContents
      || event.senderFrame !== window.webContents.mainFrame) {
      throw new Error('Window chrome is only available to the main application frame')
    }
    return window
  }
  const state = (window) => ({ fullscreen: window.isFullScreen() })

  ipcMain.handle('set-window-theme', (event, value) => {
    const window = senderWindow(event)
    if (value !== 'light' && value !== 'dark') throw new TypeError('Invalid window theme')
    theme = value
    nativeTheme.themeSource = theme
    window.setBackgroundColor(THEMES[theme].background)
    if (platform === 'win32') window.setTitleBarOverlay(overlayOptions(theme))
  })
  ipcMain.handle('get-window-chrome-state', (event) => state(senderWindow(event)))

  return {
    getWindowOptions: () => windowChromeOptions(platform, theme),
    attachWindow(window) {
      const publishState = () => {
        if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
          window.webContents.send('window-chrome-state-changed', state(window))
        }
      }
      window.on('enter-full-screen', publishState)
      window.on('leave-full-screen', publishState)
    },
  }
}

module.exports = { createWindowChrome, windowChromeOptions }
