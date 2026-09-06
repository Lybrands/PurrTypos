'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const { selectionToken, assertOpenUri, registerNovelKnowledgeIpc } = require('./novel_knowledge_ipc')

test('selection proof binds the selected path, book, expiry and nonce', () => {
  const token = selectionToken({ bookId: 'book', root: '/临时 Vault/小说', secret: 'secret', now: 123000 })
  const [data, mac] = token.split('.')
  const payload = JSON.parse(Buffer.from(data, 'base64url'))
  assert.equal(payload.root, '/临时 Vault/小说')
  assert.equal(payload.bookId, 'book')
  assert.equal(payload.issuedAt, 123)
  assert.ok(payload.nonce)
  assert.equal(mac, crypto.createHmac('sha256', 'secret').update(data).digest('hex'))
})

test('URI dispatch admits only encoded open-path navigation', () => {
  const uri = 'obsidian://open?path=' + encodeURIComponent('/临时 Vault/同名#&?.md')
  assert.equal(assertOpenUri(uri), uri)
  for (const value of ['https://example.com', 'obsidian://new?path=x', 'obsidian://open?path=x&append=true', 'obsidian://open?path=x&path=y', 'obsidian://open?path=x#new', 'obsidian://open/new?path=x']) {
    assert.throws(() => assertOpenUri(value))
  }
})

test('main window authorization, backend revalidation and truthful dispatch status', async () => {
  const handlers = new Map()
  const frame = {}
  const webContents = { mainFrame: frame }
  const event = { sender: webContents, senderFrame: frame }
  const calls = []
  const opened = []
  const copied = []
  const uri = 'obsidian://open?path=%2Ftmp%2Fnote.md'
  registerNovelKnowledgeIpc({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async () => ({ filePaths: ['/tmp/vault/book'] }) },
    shell: { openExternal: async (value) => opened.push(value), showItemInFolder: () => {} },
    clipboard: { writeText: (value) => copied.push(value) }, getWindow: () => ({ webContents }),
    backendUrl: 'http://localhost:1', secret: 'host',
    fetchImpl: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ success: true, data: { uri, path: '/tmp/note.md', currentMatches: false } }) } },
  })
  assert.equal((await handlers.get('novel-knowledge-select')({ sender: {} }, 'book')).success, false)
  assert.equal((await handlers.get('novel-knowledge-select')(event, 'book')).success, true)
  const result = await handlers.get('novel-knowledge-open')(event, { bookId: 'book', documentId: 'doc', revision: 'old', uri: 'obsidian://new?append=true' })
  assert.equal(result.data.status, 'dispatched')
  assert.equal(result.data.currentMatches, false)
  assert.deepEqual(opened, [uri])
  assert.equal(calls[0].options.headers['X-Knowledge-Host'], 'host')
  assert.equal(JSON.parse(calls[0].options.body).uri, undefined)
  await handlers.get('novel-knowledge-open')(event, { bookId: 'book', documentId: 'doc', action: 'copy' })
  assert.deepEqual(copied, ['/tmp/note.md'])
  assert.equal(opened.length, 1)
})
