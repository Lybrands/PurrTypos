const { app, BrowserWindow, ipcMain, dialog, shell, protocol } = require('electron')
const path = require('path')
const fs = require('fs')
const { spawn } = require('child_process')

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

if (!isDev) {
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ])
}

let mainWindow = null
let pythonProcess = null
const BACKEND_PORT = 18321
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`

// ─── Python backend lifecycle ────────────────────────────────────

function getPythonCommand() {
  if (process.platform === 'win32') {
    try {
      require('child_process').execSync('py --version', { stdio: 'ignore' })
      return 'py'
    } catch {}
  }
  return 'python'
}

function getFrozenBackendExe(backendDir) {
  if (process.platform === 'win32') {
    const p = path.join(backendDir, 'purrtypos-backend.exe')
    return fs.existsSync(p) ? p : null
  }
  const unix = path.join(backendDir, 'purrtypos-backend')
  return fs.existsSync(unix) ? unix : null
}

function startPythonBackend() {
  if (pythonProcess) return

  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..', 'backend')

  const skillsDir = app.isPackaged
    ? path.join(process.resourcesPath, 'backend', 'skills')
    : path.join(__dirname, '..', 'backend', 'skills')

  const env = {
    ...process.env,
    PURRTYPOS_DATA_DIR: app.getPath('userData'),
    PURRTYPOS_SKILLS_DIR: skillsDir,
    PURRTYPOS_PORT: String(BACKEND_PORT),
  }

  const frozen = app.isPackaged ? getFrozenBackendExe(backendDir) : null
  if (frozen) {
    console.log('[python] starting frozen backend:', frozen)
    pythonProcess = spawn(frozen, [String(BACKEND_PORT)], {
      cwd: backendDir,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
  } else {
    const pythonCmd = getPythonCommand()
    console.log(`[python] using command: ${pythonCmd}`)
    pythonProcess = spawn(pythonCmd, ['-u', 'main.py', String(BACKEND_PORT)], {
      cwd: backendDir,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
  }

  pythonProcess.stdout.on('data', (d) => console.log('[python]', d.toString().trim()))
  pythonProcess.stderr.on('data', (d) => console.error('[python]', d.toString().trim()))
  pythonProcess.on('close', (code) => {
    console.log('[python] exited with code', code)
    pythonProcess = null
  })
}

async function waitForBackend(maxRetries = 30, intervalMs = 500) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(`${BACKEND_URL}/health`)
      if (res.ok) return true
    } catch {}
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('Python backend failed to start')
}

async function fetchDatabaseInfo() {
  const res = await fetch(`${BACKEND_URL}/api/database/info`)
  return await res.json()
}

async function stopPythonBackend(timeoutMs = 5000) {
  const proc = pythonProcess
  if (!proc) return
  await new Promise((resolve) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      if (pythonProcess === proc) pythonProcess = null
      resolve()
    }
    const timer = setTimeout(() => {
      try { proc.kill('SIGKILL') } catch (_) {}
      finish()
    }, timeoutMs)
    proc.once('close', () => {
      clearTimeout(timer)
      finish()
    })
    try {
      proc.kill()
    } catch (_) {
      clearTimeout(timer)
      finish()
    }
  })
}

function removeDatabaseSidecars(dbPath) {
  for (const suffix of ['-wal', '-shm']) {
    try { fs.rmSync(`${dbPath}${suffix}`, { force: true }) } catch (_) {}
  }
}

// ─── Window ──────────────────────────────────────────────────────

function getLogoPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'icon.ico')
  }
  return path.join(__dirname, '..', 'public', 'PurrTypos.png')
}

function createWindow() {
  const iconPath = getLogoPath()
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    icon: iconPath,
    webPreferences: {
      preload: path.join(__dirname, 'preload_python.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    titleBarStyle: 'default',
    show: false,
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
  } else {
    mainWindow.loadURL('app://./dist/index.html').catch((err) => {
      console.error('loadURL app:// failed:', err)
    })
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ─── App lifecycle ───────────────────────────────────────────────

app.whenReady().then(async () => {
  startPythonBackend()
  try {
    await waitForBackend()
    console.log('Python backend is ready')
  } catch (err) {
    console.error('Failed to start Python backend:', err)
  }

  if (!isDev) {
    const mimeTypes = {
      '.html': 'text/html; charset=utf-8',
      '.js': 'application/javascript; charset=utf-8',
      '.css': 'text/css; charset=utf-8',
      '.json': 'application/json',
      '.png': 'image/png',
      '.ico': 'image/x-icon',
      '.svg': 'image/svg+xml',
      '.woff2': 'font/woff2',
      '.woff': 'font/woff',
    }
    protocol.handle('app', (request) => {
      const u = new URL(request.url)
      const segments = decodeURIComponent(u.pathname).split('/').filter(Boolean)
      if (segments.length === 0) {
        return new Response('', { status: 404 })
      }
      const resourcesPath = process.resourcesPath
      const pathsToTry = [
        path.join(resourcesPath, ...segments),
        ...(segments[0] === 'assets' ? [path.join(resourcesPath, 'dist', ...segments)] : []),
        path.join(resourcesPath, 'app', ...segments),
        path.join(resourcesPath, 'app.asar.unpacked', ...segments),
        path.join(app.getAppPath(), ...segments),
      ]
      let buf = null
      let filePathUsed = ''
      for (const fp of pathsToTry) {
        try {
          buf = fs.readFileSync(fp)
          filePathUsed = fp
          break
        } catch (_) {}
      }
      if (buf) {
        const ext = path.extname(filePathUsed)
        const contentType = mimeTypes[ext] || 'application/octet-stream'
        return new Response(buf, { headers: { 'Content-Type': contentType } })
      }
      return new Response('', { status: 404 })
    })
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopPythonBackend()
})

// ─── IPC: native file operations ─────────────────────────────────

ipcMain.handle('open-xmind-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 XMind 文件',
    filters: [{ name: 'XMind 文件', extensions: ['xmind'] }],
    properties: ['openFile'],
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})

ipcMain.handle('parse-xmind', async (_, filePath) => {
  try {
    const AdmZip = require('adm-zip')
    const zip = new AdmZip(filePath)
    const contentEntry = zip.getEntry('content.json')
    if (!contentEntry) throw new Error('content.json 不存在于 XMind 文件中')
    const content = JSON.parse(contentEntry.getData().toString('utf8'))
    return { success: true, data: content }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('read-file-buffer', async (_, filePath) => {
  try {
    const buffer = fs.readFileSync(filePath)
    return { success: true, data: buffer }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('open-and-read-text-file', async () => {
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择要导入的文本文件',
      filters: [
        { name: '文本文件', extensions: ['txt', 'md', 'markdown'] },
        { name: '全部', extensions: ['*'] },
      ],
      properties: ['openFile'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }
    const filePath = result.filePaths[0]
    const content = fs.readFileSync(filePath, 'utf8')
    return { success: true, data: content }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('story-background-pick-attachments', async (_, { bookId }) => {
  try {
    if (bookId == null || bookId === undefined) {
      return { success: false, error: '书籍未选择' }
    }
    const win = mainWindow || BrowserWindow.getFocusedWindow()
    const result = await dialog.showOpenDialog(win, {
      title: '选择附件',
      properties: ['openFile', 'multiSelections'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }

    const attachmentDir = path.join(app.getPath('userData'), 'story-background-attachments', String(bookId))
    fs.mkdirSync(attachmentDir, { recursive: true })
    const attachments = []
    for (const fp of result.filePaths) {
      const name = path.basename(fp)
      const storedName = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}-${name}`
      const relativePath = path.join('story-background-attachments', String(bookId), storedName)
      fs.copyFileSync(fp, path.join(app.getPath('userData'), relativePath))
      attachments.push({ name, storedPath: relativePath })
    }

    const res = await fetch(`${BACKEND_URL}/api/story-background/${bookId}/attachments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attachments }),
    })
    return await res.json()
  } catch (err) {
    console.error('story-background-pick-attachments:', err)
    return { success: false, error: (err && err.message) || String(err) }
  }
})

ipcMain.handle('story-background-open-attachment', (_, { storedPath }) => {
  try {
    const full = path.join(app.getPath('userData'), storedPath)
    return shell.openPath(full)
  } catch (err) {
    return Promise.resolve(err.message)
  }
})

ipcMain.handle('write-export-files', async (_, { entries, exportAsZip }) => {
  try {
    const list = Array.isArray(entries) ? entries : []
    if (list.length === 0) return { success: false, error: '没有可导出的内容' }

    if (exportAsZip) {
      const result = await dialog.showSaveDialog(mainWindow, {
        title: '保存导出压缩包',
        defaultPath: '书籍导出.zip',
        filters: [{ name: 'ZIP 压缩包', extensions: ['zip'] }],
      })
      if (result.canceled || !result.filePath) return { success: false, error: 'canceled' }
      const AdmZip = require('adm-zip')
      const zip = new AdmZip()
      for (const { path: zipPath, content } of list) {
        zip.addFile(zipPath, Buffer.from(content || '', 'utf8'))
      }
      zip.writeZip(result.filePath)
      return { success: true }
    }

    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择导出目录',
      properties: ['openDirectory', 'createDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }

    const dir = result.filePaths[0]
    for (const { path: relativePath, content } of list) {
      const filePath = path.join(dir, relativePath)
      const dirPath = path.dirname(filePath)
      try { fs.mkdirSync(dirPath, { recursive: true }) } catch (_) {}
      fs.writeFileSync(filePath, content || '', 'utf8')
    }
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('export-database', async () => {
  try {
    const res = await fetch(`${BACKEND_URL}/api/database/export`, { method: 'POST' })
    if (!res.ok) return { success: false, error: '导出失败' }
    const buffer = Buffer.from(await res.arrayBuffer())
    const defaultName = `purrtypos-backup-${new Date().toISOString().slice(0, 10)}.db`
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '导出数据库备份',
      defaultPath: defaultName,
      filters: [{ name: '数据库文件', extensions: ['db'] }],
    })
    if (result.canceled || !result.filePath) return { success: false, error: 'canceled' }
    fs.writeFileSync(result.filePath, buffer)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('import-database', async () => {
  try {
    let beforeStats = null
    try {
      const before = await fetchDatabaseInfo()
      if (before.success) beforeStats = before.data
    } catch (_) {}

    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择要导入的数据库备份文件',
      filters: [
        { name: '数据库文件', extensions: ['db'] },
        { name: '全部', extensions: ['*'] },
      ],
      properties: ['openFile'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }

    const sourcePath = result.filePaths[0]
    if (!fs.existsSync(sourcePath)) return { success: false, error: '备份文件不存在' }

    const dbPath = path.join(app.getPath('userData'), 'purrtypos.db')
    if (path.resolve(sourcePath).toLowerCase() === path.resolve(dbPath).toLowerCase()) {
      return { success: false, error: '不能导入当前正在使用的数据库文件' }
    }

    await stopPythonBackend()
    fs.mkdirSync(path.dirname(dbPath), { recursive: true })
    if (fs.existsSync(dbPath)) {
      fs.copyFileSync(dbPath, `${dbPath}.before-import-${Date.now()}.bak`)
    }
    fs.copyFileSync(sourcePath, dbPath)
    removeDatabaseSidecars(dbPath)

    startPythonBackend()
    await waitForBackend()

    let afterStats = null
    try {
      const after = await fetchDatabaseInfo()
      if (after.success) afterStats = after.data
    } catch (_) {}

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.reload()
    }
    return { success: true, data: { beforeStats, afterStats } }
  } catch (err) {
    try {
      if (!pythonProcess) {
        startPythonBackend()
        await waitForBackend()
      }
    } catch (_) {}
    return { success: false, error: err.message }
  }
})

ipcMain.handle('open-database-directory', async () => {
  try {
    const res = await fetch(`${BACKEND_URL}/api/database/info`)
    const info = await res.json()
    if (!info.success || !info.data?.dbPath) return { success: false, error: '数据库路径未知' }
    const dir = path.dirname(info.data.dbPath)
    const openResult = await shell.openPath(dir)
    if (openResult) return { success: false, error: openResult }
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('open-file-path', async (_, filePath) => {
  try {
    if (!filePath || !fs.existsSync(filePath)) {
      return { success: false, error: '文件不存在：' + filePath }
    }
    const minDelay = new Promise((r) => setTimeout(r, 1500))

    const processReady = new Promise((resolve, reject) => {
      let child
      if (process.platform === 'win32') {
        child = spawn('cmd', ['/c', 'start', '""', filePath], { shell: false, stdio: 'ignore' })
      } else if (process.platform === 'darwin') {
        child = spawn('open', [filePath], { stdio: 'ignore' })
      } else {
        child = spawn('xdg-open', [filePath], { stdio: 'ignore' })
      }
      child.on('spawn', () => resolve())
      child.on('error', (err) => reject(err))
      child.unref()
    })

    await Promise.all([processReady, minDelay])
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})
