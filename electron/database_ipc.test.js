'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {
  registerDatabaseIpcHandlers,
  removeDatabaseSidecars,
  samePath,
} = require('./database_ipc')

function register(overrides = {}) {
  const handlers = new Map()
  const backendProcess = {
    backendUrl: 'http://backend.test',
    fetchDatabaseInfo: async () => ({ success: true, data: { dbPath: 'C:\\data\\purrtypos.db' } }),
    isRunning: () => true,
    start() {},
    async stop() {},
    async waitUntilReady() {},
  }
  registerDatabaseIpcHandlers({
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: {
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
      showSaveDialog: async () => ({ canceled: true }),
    },
    shell: { openPath: async () => '' },
    app: { getPath: () => 'C:\\data' },
    backendProcess,
    getMainWindow: () => null,
    ...overrides,
  })
  return { backendProcess, handlers }
}

test('removeDatabaseSidecars removes WAL and shared-memory files', () => {
  const removed = []
  removeDatabaseSidecars('book.db', {
    rmSync: (filePath, options) => removed.push([filePath, options]),
  })

  assert.deepEqual(removed, [
    ['book.db-wal', { force: true }],
    ['book.db-shm', { force: true }],
  ])
})

test('samePath compares normalized paths case-insensitively', () => {
  assert.equal(samePath('C:\\DATA\\book.db', 'c:\\data\\book.db'), true)
  assert.equal(samePath('C:\\data\\one.db', 'C:\\data\\two.db'), false)
})

test('import rejects the active database without stopping the backend', async () => {
  let stopCalls = 0
  const fsImpl = {
    existsSync: () => true,
  }
  const { handlers } = register({
    dialog: {
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: [path.resolve('/data/purrtypos.db')],
      }),
      showSaveDialog: async () => ({ canceled: true }),
    },
    app: { getPath: () => path.resolve('/data') },
    fsImpl,
    backendProcess: {
      backendUrl: 'http://backend.test',
      fetchDatabaseInfo: async () => ({ success: true, data: {} }),
      isRunning: () => true,
      start() {},
      stop: async () => { stopCalls += 1 },
      async waitUntilReady() {},
    },
  })

  const result = await handlers.get('import-database')()

  assert.deepEqual(result, {
    success: false,
    error: '不能导入当前正在使用的数据库文件',
  })
  assert.equal(stopCalls, 0)
})

test('open database directory uses the backend-reported path', async () => {
  const opened = []
  const { handlers } = register({
    shell: { openPath: async (directory) => { opened.push(directory); return '' } },
  })

  assert.deepEqual(await handlers.get('open-database-directory')(), { success: true })
  assert.deepEqual(opened, [path.dirname('C:\\data\\purrtypos.db')])
})
