'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const AdmZip = require('adm-zip')
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
      importKind: 'file',
      documentCount: 1,
      skippedFileCount: 0,
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

test('novel source folder picker combines supported documents in natural order', async () => {
  const handlers = new Map()
  const entries = {
    '/tmp/原作': [
      { name: '第10章.txt', isDirectory: () => false, isFile: () => true },
      { name: '封面.png', isDirectory: () => false, isFile: () => true },
      { name: '第2章.md', isDirectory: () => false, isFile: () => true },
    ],
  }
  const contents = {
    '/tmp/原作/第10章.txt': Buffer.from('第十章正文'),
    '/tmp/原作/第2章.md': Buffer.from('第二章正文'),
  }
  registerNovelSourceFileIpc({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: ['/tmp/原作'] }) },
    getMainWindow: () => null,
    fsImpl: {
      readdirSync: (directory) => entries[directory],
      readFileSync: (filePath) => contents[filePath],
    },
  })
  const result = await handlers.get('pick-novel-source-text-file')(null, { mode: 'folder' })
  assert.equal(result.success, true)
  assert.equal(result.data.fileName, '原作')
  assert.equal(result.data.extension, '.md')
  assert.equal(result.data.importKind, 'folder')
  assert.equal(result.data.documentCount, 2)
  assert.equal(result.data.skippedFileCount, 1)
  assert.ok(result.data.content.indexOf('第2章') < result.data.content.indexOf('第10章'))
})

test('novel source ZIP picker reads only supported UTF-8 documents', async () => {
  const handlers = new Map()
  const zip = new AdmZip()
  zip.addFile('卷一/第2章.md', Buffer.from('第二章正文'))
  zip.addFile('卷一/第1章.txt', Buffer.from('第一章正文'))
  zip.addFile('cover.jpg', Buffer.from('image'))
  registerNovelSourceFileIpc({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async () => ({ canceled: false, filePaths: ['/tmp/原作.zip'] }) },
    getMainWindow: () => null,
    fsImpl: { readFileSync: () => zip.toBuffer() },
  })
  const result = await handlers.get('pick-novel-source-text-file')()
  assert.equal(result.success, true)
  assert.equal(result.data.fileName, '原作.zip')
  assert.equal(result.data.extension, '.md')
  assert.equal(result.data.importKind, 'archive')
  assert.equal(result.data.documentCount, 2)
  assert.equal(result.data.skippedFileCount, 1)
  assert.ok(result.data.content.indexOf('第1章') < result.data.content.indexOf('第2章'))
})
