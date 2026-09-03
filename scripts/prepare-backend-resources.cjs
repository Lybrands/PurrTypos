/**
 * Fills build-resources/backend for electron-builder extraResources.
 * - Windows: if backend/dist/purrtypos-backend/purrtypos-backend.exe exists, copy that onedir bundle.
 * - Otherwise: copy backend source (excluding venv, PyInstaller output, caches) for Python-at-runtime.
 */
const fs = require('fs')
const crypto = require('crypto')
const path = require('path')
const { spawnSync } = require('child_process')

const root = path.join(__dirname, '..')
const outDir = path.join(root, 'build-resources', 'backend')
const backendSrc = path.join(root, 'backend')
const purraRequirements = path.join(backendSrc, 'requirements-purra.txt')
const runtimeRequirements = path.join(backendSrc, 'requirements-runtime.txt')
const frozenDir = path.join(backendSrc, 'dist', 'purrtypos-backend')
const frozenExe = path.join(frozenDir, 'purrtypos-backend.exe')
const PURRA_WHEEL_REQUIREMENT = './backend/vendor/purra-0.5.0-py3-none-any.whl'
const PURRA_WHEEL_SHA256 = 'c623b959aa4fc2d09799ccc298dc753f7e59f316aa517511896a34824ad92b0b'

const SKIP_NAMES = new Set([
  'dist',
  'build',
  '__pycache__',
  '.venv',
  'venv',
  '.git',
  '.mypy_cache',
  '.pytest_cache',
  'tests',
  '.DS_Store',
])

function copyDirFiltered(src, dest) {
  fs.mkdirSync(dest, { recursive: true })
  for (const ent of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP_NAMES.has(ent.name)) continue
    const from = path.join(src, ent.name)
    const to = path.join(dest, ent.name)
    if (ent.isDirectory()) copyDirFiltered(from, to)
    else fs.copyFileSync(from, to)
  }
}

function pythonCandidates() {
  const explicit = process.env.PURRTYPOS_PYTHON?.trim()
  return [
    ...(explicit ? [{ command: explicit, prefix: [] }] : []),
    { command: path.join(root, '.venv', 'Scripts', 'python.exe'), prefix: [], local: true },
    { command: path.join(root, '.venv', 'bin', 'python'), prefix: [], local: true },
    { command: 'python3', prefix: [] },
    { command: 'python', prefix: [] },
    { command: 'py', prefix: ['-3'] },
  ]
}

function findPackagingPython() {
  return pythonCandidates().find((candidate) => {
    if (candidate.local && !fs.existsSync(candidate.command)) return false
    return spawnSync(
      candidate.command,
      [
        ...candidate.prefix,
        '-c',
        'import pip',
      ],
      { cwd: root, stdio: 'ignore' },
    ).status === 0
  })
}

function localPurraRequirements() {
  return fs.readFileSync(purraRequirements, 'utf8')
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+#.*$/, '').trim())
    .filter(Boolean)
    .map((line) => {
      if (line === PURRA_WHEEL_REQUIREMENT) {
        const wheel = path.resolve(root, line)
        if (!fs.existsSync(wheel)) {
          throw new Error(`Local PurrA wheel does not exist: ${wheel}`)
        }
        const digest = crypto.createHash('sha256')
          .update(fs.readFileSync(wheel))
          .digest('hex')
        if (digest !== PURRA_WHEEL_SHA256) {
          throw new Error(`Local PurrA wheel SHA-256 mismatch: ${digest}`)
        }
        return wheel
      }
      const match = line.match(/^-e\s+(.+?)(\[[^\]]+\])?$/)
      if (!match) {
        throw new Error(`Unsupported local PurrA requirement: ${line}`)
      }
      const source = path.resolve(root, match[1])
      if (!fs.existsSync(source)) {
        throw new Error(`Local PurrA dependency does not exist: ${source}`)
      }
      return `${source}${match[2] || ''}`
    })
}

function vendorPurra(dest) {
  const python = findPackagingPython()
  if (!python) {
    throw new Error('Python with pip is required to install PurrA dependencies')
  }
  const requirements = localPurraRequirements()
  const result = spawnSync(
    python.command,
    [
      ...python.prefix,
      '-m', 'pip', 'install', '--no-build-isolation', '--target', dest,
      '-r', runtimeRequirements,
      ...requirements,
    ],
    { cwd: root, stdio: 'inherit' },
  )
  if (result.status !== 0) {
    throw new Error('Failed to package the local PurrA dependencies')
  }
}

fs.rmSync(outDir, { recursive: true, force: true })
fs.mkdirSync(path.dirname(outDir), { recursive: true })

if (process.platform === 'win32' && fs.existsSync(frozenExe)) {
  fs.cpSync(frozenDir, outDir, { recursive: true })
  console.log('[prepare-backend-resources] Using frozen backend:', frozenDir)
} else {
  if (process.platform === 'win32') {
    console.warn(
      '[prepare-backend-resources] No purrtypos-backend.exe found; copying Python source backend.',
      'Run npm run build:backend:win before pack windows for no-Python installs.',
    )
  }
  copyDirFiltered(backendSrc, outDir)
  vendorPurra(outDir)
  console.log('[prepare-backend-resources] Copied backend and installed local PurrA dependencies →', outDir)
}
