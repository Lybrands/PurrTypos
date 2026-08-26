'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { registerNovelSourceFileIpc } = require('./source_file_ipc')

test('novel source picker returns file identity and UTF-8 content', async () => {
  const handlers = new Map()
  registerNovelSourceFileIpc({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: ['/tmp/原作.md'] }) },
    getMainWindow: () => null,
    fsImpl: { readFileSync: () => Buffer.from('# 第一章\n正文', 'utf8') },
  })
  const result = await handlers.get('pick-novel-source-text-file')()
  assert.deepEqual(result, {
    success: true,
    data: {
      fileName: '原作.md',
      extension: '.md',
      byteCount: Buffer.byteLength('# 第一章\n正文'),
      content: '# 第一章\n正文',
    },
  })
})

test('novel source picker rejects unsupported extension before reading', async () => {
  const handlers = new Map()
  let reads = 0
  registerNovelSourceFileIpc({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: ['/tmp/book.epub'] }) },
    getMainWindow: () => null,
    fsImpl: { readFileSync: () => { reads += 1; return Buffer.from('') } },
  })
  const result = await handlers.get('pick-novel-source-text-file')()
  assert.equal(result.success, false)
  assert.match(result.error, /TXT、Markdown/)
  assert.equal(reads, 0)
})
