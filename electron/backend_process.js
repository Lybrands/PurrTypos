'use strict'

const fs = require('fs')
const path = require('path')
const { execSync, spawn } = require('child_process')

const BACKEND_PORT = 18321
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const NOVEL_ANALYSIS_ACCEPTANCE_ENV = 'PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE'
const NOVEL_ANALYSIS_ACCEPTANCE_MARKER = '.purrtypos-novel-analysis-replacement-acceptance'
const SCREENPLAY_ACCEPTANCE_ENV = 'PURRTYPOS_SCREENPLAY_REPLACEMENT_ACCEPTANCE'
const SCREENPLAY_ACCEPTANCE_MARKER = '.purrtypos-screenplay-replacement-acceptance'
const WRITING_ACCEPTANCE_ENV = 'PURRTYPOS_WRITING_REPLACEMENT_ACCEPTANCE'
const WRITING_ACCEPTANCE_MARKER = '.purrtypos-writing-replacement-acceptance'

const REPLACEMENT_ACCEPTANCE_TARGETS = [
  [NOVEL_ANALYSIS_ACCEPTANCE_ENV, NOVEL_ANALYSIS_ACCEPTANCE_MARKER, 'Novel Analysis'],
  [SCREENPLAY_ACCEPTANCE_ENV, SCREENPLAY_ACCEPTANCE_MARKER, 'Screenplay'],
  [WRITING_ACCEPTANCE_ENV, WRITING_ACCEPTANCE_MARKER, 'Writing'],
]

function resolveBackendDataDir({ app, platform, processEnv, fsImpl }) {
  const pathImpl = platform === 'win32' ? path.win32 : path.posix
  const userData = app.getPath('userData')
  const enabledTargets = REPLACEMENT_ACCEPTANCE_TARGETS.filter(
    ([envName]) => processEnv[envName] === '1',
  )
  if (enabledTargets.length === 0) return userData

  const explicit = processEnv.PURRTYPOS_DATA_DIR?.trim()
  if (!explicit) {
    throw new Error('Replacement acceptance requires PURRTYPOS_DATA_DIR')
  }
  const candidate = pathImpl.resolve(explicit)
  if (candidate === pathImpl.resolve(userData)) {
    throw new Error('Replacement acceptance must not use Electron userData')
  }
  for (const [, marker, label] of enabledTargets) {
    if (!fsImpl.existsSync(path.join(candidate, marker))) {
      throw new Error(`${label} replacement acceptance data directory is not marked`)
    }
  }
  return candidate
}

function createBackendProcessManager({
  app,
  port = BACKEND_PORT,
  platform = process.platform,
  processEnv = process.env,
  resourcesPath = process.resourcesPath,
  moduleDir = __dirname,
  fsImpl = fs,
  spawnImpl = spawn,
  execSyncImpl = execSync,
  fetchImpl = globalThis.fetch,
  sleep = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  logger = console,
} = {}) {
  if (!app) throw new TypeError('app is required')

  const backendUrl = `http://127.0.0.1:${port}`
  let childProcess = null

  function getVirtualEnvPython() {
    if (app.isPackaged) return null

    const executable = platform === 'win32' ? 'python.exe' : 'python'
    const binDir = platform === 'win32' ? 'Scripts' : 'bin'
    const candidate = path.join(moduleDir, '..', '.venv', binDir, executable)
    return fsImpl.existsSync(candidate) ? candidate : null
  }

  function getPythonCommand() {
    const explicitPython = processEnv.PURRTYPOS_PYTHON?.trim()
    if (!app.isPackaged && explicitPython) return explicitPython

    const virtualEnvPython = getVirtualEnvPython()
    if (virtualEnvPython) return virtualEnvPython

    if (platform === 'win32') {
      try {
        execSyncImpl('py --version', { stdio: 'ignore' })
        return 'py'
      } catch {
        // Fall back to the cross-platform command below.
      }
    }
    return 'python'
  }

  function getFrozenBackendExe(backendDir) {
    const executable = path.join(
      backendDir,
      platform === 'win32' ? 'purrtypos-backend.exe' : 'purrtypos-backend',
    )
    return fsImpl.existsSync(executable) ? executable : null
  }

  function getBackendPaths() {
    const backendDir = app.isPackaged
      ? path.join(resourcesPath, 'backend')
      : path.join(moduleDir, '..', 'backend')
    return { backendDir }
  }

  function start() {
    if (childProcess) return childProcess

    const { backendDir } = getBackendPaths()
    const env = {
      ...processEnv,
      PURRTYPOS_DATA_DIR: resolveBackendDataDir({
        app,
        platform,
        processEnv,
        fsImpl,
      }),
      PURRTYPOS_PORT: String(port),
      PURRTYPOS_DEV_DIAGNOSTICS: (
        processEnv.PURRTYPOS_DEV_DIAGNOSTICS
        ?? (app.isPackaged ? '0' : '1')
      ),
      // Packaged Python sources live inside signed application resources.
      // Writing __pycache__ there mutates the sealed bundle after first launch.
      PYTHONDONTWRITEBYTECODE: app.isPackaged
        ? '1'
        : processEnv.PYTHONDONTWRITEBYTECODE,
    }
    const frozen = app.isPackaged ? getFrozenBackendExe(backendDir) : null

    if (frozen) {
      logger.log('[python] starting frozen backend:', frozen)
      childProcess = spawnImpl(frozen, [String(port)], {
        cwd: backendDir,
        env,
        stdio: ['pipe', 'pipe', 'pipe'],
      })
    } else {
      const pythonCommand = getPythonCommand()
      logger.log(`[python] using command: ${pythonCommand}`)
      childProcess = spawnImpl(
        pythonCommand,
        ['-u', 'main.py', String(port)],
        { cwd: backendDir, env, stdio: ['pipe', 'pipe', 'pipe'] },
      )
    }

    const startedProcess = childProcess
    startedProcess.stdout?.on('data', (data) => {
      logger.log('[python]', data.toString().trim())
    })
    startedProcess.stderr?.on('data', (data) => {
      logger.error('[python]', data.toString().trim())
    })
    startedProcess.on('close', (code) => {
      logger.log('[python] exited with code', code)
      if (childProcess === startedProcess) childProcess = null
    })
    return startedProcess
  }

  async function waitUntilReady(maxRetries = 30, intervalMs = 500) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        const response = await fetchImpl(`${backendUrl}/health`)
        if (response.ok) return true
      } catch {
        // The backend may not have bound its port yet.
      }
      await sleep(intervalMs)
    }
    throw new Error('Python backend failed to start')
  }

  async function fetchDatabaseInfo() {
    const response = await fetchImpl(`${backendUrl}/api/database/info`)
    return response.json()
  }

  async function stop(timeoutMs = 5000) {
    const processToStop = childProcess
    if (!processToStop) return

    await new Promise((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        if (childProcess === processToStop) childProcess = null
        resolve()
      }
      const timer = setTimer(() => {
        try { processToStop.kill('SIGKILL') } catch {}
        finish()
      }, timeoutMs)

      processToStop.once('close', () => {
        clearTimer(timer)
        finish()
      })
      try {
        processToStop.kill()
      } catch {
        clearTimer(timer)
        finish()
      }
    })
  }

  return {
    backendUrl,
    fetchDatabaseInfo,
    getBackendPaths,
    getFrozenBackendExe,
    getPythonCommand,
    getVirtualEnvPython,
    isRunning: () => childProcess !== null,
    start,
    stop,
    waitUntilReady,
  }
}

module.exports = { BACKEND_PORT, BACKEND_URL, createBackendProcessManager }
