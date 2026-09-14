const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn, spawnSync } = require('node:child_process')

const projectRoot = path.resolve(__dirname, '..')
const explicitPython = process.env.PURRTYPOS_PYTHON?.trim()
const candidates = [
  ...(explicitPython ? [{ command: explicitPython, prefix: [] }] : []),
  { command: path.join(projectRoot, '.venv', 'Scripts', 'python.exe'), prefix: [], local: true },
  { command: path.join(projectRoot, '.venv', 'bin', 'python'), prefix: [], local: true },
  { command: 'python', prefix: [] },
  { command: 'python3', prefix: [] },
  { command: 'py', prefix: ['-3'] },
]

function supportsBackend(candidate) {
  if (candidate.local && !fs.existsSync(candidate.command)) return false
  const probe = spawnSync(
    candidate.command,
    [...candidate.prefix, '-c', 'import fastapi, purra, uvicorn'],
    { cwd: projectRoot, stdio: 'ignore' },
  )
  return probe.status === 0
}

function userDataDir() {
  if (process.platform === 'win32') {
    return path.join(process.env.APPDATA || os.homedir(), 'PurrTypos')
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'PurrTypos')
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'PurrTypos')
}

const python = candidates.find(supportsBackend)
if (!python) {
  console.error('No Python interpreter with backend dependencies and PurrA was found.')
  process.exit(1)
}

const child = spawn(
  python.command,
  [...python.prefix, '-u', path.join('backend', 'main.py')],
  {
    cwd: projectRoot,
    env: {
      ...process.env,
      PURRTYPOS_DATA_DIR: process.env.PURRTYPOS_DATA_DIR || userDataDir(),
      PURRTYPOS_PORT: process.env.PURRTYPOS_PORT || '18321',
      PURRTYPOS_DEV_DIAGNOSTICS: process.env.PURRTYPOS_DEV_DIAGNOSTICS || '1',
    },
    stdio: 'inherit',
  },
)

const stop = () => {
  if (!child.killed) child.kill()
}
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
child.on('exit', (code) => process.exit(code ?? 0))
