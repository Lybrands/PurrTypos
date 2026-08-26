'use strict'

const fs = require('fs')
const path = require('path')

function registerNovelSourceFileIpc({
  ipcMain,
  dialog,
  getMainWindow,
  fsImpl = fs,
  pathImpl = path,
} = {}) {
  ipcMain.handle('pick-novel-source-text-file', async () => {
    try {
      const result = await dialog.showOpenDialog(getMainWindow(), {
        title: '选择小说来源文件',
        filters: [{ name: 'TXT / Markdown', extensions: ['txt', 'md', 'markdown'] }],
        properties: ['openFile'],
      })
      if (result.canceled || result.filePaths.length === 0) {
        return { success: false, error: 'canceled' }
      }
      const filePath = result.filePaths[0]
      const extension = pathImpl.extname(filePath).toLowerCase()
      if (!['.txt', '.md', '.markdown'].includes(extension)) {
        return { success: false, error: '首版只支持 TXT、Markdown 文件' }
      }
      const buffer = fsImpl.readFileSync(filePath)
      let content
      try {
        content = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
      } catch {
        return { success: false, error: '文件不是有效的 UTF-8 文本' }
      }
      return {
        success: true,
        data: {
          fileName: pathImpl.basename(filePath),
          extension,
          byteCount: buffer.byteLength,
          content,
        },
      }
    } catch (error) {
      return { success: false, error: error.message }
    }
  })
}

module.exports = { registerNovelSourceFileIpc }
