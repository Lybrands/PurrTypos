'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { registerDatabaseIpcHandlers } = require('./database_ipc')

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

test('import sends the complete archive through the backend validator before restart', async () => {
  let stopCalls = 0
  let startCalls = 0
  const requests = []
  const fsImpl = {
    existsSync: () => true,
    readFileSync: () => Buffer.from('full-backup'),
  }
  const { handlers } = register({
    dialog: {
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: [path.resolve('/data/project.purrbackup')],
      }),
      showSaveDialog: async () => ({ canceled: true }),
    },
    fsImpl,
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return {
        ok: true,
        json: async () => ({
          success: true,
          data: {
            beforeStats: { books: 1 },
            afterStats: { books: 2 },
            restartRequired: true,
          },
        }),
      }
    },
    backendProcess: {
      backendUrl: 'http://backend.test',
      fetchDatabaseInfo: async () => ({ success: true, data: {} }),
      isRunning: () => true,
      start: () => { startCalls += 1 },
      stop: async () => { stopCalls += 1 },
      async waitUntilReady() {},
    },
  })

  const result = await handlers.get('import-database')()

  assert.equal(result.success, true)
  assert.equal(requests.length, 1)
  assert.match(requests[0].url, /project\.purrbackup$/)
  assert.equal(requests[0].options.body.toString(), 'full-backup')
  assert.equal(stopCalls, 1)
  assert.equal(startCalls, 1)
})

test('open database directory uses the backend-reported path', async () => {
  const opened = []
  const { handlers } = register({
    shell: { openPath: async (directory) => { opened.push(directory); return '' } },
  })

  assert.deepEqual(await handlers.get('open-database-directory')(), { success: true })
  assert.deepEqual(opened, [path.dirname('C:\\data\\purrtypos.db')])
})
