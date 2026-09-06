'use strict'
const crypto = require('node:crypto')

function selectionToken({ bookId, root, secret, now = Date.now() }) {
  const payload = Buffer.from(JSON.stringify({ bookId, root, issuedAt: Math.floor(now / 1000), nonce: crypto.randomUUID() })).toString('base64url')
  return `${payload}.${crypto.createHmac('sha256', secret).update(payload).digest('hex')}`
}

function assertOpenUri(uri) {
  const value = new URL(uri)
  if (value.protocol !== 'obsidian:' || value.hostname !== 'open' || !['', '/'].includes(value.pathname) || value.hash || value.username || value.password
      || [...value.searchParams.keys()].some((key) => key !== 'path') || value.searchParams.getAll('path').length !== 1) {
    throw new Error('无效的资料跳转地址')
  }
  return value.href
}

function registerNovelKnowledgeIpc({ ipcMain, dialog, shell, clipboard, getWindow, backendUrl, secret, fetchImpl = fetch }) {
  const trusted = (event) => {
    const window = getWindow()
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) throw new Error('窗口无权访问资料目录')
  }
  ipcMain.handle('novel-knowledge-select', async (event, bookId) => {
    try {
      trusted(event)
      if (typeof bookId !== 'string' || !bookId.trim()) throw new Error('作品不能为空')
      const result = await dialog.showOpenDialog(getWindow(), { title: '选择 Vault 内本小说的独立资料目录', properties: ['openDirectory'] })
      if (result.canceled || !result.filePaths[0]) return { success: false, error: 'canceled' }
      return { success: true, data: { selectionToken: selectionToken({ bookId, root: result.filePaths[0], secret }) } }
    } catch (error) { return { success: false, error: error.message } }
  })
  ipcMain.handle('novel-knowledge-open', async (event, args) => {
    try {
      trusted(event)
      if (!args || typeof args.bookId !== 'string' || typeof args.documentId !== 'string') throw new Error('资料来源不能为空')
      const response = await fetchImpl(`${backendUrl}/api/books/${encodeURIComponent(args.bookId)}/knowledge/navigation`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Knowledge-Host': secret },
        body: JSON.stringify({ documentId: args.documentId, revision: args.revision, anchor: args.anchor }),
      })
      const result = await response.json()
      if (!response.ok || !result.success) throw new Error(result.error || '资料来源不可用')
      const source = result.data
      if (args.action === 'copy') {
        clipboard.writeText(source.path)
        return { success: true, data: { status: 'copied' } }
      }
      if (args.action === 'reveal') {
        shell.showItemInFolder(source.path)
        return { success: true, data: { status: 'dispatched' } }
      }
      try { await shell.openExternal(assertOpenUri(source.uri)) }
      catch { return { success: false, error: '无法派发 Obsidian 跳转。请先安装并打开 Obsidian、注册 Vault，或使用复制路径。' } }
      return { success: true, data: { status: 'dispatched', currentMatches: source.currentMatches, anchorFallback: source.anchorFallback } }
    } catch (error) { return { success: false, error: error.message } }
  })
}
module.exports = { selectionToken, assertOpenUri, registerNovelKnowledgeIpc }
