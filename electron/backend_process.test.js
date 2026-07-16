'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createBackendProcessManager } = require('./backend_process')

function createChildProcess() {
  const child = new EventEmitter()
  child.stdout = new EventEmitter()
  child.stderr = new EventEmitter()
  child.killCalls = []
  child.kill = (signal) => {
    child.killCalls.push(signal)
    return true
  }
  return child
}

function createManager(overrides = {}) {
  const spawnCalls = []
  const child = createChildProcess()
  const manager = createBackendProcessManager({
    app: { isPackaged: false, getPath: () => 'C:\\user-data' },
    platform: 'win32',
    moduleDir: 'C:\\app\\electron',
    processEnv: { EXISTING: 'value' },
    execSyncImpl: () => {},
    spawnImpl: (...args) => {
      spawnCalls.push(args)
      return child
    },
    logger: { log() {}, error() {} },
    ...overrides,
  })
  return { child, manager, spawnCalls }
}

test('starts the development backend once with the expected environment', () => {
  const { child, manager, spawnCalls } = createManager()

  assert.equal(manager.start(), child)
  assert.equal(manager.start(), child)
  assert.equal(spawnCalls.length, 1)
  assert.equal(spawnCalls[0][0], 'py')
  assert.deepEqual(spawnCalls[0][1], ['-u', 'main.py', '18321'])
  assert.equal(spawnCalls[0][2].env.PURRTYPOS_DATA_DIR, 'C:\\user-data')
  assert.equal(spawnCalls[0][2].env.PURRTYPOS_PORT, '18321')
  assert.match(spawnCalls[0][2].env.PURRTYPOS_SKILLS_DIR, /backend[\\/]skills$/)
})

test('uses a packaged executable when one exists', () => {
  const executable = '/resources/backend/purrtypos-backend.exe'
  const { manager, spawnCalls } = createManager({
    app: { isPackaged: true, getPath: () => 'C:\\user-data' },
    resourcesPath: '/resources',
    fsImpl: { existsSync: (candidate) => candidate === executable },
  })

  manager.start()

  assert.equal(spawnCalls[0][0], executable)
  assert.deepEqual(spawnCalls[0][1], ['18321'])
})

test('waitUntilReady retries until the health endpoint succeeds', async () => {
  let attempts = 0
  const delays = []
  const { manager } = createManager({
    fetchImpl: async () => ({ ok: ++attempts === 3 }),
    sleep: async (delay) => delays.push(delay),
  })

  assert.equal(await manager.waitUntilReady(3, 25), true)
  assert.equal(attempts, 3)
  assert.deepEqual(delays, [25, 25])
})

test('stop terminates the active process and clears manager state', async () => {
  const { child, manager } = createManager()
  manager.start()

  const stopping = manager.stop()
  child.emit('close', 0)
  await stopping

  assert.deepEqual(child.killCalls, [undefined])
  assert.equal(manager.isRunning(), false)
})
