const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const projectRoot = path.resolve(__dirname, '..')
const explicitPython = process.env.PURRTYPOS_PYTHON?.trim()
const candidates = [
  ...(explicitPython ? [{ command: explicitPython, prefix: [] }] : []),
  {
    command: path.join(projectRoot, '.venv', 'Scripts', 'python.exe'),
    prefix: [],
    local: true,
  },
  {
    command: path.join(projectRoot, '.venv', 'bin', 'python'),
    prefix: [],
    local: true,
  },
  { command: 'python', prefix: [] },
  { command: 'python3', prefix: [] },
  { command: 'py', prefix: ['-3'] },
]

function supportsPytest(candidate) {
  if (candidate.local && !fs.existsSync(candidate.command)) return false
  const probe = spawnSync(
    candidate.command,
    [...candidate.prefix, '-c', 'import pytest'],
    { cwd: projectRoot, stdio: 'ignore' },
  )
  return probe.status === 0
}

const python = candidates.find(supportsPytest)
if (!python) {
  console.error(
    'No Python interpreter with pytest was found. Install backend/requirements.txt ' +
      'or set PURRTYPOS_PYTHON.',
  )
  process.exit(1)
}

const result = spawnSync(
  python.command,
  [...python.prefix, '-m', 'pytest', 'backend/tests', ...process.argv.slice(2)],
  { cwd: projectRoot, stdio: 'inherit' },
)

if (result.error) {
  console.error(result.error.message)
  process.exit(1)
}
process.exit(result.status ?? 1)
