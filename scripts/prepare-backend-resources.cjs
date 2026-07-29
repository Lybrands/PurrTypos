/**
 * Fills build-resources/backend for electron-builder extraResources.
 * - Windows: if backend/dist/purrtypos-backend/purrtypos-backend.exe exists, copy that onedir bundle.
 * - Otherwise: copy backend source (excluding venv, PyInstaller output, caches) for Python-at-runtime.
 */
const fs = require('fs')
const path = require('path')

const root = path.join(__dirname, '..')
const outDir = path.join(root, 'build-resources', 'backend')
const backendSrc = path.join(root, 'backend')
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
  console.log('[prepare-backend-resources] Copied source backend →', outDir)
}
