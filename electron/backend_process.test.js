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
  assert.equal(spawnCalls[0][2].env.PURRTYPOS_DEV_DIAGNOSTICS, '1')
  assert.equal(spawnCalls[0][2].env.PURRTYPOS_SKILLS_DIR, undefined)
})

test('prefers the project virtual environment for the development backend', () => {
  const moduleDir = '/app/electron'
  const virtualEnvPython = '/app/.venv/bin/python'
  const { manager, spawnCalls } = createManager({
    platform: 'darwin',
    moduleDir,
    fsImpl: { existsSync: (candidate) => candidate === virtualEnvPython },
  })

  manager.start()

  assert.equal(manager.getVirtualEnvPython(), virtualEnvPython)
  assert.equal(spawnCalls[0][0], virtualEnvPython)
})

test('falls back to the system Python when no project virtual environment exists', () => {
  const { manager, spawnCalls } = createManager({
    platform: 'darwin',
    fsImpl: { existsSync: () => false },
  })

  manager.start()

  assert.equal(manager.getVirtualEnvPython(), null)
  assert.equal(spawnCalls[0][0], 'python')
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
  assert.equal(spawnCalls[0][2].env.PURRTYPOS_DEV_DIAGNOSTICS, '0')
  assert.equal(spawnCalls[0][2].env.PYTHONDONTWRITEBYTECODE, '1')
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

test('an explicit development Python keeps candidate and rollback environments separate', () => {
  const { manager, spawnCalls } = createManager({
    platform: 'darwin', processEnv: { PURRTYPOS_PYTHON: '/candidate/bin/python' },
    fsImpl: { existsSync: () => true },
  })
  manager.start()
  assert.equal(spawnCalls[0][0], '/candidate/bin/python')
})

test('uses a separate marked data directory for Novel Analysis acceptance', () => {
  const marker = '/private/tmp/analysis-acceptance/.purrtypos-novel-analysis-replacement-acceptance'
  const { manager, spawnCalls } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/analysis-acceptance',
    },
    fsImpl: { existsSync: (candidate) => candidate === marker },
  })

  manager.start()

  assert.equal(
    spawnCalls[0][2].env.PURRTYPOS_DATA_DIR,
    '/private/tmp/analysis-acceptance',
  )
})

test('rejects Novel Analysis acceptance against Electron userData', () => {
  const { manager } = createManager({
    processEnv: {
      PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: 'C:\\user-data',
    },
  })

  assert.throws(() => manager.start(), /must not use Electron userData/)
})

test('rejects an unmarked Novel Analysis acceptance directory', () => {
  const { manager } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/analysis-acceptance',
    },
    fsImpl: { existsSync: () => false },
  })

  assert.throws(() => manager.start(), /data directory is not marked/)
})

test('uses a separate marked data directory for Screenplay acceptance', () => {
  const marker = '/private/tmp/screenplay-acceptance/.purrtypos-screenplay-replacement-acceptance'
  const { manager, spawnCalls } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_SCREENPLAY_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/screenplay-acceptance',
    },
    fsImpl: { existsSync: (candidate) => candidate === marker },
  })

  manager.start()

  assert.equal(
    spawnCalls[0][2].env.PURRTYPOS_DATA_DIR,
    '/private/tmp/screenplay-acceptance',
  )
})

test('rejects an unmarked Screenplay acceptance directory', () => {
  const { manager } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_SCREENPLAY_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/screenplay-acceptance',
    },
    fsImpl: { existsSync: () => false },
  })

  assert.throws(() => manager.start(), /Screenplay replacement acceptance data directory is not marked/)
})

test('uses a separate marked data directory for Writing acceptance', () => {
  const marker = '/private/tmp/writing-acceptance/.purrtypos-writing-replacement-acceptance'
  const { manager, spawnCalls } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_WRITING_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/writing-acceptance',
    },
    fsImpl: { existsSync: (candidate) => candidate === marker },
  })

  manager.start()

  assert.equal(
    spawnCalls[0][2].env.PURRTYPOS_DATA_DIR,
    '/private/tmp/writing-acceptance',
  )
})

test('rejects an unmarked Writing acceptance directory', () => {
  const { manager } = createManager({
    platform: 'darwin',
    processEnv: {
      PURRTYPOS_WRITING_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: '/private/tmp/writing-acceptance',
    },
    fsImpl: { existsSync: () => false },
  })

  assert.throws(() => manager.start(), /Writing replacement acceptance data directory is not marked/)
})
