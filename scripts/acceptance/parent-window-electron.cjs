const { app, BrowserWindow } = require('electron')

const acceptanceUrl = process.env.PURRTYPOS_ACCEPTANCE_URL
if (!acceptanceUrl || !acceptanceUrl.startsWith('http://127.0.0.1:5175/')) {
  throw new Error('PURRTYPOS_ACCEPTANCE_URL must use the isolated local acceptance server')
}

app.setName('PurrTypos Stage Acceptance')

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1000,
    height: 820,
    show: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  await window.loadURL(acceptanceUrl)
})

app.on('window-all-closed', () => app.quit())
