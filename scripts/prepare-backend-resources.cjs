/**
 * Fills build-resources/backend for electron-builder extraResources.
 * - Windows: if backend/dist/purrtypos-backend/purrtypos-backend.exe exists, copy that onedir bundle.
 * - Otherwise: copy backend source (excluding venv, PyInstaller output, caches) for Python-at-runtime.
 */
const fs = require('fs')
const path = require('path')
const { spawnSync } = require('child_process')

const root = path.join(__dirname, '..')
const outDir = path.join(root, 'build-resources', 'backend')
const backendSrc = path.join(root, 'backend')
const purraRequirements = path.join(backendSrc, 'requirements-purra.txt')
const runtimeRequirements = path.join(backendSrc, 'requirements-runtime.txt')
const frozenDir = path.join(backendSrc, 'dist', 'purrtypos-backend')
const frozenExe = path.join(frozenDir, 'purrtypos-backend.exe')

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
  'vendor',
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

function vendorPurra(dest) {
  const python = findPackagingPython()
  if (!python) {
    throw new Error('Python with pip is required to install PurrA dependencies')
  }
  const result = spawnSync(
    python.command,
    [
      ...python.prefix,
      '-m', 'pip', 'install', '--target', dest,
      '-r', runtimeRequirements,
      '-r', purraRequirements,
    ],
    { cwd: root, stdio: 'inherit' },
  )
  if (result.status !== 0) {
    throw new Error('Failed to package the published PurrA dependencies')
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
  console.log('[prepare-backend-resources] Copied backend and installed published PurrA dependencies →', outDir)
}
