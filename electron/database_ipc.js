'use strict'

const fs = require('fs')
const path = require('path')

function removeDatabaseSidecars(dbPath, fsImpl = fs) {
  for (const suffix of ['-wal', '-shm']) {
    try { fsImpl.rmSync(`${dbPath}${suffix}`, { force: true }) } catch {}
  }
}

function samePath(first, second, pathImpl = path) {
  return pathImpl.resolve(first).toLowerCase() === pathImpl.resolve(second).toLowerCase()
}

function registerDatabaseIpcHandlers({
  ipcMain,
  dialog,
  shell,
  app,
  backendProcess,
  getMainWindow,
  backendUrl = backendProcess.backendUrl,
  fsImpl = fs,
  pathImpl = path,
  fetchImpl = globalThis.fetch,
  now = () => Date.now(),
  today = () => new Date().toISOString().slice(0, 10),
} = {}) {
  ipcMain.handle('export-database', async () => {
    try {
      const response = await fetchImpl(`${backendUrl}/api/database/export`, {
        method: 'POST',
      })
      if (!response.ok) return { success: false, error: '导出失败' }

      const buffer = Buffer.from(await response.arrayBuffer())
      const result = await dialog.showSaveDialog(getMainWindow(), {
        title: '导出数据库备份',
        defaultPath: `purrtypos-backup-${today()}.db`,
        filters: [{ name: '数据库文件', extensions: ['db'] }],
      })
      if (result.canceled || !result.filePath) {
        return { success: false, error: 'canceled' }
      }
      fsImpl.writeFileSync(result.filePath, buffer)
      return { success: true }
    } catch (error) {
      return { success: false, error: error.message }
    }
  })

  ipcMain.handle('import-database', async () => {
    try {
      let beforeStats = null
      try {
        const before = await backendProcess.fetchDatabaseInfo()
        if (before.success) beforeStats = before.data
      } catch {}

      const result = await dialog.showOpenDialog(getMainWindow(), {
        title: '选择要导入的数据库备份文件',
        filters: [
          { name: '数据库文件', extensions: ['db'] },
          { name: '全部', extensions: ['*'] },
        ],
        properties: ['openFile'],
      })
      if (result.canceled || result.filePaths.length === 0) {
        return { success: false, error: 'canceled' }
      }

      const sourcePath = result.filePaths[0]
      if (!fsImpl.existsSync(sourcePath)) {
        return { success: false, error: '备份文件不存在' }
      }

      const dbPath = pathImpl.join(app.getPath('userData'), 'purrtypos.db')
      if (samePath(sourcePath, dbPath, pathImpl)) {
        return { success: false, error: '不能导入当前正在使用的数据库文件' }
      }

      await backendProcess.stop()
      fsImpl.mkdirSync(pathImpl.dirname(dbPath), { recursive: true })
      if (fsImpl.existsSync(dbPath)) {
        fsImpl.copyFileSync(dbPath, `${dbPath}.before-import-${now()}.bak`)
      }
      fsImpl.copyFileSync(sourcePath, dbPath)
      removeDatabaseSidecars(dbPath, fsImpl)

      backendProcess.start()
      await backendProcess.waitUntilReady()

      let afterStats = null
      try {
        const after = await backendProcess.fetchDatabaseInfo()
        if (after.success) afterStats = after.data
      } catch {}

      const mainWindow = getMainWindow()
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload()
      return { success: true, data: { beforeStats, afterStats } }
    } catch (error) {
      try {
        if (!backendProcess.isRunning()) {
          backendProcess.start()
          await backendProcess.waitUntilReady()
        }
      } catch {}
      return { success: false, error: error.message }
    }
  })

  ipcMain.handle('open-database-directory', async () => {
    try {
      const info = await backendProcess.fetchDatabaseInfo()
      if (!info.success || !info.data?.dbPath) {
        return { success: false, error: '数据库路径未知' }
      }
      const openResult = await shell.openPath(pathImpl.dirname(info.data.dbPath))
      return openResult
        ? { success: false, error: openResult }
        : { success: true }
    } catch (error) {
      return { success: false, error: error.message }
    }
  })
}

module.exports = {
  registerDatabaseIpcHandlers,
  removeDatabaseSidecars,
  samePath,
}
