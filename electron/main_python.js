const { app, BrowserWindow, ipcMain, dialog, shell, protocol, nativeImage } = require('electron')
const path = require('path')
const fs = require('fs')
const { spawn } = require('child_process')
const {
  BACKEND_PORT,
  BACKEND_URL,
  createBackendProcessManager,
} = require('./backend_process')
const { createAppProtocolHandler } = require('./app_protocol')
const { registerDatabaseIpcHandlers } = require('./database_ipc')
const { registerNovelSourceFileIpc } = require('./source_file_ipc')
const { configureAppIcon } = require('./app_icon')

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

if (!isDev) {
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ])
}

let mainWindow = null
let appIcon = null
const backendProcess = createBackendProcessManager({ app })

// ─── Python backend lifecycle ────────────────────────────────────

const startPythonBackend = () => backendProcess.start()
const waitForBackend = (...args) => backendProcess.waitUntilReady(...args)
const stopPythonBackend = (...args) => backendProcess.stop(...args)

// ─── Window ──────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    icon: appIcon || undefined,
    webPreferences: {
      preload: path.join(__dirname, 'preload_python.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    titleBarStyle: 'default',
    show: false,
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5174')
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
  appIcon = configureAppIcon({
    app,
    nativeImage,
    resourcesPath: process.resourcesPath,
    moduleDir: __dirname,
  }).image

  startPythonBackend()
  try {
    await waitForBackend()
    console.log('Python backend is ready')
  } catch (err) {
    console.error('Failed to start Python backend:', err)
  }

  if (!isDev) {
    protocol.handle('app', createAppProtocolHandler({
      resourcesPath: process.resourcesPath,
      appPath: app.getAppPath(),
    }))
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

// 整本导出为单个 TXT 文件（保存对话框 + 写盘）
ipcMain.handle('write-single-text-file', async (_, { defaultName, content }) => {
  try {
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '保存导出文件',
      defaultPath: defaultName || '导出.txt',
      filters: [{ name: '文本文件', extensions: ['txt'] }],
    })
    if (result.canceled || !result.filePath) return { success: false, error: 'canceled' }
    fs.writeFileSync(result.filePath, content || '', 'utf8')
    return { success: true, data: { path: result.filePath } }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('write-screenplay-file', async (_, { defaultName, content, format }) => {
  try {
    const formats = {
      fountain: { extension: 'fountain', name: 'Fountain 剧本' },
      markdown: { extension: 'md', name: 'Markdown 文档' },
      txt: { extension: 'txt', name: '纯文本' },
      json: { extension: 'json', name: 'JSON 交付清单' },
    }
    const selected = formats[format] || formats.fountain
    const baseName = String(defaultName || '剧本').replace(/\.(fountain|md|txt|json)$/i, '')
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '导出剧本',
      defaultPath: `${baseName}.${selected.extension}`,
      filters: [{ name: selected.name, extensions: [selected.extension] }],
    })
    if (result.canceled || !result.filePath) {
      return { success: false, error: 'canceled' }
    }
    fs.writeFileSync(result.filePath, content || '', 'utf8')
    return { success: true, data: { path: result.filePath } }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('export-screenplay-pdf', async (_, { projectId, defaultName }) => {
  try {
    const res = await fetch(
      `${BACKEND_URL}/api/screenplay-projects/${encodeURIComponent(String(projectId))}/export/pdf`,
      { method: 'POST' },
    )
    if (!res.ok) {
      let error = 'PDF 生成失败'
      try {
        const body = await res.json()
        error = body.error || body.detail || error
      } catch (_) {}
      return { success: false, error }
    }
    const buffer = Buffer.from(await res.arrayBuffer())
    const baseName = String(defaultName || '剧本').replace(/\.pdf$/i, '')
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '导出标准剧本 PDF',
      defaultPath: `${baseName}.pdf`,
      filters: [{ name: 'PDF 文档', extensions: ['pdf'] }],
    })
    if (result.canceled || !result.filePath) {
      return { success: false, error: 'canceled' }
    }
    fs.writeFileSync(result.filePath, buffer)
    return { success: true, data: { path: result.filePath } }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// EPUB 导出：后端生成字节流，这里负责保存对话框与写盘
ipcMain.handle('export-epub', async (_, { bookId, chapterIds, defaultName }) => {
  try {
    const res = await fetch(`${BACKEND_URL}/api/export/epub`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bookId: String(bookId), chapterIds: chapterIds || null }),
    })
    const contentType = res.headers.get('content-type') || ''
    if (!res.ok) return { success: false, error: 'EPUB 生成失败' }
    if (contentType.includes('application/json')) {
      // 后端以 JSON 返回了业务错误
      const body = await res.json()
      return { success: false, error: body.error || 'EPUB 生成失败' }
    }
    const buffer = Buffer.from(await res.arrayBuffer())
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '导出 EPUB',
      defaultPath: `${defaultName || '书籍'}.epub`,
      filters: [{ name: 'EPUB 电子书', extensions: ['epub'] }],
    })
    if (result.canceled || !result.filePath) return { success: false, error: 'canceled' }
    fs.writeFileSync(result.filePath, buffer)
    return { success: true, data: { path: result.filePath } }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

registerDatabaseIpcHandlers({
  ipcMain,
  dialog,
  shell,
  app,
  backendProcess,
  getMainWindow: () => mainWindow,
})

registerNovelSourceFileIpc({
  ipcMain,
  dialog,
  getMainWindow: () => mainWindow,
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
