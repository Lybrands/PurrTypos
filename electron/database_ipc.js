'use strict'

const fs = require('fs')
const path = require('path')

function registerDatabaseIpcHandlers({
  ipcMain,
  dialog,
  shell,
  backendProcess,
  getMainWindow,
  backendUrl = backendProcess.backendUrl,
  fsImpl = fs,
  pathImpl = path,
  fetchImpl = globalThis.fetch,
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
        title: '导出完整项目备份',
        defaultPath: `purrtypos-backup-${today()}.purrbackup`,
        filters: [{ name: 'PurrTypos 完整备份', extensions: ['purrbackup'] }],
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
      const result = await dialog.showOpenDialog(getMainWindow(), {
        title: '选择要恢复的完整项目备份',
        filters: [
          { name: 'PurrTypos 完整备份', extensions: ['purrbackup'] },
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

      const buffer = fsImpl.readFileSync(sourcePath)
      const response = await fetchImpl(
        `${backendUrl}/api/database/import?fileName=${encodeURIComponent(pathImpl.basename(sourcePath))}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: buffer,
        },
      )
      const restored = await response.json()
      if (!response.ok || !restored.success) {
        return { success: false, error: restored.error || '恢复失败' }
      }
      if (restored.data?.restartRequired) {
        await backendProcess.stop()
        backendProcess.start()
        await backendProcess.waitUntilReady()
      }

      const mainWindow = getMainWindow()
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload()
      return restored
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
}
