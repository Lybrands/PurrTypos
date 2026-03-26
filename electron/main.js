const { app, BrowserWindow, ipcMain, dialog, shell, protocol, net } = require('electron')
const path = require('path')
const url = require('url')
const fs = require('fs')

// 开发环境判断
const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

// 生产环境用 app:// 协议，需在 ready 前注册为特权协议
if (!isDev) {
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }
  ])
}

// 使用 sql.js（纯 JS），需异步初始化
const Database = require('./database')
const { shortId8 } = require('./idUtils')
function getDb() {
  return Database
}

let mainWindow = null

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
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    titleBarStyle: 'default',
    show: false,
  })

  // 开发环境加载 Vite 服务，生产环境加载构建产物
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    // 使用 app:// 协议加载，避免 file:// 被 Chromium 拦截
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

app.whenReady().then(async () => {
  // 工具路由：技能目录（开发用 electron/skills，打包用 extraResources 的 skills）
  const skillsPath = app.isPackaged
    ? path.join(process.resourcesPath, 'skills')
    : path.join(__dirname, 'skills')
  toolRouter.setSkillsPath(skillsPath)

  // 生产环境：注册 app:// 协议
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
      // 依次尝试：extraResources 的 resources/dist、pathname 直接、app 内 dist、asar.unpacked、asar
      // 当 pathname 为 /assets/xxx（无 dist 前缀）时，先尝试 resources/dist/assets/xxx
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

  // 初始化数据库（sql.js 需异步加载）
  try {
    await Database.initDatabase()
  } catch (err) {
    console.error('数据库初始化失败:', err)
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// ─── IPC: 文件操作 ─────────────────────────────────────────────

// 打开 .xmind 文件选择对话框
ipcMain.handle('open-xmind-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 XMind 文件',
    filters: [{ name: 'XMind 文件', extensions: ['xmind'] }],
    properties: ['openFile'],
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})

// 读取并解析 .xmind 文件
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

// 读取文件为 Buffer（供渲染进程用 ArrayBuffer 加载 XMind Embed Viewer）
ipcMain.handle('read-file-buffer', async (_, filePath) => {
  try {
    const fs = require('fs')
    const buffer = fs.readFileSync(filePath)
    return { success: true, data: buffer }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 打开并读取文本文件（用于小说背景等导入）
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

// 小说背景附件：选择文件并复制到应用目录
ipcMain.handle('story-background-pick-attachments', async (_, { bookId }) => {
  try {
    if (bookId == null || bookId === undefined) {
      return { success: false, error: '书籍未选择' }
    }
    const win = mainWindow || require('electron').BrowserWindow.getFocusedWindow()
    const result = await dialog.showOpenDialog(win, {
      title: '选择附件',
      properties: ['openFile', 'multiSelections'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }
    const userData = app.getPath('userData')
    const dir = path.join(userData, 'story-background-attachments', String(bookId))
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
    let addedCount = 0
    const batchId = shortId8()
    for (let i = 0; i < result.filePaths.length; i++) {
      const filePath = result.filePaths[i]
      try {
        const name = path.basename(filePath)
        const safeName = `${batchId}_${i}_${name}`.replace(/[^a-zA-Z0-9._-]/g, '_')
        const destPath = path.join(dir, safeName)
        const relativePath = path.join('story-background-attachments', String(bookId), safeName)
        fs.copyFileSync(filePath, destPath)
        getDb().addStoryBackgroundAttachment(bookId, name, relativePath)
        addedCount++
      } catch (fileErr) {
        console.error('附件导入失败:', filePath, fileErr)
        if (addedCount === 0) return { success: false, error: (fileErr && fileErr.message) || String(fileErr) }
      }
    }
    // 成功后从数据库重新查列表返回，避免 sql.js 返回对象序列化导致前端收不到
    const list = getDb().getStoryBackgroundAttachments(bookId) || []
    const data = list.map((row) => ({
      id: Number(row.id),
      book_id: Number(row.book_id),
      name: String(row.name != null ? row.name : ''),
      stored_path: String(row.stored_path != null ? row.stored_path : ''),
      create_time: row.create_time != null ? String(row.create_time) : '',
    }))
    return { success: true, data, addedCount }
  } catch (err) {
    console.error('story-background-pick-attachments:', err)
    return { success: false, error: (err && err.message) || String(err) }
  }
})

ipcMain.handle('db-get-story-background-attachments', (_, { bookId }) => {
  try {
    return { success: true, data: getDb().getStoryBackgroundAttachments(bookId) || [] }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-delete-story-background-attachment', (_, { id }) => {
  try {
    const storedPath = getDb().deleteStoryBackgroundAttachment(id)
    if (storedPath) {
      const full = path.join(app.getPath('userData'), storedPath)
      try { fs.unlinkSync(full) } catch (_) {}
    }
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
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

// 写入导出文件：由渲染进程传入已构建的 entries（见 src/utils/exportBooks.ts），主进程仅负责选路径与写入
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

// 数据库导出：另存为 .db 备份文件
ipcMain.handle('export-database', async () => {
  try {
    const buffer = Database.exportToBuffer()
    if (!buffer) return { success: false, error: '数据库未就绪' }
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

// 数据库导入：选择备份文件覆盖当前数据库，完成后需重启应用以生效
ipcMain.handle('import-database', async () => {
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择要导入的数据库备份文件',
      filters: [{ name: '数据库文件', extensions: ['db'] }, { name: '全部', extensions: ['*'] }],
      properties: ['openFile'],
    })
    if (result.canceled || result.filePaths.length === 0) return { success: false, error: 'canceled' }
    const sourcePath = result.filePaths[0]
    const dbPath = Database.getDbPath()
    if (!dbPath) return { success: false, error: '数据库路径未知' }
    Database.closeDatabase()
    fs.copyFileSync(sourcePath, dbPath)
    await Database.initDatabase()
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.reload()
    }
    return { success: true }
  } catch (err) {
    try { await Database.initDatabase() } catch (_) {}
    return { success: false, error: err.message }
  }
})

// ─── IPC: 数据库操作 ───────────────────────────────────────────

// 保存大纲 { title, type?: 'global'|'chapter' }
ipcMain.handle('db-save-outline', (_, data) => {
  try {
    const result = getDb().saveOutline(data)
    return { success: true, data: result }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取所有大纲（可选 type: 'global'|'chapter'）
ipcMain.handle('db-get-outlines', (_, typeFilter) => {
  try {
    return { success: true, data: getDb().getOutlines(typeFilter) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 书籍 CRUD
ipcMain.handle('db-get-books', () => {
  try { return { success: true, data: getDb().getBooks() } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-create-book', (_, { title, enableVolume }) => {
  try { return { success: true, data: getDb().createBook(title, enableVolume) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-delete-book', (_, { bookId }) => {
  try {
    const result = getDb().deleteBook(bookId)
    if (result && result.attachmentPaths && result.attachmentPaths.length) {
      const userData = app.getPath('userData')
      for (const rel of result.attachmentPaths) {
        try { fs.unlinkSync(path.join(userData, rel)) } catch (_) {}
      }
    }
    return { success: true }
  } catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-rename-book', (_, { bookId, title }) => {
  try { getDb().renameBook(bookId, title); return { success: true } }
  catch (err) { return { success: false, error: err.message } }
})

// 人物 CRUD
ipcMain.handle('db-get-characters', (_, { bookId }) => {
  try { return { success: true, data: getDb().getCharacters(bookId) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-create-character', (_, { bookId, data }) => {
  try { return { success: true, data: getDb().createCharacter(bookId, data) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-update-character', (_, { id, data }) => {
  try { return { success: true, data: getDb().updateCharacter(id, data) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-delete-character', (_, { id }) => {
  try { getDb().deleteCharacter(id); return { success: true } }
  catch (err) { return { success: false, error: err.message } }
})

// 人物选项 CRUD（性格 / 标签候选值）
ipcMain.handle('db-get-character-options', (_, { category }) => {
  try { return { success: true, data: getDb().getCharacterOptions(category) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-add-character-option', (_, { category, value }) => {
  try { return { success: true, data: getDb().addCharacterOption(category, value) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-update-character-option', (_, { id, value }) => {
  try { return { success: true, data: getDb().updateCharacterOption(id, value) } }
  catch (err) { return { success: false, error: err.message } }
})
ipcMain.handle('db-delete-character-option', (_, { id }) => {
  try { getDb().deleteCharacterOption(id); return { success: true } }
  catch (err) { return { success: false, error: err.message } }
})

// 获取卷大纲（含各卷下章节大纲）
ipcMain.handle('db-get-volume-outlines', (_, bookId) => {
  try { return { success: true, data: getDb().getVolumeOutlines(bookId) } }
  catch (err) { return { success: false, error: err.message } }
})
// 通过 writingChapterId 查找对应 outline 记录
ipcMain.handle('db-get-outline-by-writing-chapter', (_, writingChapterId) => {
  try { return { success: true, data: getDb().getOutlineByWritingChapterId(writingChapterId) } }
  catch (err) { return { success: false, error: err.message } }
})

// 获取全局大纲（支持 bookId）
ipcMain.handle('db-get-global-outline', (_, bookId) => {
  try {
    return { success: true, data: getDb().getGlobalOutline(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取或创建总纲行（可无 XMind，仅用于文本大纲）
ipcMain.handle('db-ensure-global-outline', (_, bookId) => {
  try {
    return { success: true, data: getDb().getOrCreateGlobalOutline(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取或创建写作章节专属大纲（支持 bookId）
ipcMain.handle('db-get-writing-outline', (_, bookId) => {
  try {
    return { success: true, data: getDb().getOrCreateWritingOutline(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取章大纲列表（支持 bookId）
ipcMain.handle('db-get-chapter-outlines', (_, bookId) => {
  try {
    return { success: true, data: getDb().getChapterOutlines(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取其他大纲列表（支持 bookId）
ipcMain.handle('db-get-other-outlines', (_, bookId) => {
  try {
    return { success: true, data: getDb().getOtherOutlines(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 删除大纲
ipcMain.handle('db-delete-outline', (_, { outlineId }) => {
  try {
    getDb().deleteOutline(outlineId)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 通用配置（存数据库）
ipcMain.handle('db-get-settings', () => {
  try {
    return { success: true, data: getDb().getSettings() }
  } catch (err) {
    return { success: false, error: err.message }
  }
})
ipcMain.handle('db-set-settings', (_, data) => {
  try {
    getDb().setSettings(data)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 更新大纲（标题 / XMind / Markdown 等，字段可选）
ipcMain.handle('db-update-outline', (_, data) => {
  try {
    const result = getDb().updateOutline(data)
    return { success: true, data: result }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 保存章节列表（批量）
ipcMain.handle('db-save-chapters', (_, { outlineId, chapters }) => {
  try {
    getDb().saveChapters(outlineId, chapters)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取章节列表
ipcMain.handle('db-get-chapters', (_, { outlineId }) => {
  try {
    return { success: true, data: getDb().getChapters(outlineId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 新增单个章节（parentId 可选，分卷时用于指定所属卷）
ipcMain.handle('db-add-chapter', (_, { outlineId, title, parentId }) => {
  try {
    const result = getDb().addChapter(outlineId, title, parentId)
    return { success: true, data: result }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 删除章节
ipcMain.handle('db-delete-chapter', (_, data) => {
  try {
    const cid = data.chapterId ?? data.id
    getDb().deleteChapter(cid)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 重命名章节
ipcMain.handle('db-rename-chapter', (_, data) => {
  try {
    const cid = data.chapterId ?? data.id
    getDb().renameChapter(cid, data.title)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 更新章节进度
ipcMain.handle('db-update-chapter-progress', (_, { chapterId, progress }) => {
  try {
    getDb().updateChapterProgress(chapterId, progress)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 保存文档内容（自动存）
ipcMain.handle('db-save-article', (_, { chapterId, content }) => {
  try {
    getDb().saveArticle(chapterId, content)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 获取文档内容
ipcMain.handle('db-get-article', (_, { chapterId }) => {
  try {
    return { success: true, data: getDb().getArticle(chapterId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 小说背景
ipcMain.handle('db-get-story-background', (_, { bookId }) => {
  try {
    return { success: true, data: getDb().getStoryBackground(bookId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-save-story-background', (_, { bookId, content }) => {
  try {
    getDb().saveStoryBackground(bookId, content)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 保存 AI 对话
// AI 会话管理
ipcMain.handle('db-create-session', (_, { bookId, chapterId }) => {
  try {
    return { success: true, data: getDb().createSession(bookId, chapterId ?? null) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-get-sessions', (_, { bookId, chapterId, includeClosed }) => {
  try {
    return { success: true, data: getDb().getSessions(bookId, chapterId ?? null, includeClosed) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-set-session-closed', (_, { sessionId }) => {
  try {
    getDb().setSessionClosed(sessionId)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-set-session-reopened', (_, { sessionId }) => {
  try {
    getDb().setSessionReopened(sessionId)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-delete-session', (_, { sessionId }) => {
  try {
    getDb().deleteSession(sessionId)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-save-conversation', (_, { sessionId, chapterId, prompt, response, model, thinking, toolCallSegments, thinkingBlocks }) => {
  try {
    const toolCallSegmentsJson = toolCallSegments != null && Array.isArray(toolCallSegments)
      ? JSON.stringify(toolCallSegments)
      : null
    const thinkingBlocksJson = thinkingBlocks != null && Array.isArray(thinkingBlocks)
      ? JSON.stringify(thinkingBlocks)
      : null
    getDb().saveConversation(sessionId, chapterId, prompt, response, model, thinking, toolCallSegmentsJson, thinkingBlocksJson)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-get-conversations', (_, { sessionId }) => {
  try {
    return { success: true, data: getDb().getConversations(sessionId) }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-delete-conversations-after-turn', (_, { sessionId, keepTurnCount }) => {
  try {
    getDb().deleteConversationsAfterTurn(sessionId, keepTurnCount)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-save-ai-favorite', (_, { sessionId, sessionTitle, prompt, content }) => {
  try {
    const row = getDb().saveAiFavorite(sessionId, sessionTitle, prompt, content)
    return { success: true, data: row }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-get-ai-favorites', () => {
  try {
    return { success: true, data: getDb().getAiFavorites() }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

ipcMain.handle('db-delete-ai-favorite', (_, { id }) => {
  try {
    getDb().deleteAiFavorite(id)
    return { success: true }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

// 长期记忆（五层）— 仅使用 mem0，不再使用数据库记忆
const mem0Service = require('./mem0Service')
const toolRouter = require('./toolRouter')
const { buildToolRouterEmbeddingQuery } = require('./toolRouterQueryText')
const { normalizeSessionTitle } = require('./sessionTitle')
const toolExecutor = require('./toolExecutor')
const skillOrchestrator = require('./skillOrchestrator')
const { getSkillSpecs } = require('./agentToolDefinitions')
const {
  buildCollabSystemPrompt,
  buildCollabTurnAppendix,
  filterCollabTools,
} = require('./collabPrompt')
const { EXEC_ACTIONS } = require('./subagentConfig')
const { runSubagentPipeline } = require('./subagentPipeline')

/** 将 mem0 相关错误转为对用户/模型友好的提示，并打印原始错误到控制台 */
function mem0ErrMessage(err) {
  const msg = (err && err.message) || String(err)
  console.error('[mem0]', err && err.code, msg, err && err.stack)
  if (/ECONNREFUSED|127\.0\.0\.1:11434|localhost:11434|fetch failed|socket hang up/i.test(msg)) {
    return '长期记忆需要 Ollama 在本地运行。请先打开 Ollama 应用（或从开始菜单启动），再试一次。'
  }
  if (/Cannot find module|MODULE_NOT_FOUND/i.test(msg)) {
    return '记忆服务依赖未正确加载：' + msg + '。请确认已安装 better-sqlite3 并执行过 electron-rebuild（或 npm run postinstall）。'
  }
  if (/better-sqlite3|Error: The module|was compiled against/i.test(msg)) {
    return 'better-sqlite3 需针对 Electron 重新编译。请在项目目录执行：npx electron-rebuild -f -w better-sqlite3，然后完全退出并重启应用。'
  }
  if (/nomic-embed|embed.*model|ollama.*pull/i.test(msg)) {
    return '请先在终端执行：ollama pull nomic-embed-text，再试添加记忆。'
  }
  if (/mem0 需要 Embedder|OPENAI_API_KEY/i.test(msg)) {
    return '未检测到本地 Ollama 或 OpenAI 配置。请安装并启动 Ollama，或设置 OPENAI_API_KEY 后重试。'
  }
  return msg
}

ipcMain.handle('db-add-memory', async (_, { bookId, layer, content, chapterId, characterId }) => {
  try {
    const row = await mem0Service.addMemory(bookId, layer, content, chapterId ?? null, characterId ?? null)
    return { success: true, data: row }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-update-memory', async (_, { id, data }) => {
  try {
    const row = await mem0Service.updateMemory(id, data)
    return { success: true, data: row }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-delete-memory', async (_, { id }) => {
  try {
    await mem0Service.deleteMemory(id)
    return { success: true }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-memories-by-book', async (_, { bookId, layer }) => {
  try {
    const data = await mem0Service.getMemoriesByBook(bookId, layer || null)
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-memories-by-ids', async (_, { ids }) => {
  try {
    const data = await mem0Service.getMemoriesByIds(ids || [])
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-memories-for-prompt', async (_, { bookId, query, options }) => {
  try {
    const data = await mem0Service.getMemoriesForPrompt(bookId, query || '', options || {})
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})

// 伏笔记忆 — 仅 mem0
ipcMain.handle('db-add-foreshadowing', async (_, { bookId, chapterId, content, type, expectedChapterId }) => {
  try {
    const row = await mem0Service.addForeshadowing(bookId, chapterId, content, type, expectedChapterId ?? null)
    return { success: true, data: row }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-update-foreshadowing', async (_, { id, data }) => {
  try {
    const row = await mem0Service.updateForeshadowing(id, data)
    return { success: true, data: row }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-delete-foreshadowing', async (_, { id }) => {
  try {
    await mem0Service.deleteForeshadowing(id)
    return { success: true }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-foreshadowing-by-book', async (_, { bookId, status }) => {
  try {
    const data = await mem0Service.getForeshadowingByBook(bookId, status || null)
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-foreshadowing-by-ids', async (_, { ids }) => {
  try {
    const data = await mem0Service.getForeshadowingByIds(ids || [])
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})
ipcMain.handle('db-get-foreshadowing-for-prompt', async (_, { bookId, query, options }) => {
  try {
    const data = await mem0Service.getForeshadowingForPrompt(bookId, query || '', options || {})
    return { success: true, data }
  } catch (err) {
    return { success: false, error: mem0ErrMessage(err) }
  }
})

// 用系统默认程序打开文件，等待进程启动 + 最小 1.5s 视觉延迟
ipcMain.handle('open-file-path', async (_, filePath) => {
  try {
    if (!filePath || !fs.existsSync(filePath)) {
      return { success: false, error: '文件不存在：' + filePath }
    }
    const { spawn } = require('child_process')
    const minDelay = new Promise(r => setTimeout(r, 1500))

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

// ─── AI Provider 适配器 ──────────────────────────────────────────
const openaiChat = require('./openaiChat')
const anthropicChat = require('./anthropicChat')
const { normalizeToolCallsList } = require('./toolCallUtils')

let _activeAbortController = null

async function createChatStream(key, messages, requestParams, apiProvider, signal) {
  if (apiProvider === 'anthropic') {
    return anthropicChat.chatStreamAsOpenAIFormat(key, messages, requestParams, signal)
  }
  return openaiChat.chatStream(key, messages, requestParams, signal)
}

ipcMain.on('ai-abort-stream', () => {
  if (_activeAbortController) {
    _activeAbortController.abort()
    _activeAbortController = null
  }
})

ipcMain.on('debug-log', (_, payload = {}) => {
  const safePayload = {
    sessionId: '6b0872',
    runId: payload.runId || 'pre-fix',
    hypothesisId: payload.hypothesisId || 'H-IPC',
    location: payload.location || 'unknown',
    message: payload.message || 'debug-log',
    data: payload.data && typeof payload.data === 'object' ? payload.data : {},
    timestamp: Date.now(),
  }
  fetch('http://127.0.0.1:7307/ingest/975adc99-3dce-41a7-9a23-f9567d0436ab', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': '6b0872' },
    body: JSON.stringify(safePayload),
  }).catch(() => {})
})

// 获取账号实际可用模型列表（需提供接口地址）
ipcMain.handle('ai-list-models', async (_, { apiKey, baseURL, apiProvider = 'openai' }) => {
  const key = (apiKey || '').trim()
  if (!key) return { success: false, error: 'API Key 为空', data: [] }
  const url = typeof baseURL === 'string' ? baseURL.trim() : ''

  if (apiProvider === 'anthropic') {
    const Anthropic = require('@anthropic-ai/sdk')
    try {
      const client = new Anthropic({
        apiKey: key,
        baseURL: url ? url.replace(/\/+$/, '') : undefined,
      })
      const ids = []
      for await (const m of client.models.list()) {
        if (m && m.id) ids.push(m.id)
      }
      return { success: true, data: ids }
    } catch (e) {
      return { success: false, error: e.message, data: [] }
    }
  }

  if (!url) return { success: false, error: '请填写接口地址', data: [] }

  const OpenAI = require('openai')
  const client = new OpenAI({ apiKey: key, baseURL: url.replace(/\/+$/, '') })
  try {
    const list = await client.models.list()
    const ids = (list.data || []).map((m) => m.id)
    return { success: true, data: ids }
  } catch (e) {
    return { success: false, error: e.message, data: [] }
  }
})

// ─── IPC: AI 流式调用 ──────────────────────────────────────────
// 使用 SSE 流式推送，每个 chunk 通过 ai-chat-chunk 事件发送到渲染进程

ipcMain.handle('db-update-session-title', (_, { sessionId, title }) => {
  try {
    getDb().updateSessionTitle(sessionId, title)
    return { success: true }
  } catch (e) {
    return { success: false, error: e.message }
  }
})

/** 模型返回空标题时，从 prompt 里抽用户首行，再规范为最多 10 字 */
function fallbackSessionTitleFromPrompt(prompt) {
  const s = String(prompt || '')
  const m = s.match(/User:\s*([\s\S]*?)(?:\n\nAssistant:|$)/)
  const userPart = (m ? m[1] : s).trim()
  const line =
    userPart
      .split(/\r?\n/)
      .map((x) => x.trim())
      .find((x) => x.length > 0) || userPart
  const t = line.replace(/\s+/g, ' ').trim()
  return normalizeSessionTitle(t)
}

// 非流式：用「当前所选 model」生成 Tab 标题（temperature=1、禁思考、≤10 字、不流式）
ipcMain.handle('ai-generate-title', async (_, { apiKey, baseURL, prompt, apiProvider = 'openai', model }) => {
  const key = (apiKey || '').trim()
  if (!key) return { success: false, error: 'API Key 为空' }
  console.log('[ai-generate-title] request', {
    apiProvider,
    model: typeof model === 'string' ? model.trim() : '',
    hasBaseURL: Boolean((baseURL || '').trim()),
    promptLength: String(prompt || '').length,
  })

  try {
    if (apiProvider === 'anthropic') {
      const requestedModel = (typeof model === 'string' && model.trim()) ? model.trim() : ''
      const normalizedBaseURL = (baseURL && baseURL.trim()) ? baseURL.trim().replace(/\/+$/, '') : undefined
      if (!requestedModel) {
        return { success: false, error: '缺少模型参数' }
      }
      let title = await anthropicChat.generateTitle(key, prompt, {
        model: requestedModel,
        baseURL: normalizedBaseURL,
      })
      title = String(title || '').trim()
      if (!title) {
        title = fallbackSessionTitleFromPrompt(prompt)
        if (title) console.log('[ai-generate-title] anthropic used prompt fallback')
        else console.warn('[ai-generate-title] anthropic empty title', { model: requestedModel })
      }
      if (!title) return { success: false, error: '标题生成结果为空' }
      console.log('[ai-generate-title] 生成标题:', title)
      return { success: true, data: title }
    }
    const requestedModel = (typeof model === 'string' && model.trim()) ? model.trim() : ''
    if (!requestedModel) {
      return { success: false, error: '缺少模型参数' }
    }
    const normalizedBaseURL = (baseURL && baseURL.trim()) ? baseURL.trim().replace(/\/+$/, '') : undefined
    let title = await openaiChat.generateTitle(key, prompt, {
      model: requestedModel,
      baseURL: normalizedBaseURL,
    })
    title = String(title || '').trim()
    if (!title) {
      title = fallbackSessionTitleFromPrompt(prompt)
      if (title) console.log('[ai-generate-title] openai used prompt fallback')
    }
    if (!title) return { success: false, error: '标题生成结果为空' }
    console.log('[ai-generate-title] 生成标题:', title)
    return { success: true, data: title }
  } catch (e) {
    return { success: false, error: e.message }
  }
})

// 将流式 delta.tool_calls 按 index 合并为完整 tool_calls 列表，格式与 chatNoStream 一致
function mergeStreamToolCalls(accumulated, deltaToolCalls) {
  if (!Array.isArray(deltaToolCalls)) return accumulated
  const next = accumulated.slice()
  for (const tc of deltaToolCalls) {
    const i = tc.index ?? next.length
    while (next.length <= i) next.push({ id: '', type: 'function', function: { name: '', arguments: '' } })
    const cur = next[i]
    if (tc.id != null) cur.id = (cur.id || '') + (tc.id || '')
    if (tc.type != null) cur.type = tc.type || cur.type
    if (tc.function) {
      if (tc.function.name != null) cur.function.name = (cur.function.name || '') + (tc.function.name || '')
      if (tc.function.arguments != null) cur.function.arguments = (cur.function.arguments || '') + (tc.function.arguments || '')
    }
    next[i] = cur
  }
  return next
}

ipcMain.on('ai-chat-stream', async (event, {
  messages,
  apiKey,
  baseURL,
  apiProvider = 'openai',
  options = {},
  tools: toolsFromFront,
  useToolRouter = false,
  bookId,
  bookTitle,
  chapterId,
  currentChapterTitle,
  writingChapters = [],
  availableOutlines = [],
  associatedChapterIds,
  associatedOutlineIds,
  agentMode,
  /** legacy 下协作共创：'collab'；默认 'default' */
  writingMode: writingModeFromFront = 'default',
  agentAction,
  agentActions: rawAgentActions,
}) => {
  const { model, temperature, ...rest } = options
  const key = (apiKey || '').trim()
  if (!key) {
    event.sender.send('ai-chat-chunk', { error: 'API Key 为空，请先在设置中添加模型并填写 API Key' })
    return
  }

  if (!model || typeof model !== 'string' || !model.trim()) {
    event.sender.send('ai-chat-chunk', { error: '无效的模型参数' })
    return
  }

  const abortController = new AbortController()
  _activeAbortController = abortController

  const sendChunk = (chunk) => event.sender.send('ai-chat-chunk', chunk)
  const skillSpecs = getSkillSpecs()
  let latestUserTextForPlan = ''
  if (Array.isArray(messages) && messages.length > 0) {
    const lastUser = [...messages].reverse().find((m) => m && m.role === 'user')
    latestUserTextForPlan = String(lastUser?.content || '')
  }

  const settings = getDb().getSettings?.() || {}
  const persistedMode = settings.ai_agent_mode === 'subagent' ? 'subagent' : 'legacy'
  const runtimeMode = agentMode === 'subagent' || agentMode === 'legacy' ? agentMode : persistedMode
  const collabWriting =
    runtimeMode === 'legacy' &&
    (writingModeFromFront === 'collab' || writingModeFromFront === true)
  const action = Object.values(EXEC_ACTIONS).includes(agentAction) ? agentAction : EXEC_ACTIONS.FULL
  const allowed = new Set(Object.values(EXEC_ACTIONS))
  const normalizedAgentActions =
    Array.isArray(rawAgentActions) && rawAgentActions.length > 0
      ? rawAgentActions.filter((x) => allowed.has(x))
      : null

  let tools = toolsFromFront
  if (useToolRouter && bookId != null && Array.isArray(messages) && messages.length > 0) {
    const userText = buildToolRouterEmbeddingQuery(messages)
    latestUserTextForPlan = userText
    try {
      const routed = await toolRouter.getToolsForQuery(userText)
      tools = routed.tools || []
      if (Array.isArray(routed.warnings) && routed.warnings.length > 0) {
        sendChunk({ toolRouterWarning: routed.warnings.join(' ') })
      }
      if (routed?.candidates?.length) {
        console.log(
          '[skill-orchestrator][retrieve]',
          routed.candidates.map((x) => `${x.name}:${Number(x.score || 0).toFixed(4)}`).join(', '),
        )
      }
      if (routed?.intent?.likelySkills?.length) {
        console.log('[skill-orchestrator][intent] likelySkills:', routed.intent.likelySkills.join(', '))
      }
    } catch (err) {
      tools = []
      sendChunk({
        toolRouterWarning: `工具路由调用失败，${(err && err.message) || String(err)}`,
      })
    }
  }

  if (collabWriting && Array.isArray(tools) && tools.length > 0) {
    tools = filterCollabTools(tools, latestUserTextForPlan)
  }

  const messagesForModel = (() => {
    const base = Array.isArray(messages) ? [...messages] : []
    if (!collabWriting || base.length === 0) return base
    const extra =
      `${buildCollabSystemPrompt({ challengeLevel: 'medium', generationStrategy: 'outline_then_draft' })}\n\n${buildCollabTurnAppendix(base)}`
    const sysIdx = base.findIndex((m) => m && m.role === 'system')
    if (sysIdx >= 0) {
      return base.map((m, i) =>
        i === sysIdx
          ? { ...m, content: `${String(m.content || '')}\n\n${extra}` }
          : m,
      )
    }
    return [{ role: 'system', content: extra }, ...base]
  })()

  const requestParams = { model: model.trim(), ...rest, baseURL: (baseURL && baseURL.trim()) ? baseURL.trim().replace(/\/+$/, '') : undefined }
  if (temperature !== undefined && temperature !== null) {
    requestParams.temperature = temperature
  }
  if (tools && tools.length > 0) requestParams.tools = tools
  const normAccCh =
    Array.isArray(associatedChapterIds)
      ? [...new Set(associatedChapterIds.map((x) => String(x)).filter((s) => s.length > 0))]
      : []
  const normAccOl =
    Array.isArray(associatedOutlineIds)
      ? [...new Set(associatedOutlineIds.map((x) => String(x)).filter((s) => s.length > 0))]
      : []
  const toolCtx = {
    bookId,
    bookTitle: bookTitle != null ? String(bookTitle) : undefined,
    chapterId,
    /** legacy 协作共创开关：工具层可据此执行增量写入与回传最新段落 */
    collabWriting,
    currentChapterTitle,
    writingChapters,
    availableOutlines,
    associatedChapterIds: normAccCh,
    associatedOutlineIds: normAccOl,
    /** 本会话内章节正文缓存（多阶段重复 getChapterContent 时复用，editChapterContent 对该章失效） */
    chapterContentCache: new Map(),
    /** 本会话内只读工具结果缓存（listOutlines/queryOutline/背景/人物目录等，大纲或总纲写入后整表清空） */
    readToolCache: new Map(),
  }

  const runStreamLoop = async (currentMessages) => {
    const { stream } = await createChatStream(
      key,
      currentMessages,
      requestParams,
      apiProvider,
      abortController.signal,
    )
    let accumulatedContent = ''
    let accumulatedThinking = ''
    let accumulatedToolCalls = []
    for await (const chunk of stream) {
      if (abortController.signal.aborted) break
      const delta = chunk.choices?.[0]?.delta || {}
      const contentDelta = delta.content || ''
      const thinkingDelta = delta.reasoning_content || ''
      if (thinkingDelta) {
        accumulatedThinking += thinkingDelta
        sendChunk({ thinkingDelta })
      }
      if (contentDelta) {
        accumulatedContent += contentDelta
        sendChunk({ delta: contentDelta })
      }
      const rawToolCalls = delta.tool_calls
      if (rawToolCalls && Array.isArray(rawToolCalls)) {
        accumulatedToolCalls = mergeStreamToolCalls(accumulatedToolCalls, rawToolCalls)
      }
      const finishReason = chunk.choices?.[0]?.finish_reason
      if (finishReason === 'stop' || finishReason === 'length') {
        sendChunk({ done: true, model })
        return null
      }
      if (finishReason === 'tool_calls') {
        const list = normalizeToolCallsList(accumulatedToolCalls)
        if (list.length === 0) {
          sendChunk({ done: true, model })
          return null
        }
        const plan = skillOrchestrator.planToolCalls({
          toolCalls: list,
          skillSpecs,
          toolCtx,
          latestUserText: latestUserTextForPlan,
        })
        const executableCalls = plan.executableCalls || list
        const toolNames = executableCalls.map((tc) => tc.function?.name).filter(Boolean)
        console.log('[ai-chat-stream] tool_calls(planned):', toolNames.join(', '))
        console.log('[skill-orchestrator][plan]', {
          nodes: plan?.telemetry?.nodes || 0,
          edges: plan?.telemetry?.edges || 0,
          insertedByDag: plan?.telemetry?.insertedByDag || 0,
        })
        sendChunk({
          toolCalls: executableCalls,
          toolCallsInProgress: true,
          partialContent: accumulatedContent,
          partialThinking: accumulatedThinking,
          messagesSent: currentMessages,
          orchestratorInfo: {
            insertedByDag: plan?.telemetry?.insertedByDag || 0,
            plannedNodeCount: plan?.telemetry?.nodes || executableCalls.length,
            insertedSkillNames: plan?.telemetry?.insertedSkillNames || [],
            plannedToolNames: plan?.telemetry?.finalCalls || toolNames,
          },
          model,
        })
        const executed = await skillOrchestrator.executeWithRepair({
          plannedCalls: executableCalls,
          toolCtx,
          latestUserText: latestUserTextForPlan,
          maxRepairRounds: 1,
          runTools: async (calls) =>
            toolExecutor.runTools(calls, toolCtx, (ev) => {
              if (!ev) return
              if (ev.chapterContentUpdated != null) sendChunk({ chapterContentUpdated: ev.chapterContentUpdated })
              if (Array.isArray(ev.toolReadCacheMask)) sendChunk({ toolReadCacheMask: ev.toolReadCacheMask })
              if (typeof ev.toolIndexCompleted === 'number') {
                sendChunk({
                  toolIndexCompleted: ev.toolIndexCompleted,
                  ...(ev.toolFromCache === true ? { toolFromCache: true } : {}),
                })
              }
            }),
        })
        if (executed.repairedRounds > 0) {
          console.log('[skill-orchestrator][repair] repairedRounds:', executed.repairedRounds)
          sendChunk({
            orchestratorRepair: {
              repairedRounds: executed.repairedRounds,
              events: Array.isArray(executed.repairEvents) ? executed.repairEvents : [],
            },
          })
        }
        const toolResults = executed.toolResults || []
        const usedCalls = executed.toolCalls || executableCalls
        const assistantMsg = {
          role: 'assistant',
          content: accumulatedContent,
          tool_calls: usedCalls.map((tc) => ({
            id: tc.id,
            type: 'function',
            function: { name: tc.function.name, arguments: tc.function.arguments },
          })),
          ...(accumulatedThinking ? { reasoning_content: accumulatedThinking } : {}),
        }
        const toolMsgs = toolResults.map((r) => ({ role: 'tool', tool_call_id: r.tool_call_id, content: r.content }))
        return [...currentMessages, assistantMsg, ...toolMsgs]
      }
    }
    sendChunk({ done: true, model, aborted: abortController.signal.aborted })
    return null
  }

  try {
    const responseModel = model
    if (runtimeMode === 'subagent') {
      await runSubagentPipeline({
        sendChunk,
        signal: abortController.signal,
        key,
        apiProvider,
        requestParams,
        toolCtx,
        skillSpecs,
        messages,
        latestUserTextForPlan,
        useToolRouter,
        toolsFromFront,
        action,
        agentActions:
          normalizedAgentActions && normalizedAgentActions.length > 0
            ? normalizedAgentActions
            : undefined,
        model: responseModel,
      })
    } else if (tools && tools.length > 0) {
      let currentMessages = messagesForModel
      while (currentMessages) {
        const next = await runStreamLoop(currentMessages)
        if (next === null) break
        currentMessages = next
      }
    } else {
      const { stream } = await createChatStream(
        key,
        messagesForModel,
        requestParams,
        apiProvider,
        abortController.signal,
      )
      let finished = false
      for await (const chunk of stream) {
        if (finished) break
        const choice0 = chunk.choices?.[0]
        const delta = choice0?.delta || {}
        const contentDelta = delta.content || ''
        const thinkingDelta = delta.reasoning_content || ''
        const msg = choice0?.message
        if (thinkingDelta) sendChunk({ thinkingDelta })
        if (contentDelta) sendChunk({ delta: contentDelta })
        const finishReason = choice0?.finish_reason
        if (finishReason === 'stop' || finishReason === 'length') {
          finished = true
          sendChunk({ done: true, model: responseModel })
        }
      }
      if (!finished) {
        sendChunk({ done: true, model: responseModel, aborted: abortController.signal.aborted })
      }
    }
  } catch (err) {
    if (abortController.signal.aborted) {
      sendChunk({ done: true, model, aborted: true })
    } else {
      sendChunk({ error: err.message || '请求失败' })
    }
  } finally {
    if (_activeAbortController === abortController) {
      _activeAbortController = null
    }
  }
})
