const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const output = path.join(root, 'dist-web')
const webOutput = path.join(output, 'web')
const backendOutput = path.join(output, 'backend')

if (!fs.existsSync(path.join(root, 'dist', 'index.html'))) {
  throw new Error('dist/index.html is missing; run vite build first')
}
if (!fs.existsSync(path.join(root, 'build-resources', 'backend'))) {
  throw new Error('build-resources/backend is missing; prepare backend resources first')
}

fs.rmSync(output, { recursive: true, force: true })
fs.mkdirSync(output, { recursive: true })
fs.cpSync(path.join(root, 'dist'), webOutput, { recursive: true })
fs.cpSync(path.join(root, 'build-resources', 'backend'), backendOutput, { recursive: true })

const windowsLauncher = `@echo off
setlocal
set "PURRTYPOS_WEB_DIST_DIR=%~dp0web"
set "PURRTYPOS_DATA_DIR=%APPDATA%\\PurrTypos"
if exist "%~dp0backend\\purrtypos-backend.exe" (
  "%~dp0backend\\purrtypos-backend.exe" --open-browser
) else (
  py -3 -u "%~dp0backend\\main.py" --open-browser
)
`

const unixLauncher = `#!/bin/sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PURRTYPOS_WEB_DIST_DIR="$SCRIPT_DIR/web"
if [ "$(uname -s)" = "Darwin" ]; then
  export PURRTYPOS_DATA_DIR="\${PURRTYPOS_DATA_DIR:-$HOME/Library/Application Support/PurrTypos}"
else
  export PURRTYPOS_DATA_DIR="\${PURRTYPOS_DATA_DIR:-\${XDG_DATA_HOME:-$HOME/.local/share}/PurrTypos}"
fi
if [ -x "$SCRIPT_DIR/backend/purrtypos-backend" ]; then
  exec "$SCRIPT_DIR/backend/purrtypos-backend" --open-browser
fi
exec python3 -u "$SCRIPT_DIR/backend/main.py" --open-browser
`

fs.writeFileSync(path.join(output, 'start-web.cmd'), windowsLauncher)
fs.writeFileSync(path.join(output, 'start-web.sh'), unixLauncher, { mode: 0o755 })
fs.writeFileSync(
  path.join(output, 'README.txt'),
  'PurrTypos Local Web\\n\\nWindows: run start-web.cmd\\nmacOS/Linux: run start-web.sh\\n',
)

console.log(`[package-local-web] Created ${output}`)
