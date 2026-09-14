'use strict'

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const { spawn } = require('node:child_process')

const projectRoot = path.resolve(__dirname, '..', '..')

function assertPortAvailable(port) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port })
    socket.once('connect', () => {
      socket.destroy()
      reject(new Error(`Acceptance requires unused localhost port ${port}`))
    })
    socket.once('error', (error) => {
      socket.destroy()
      if (error.code === 'ECONNREFUSED') {
        resolve()
        return
      }
      reject(new Error(
        `Could not verify localhost port ${port}: ${error.code || error.message}`,
      ))
    })
  })
}

async function main() {
  const packaged = process.argv.includes('--packaged')
  if (!packaged) await assertPortAvailable(5174)
  await assertPortAvailable(18321)

  const acceptanceRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), 'purrtypos-novel-analysis-acceptance-'),
  )
  const databaseDir = path.join(acceptanceRoot, 'database')
  const electronUserDataDir = path.join(acceptanceRoot, 'electron-user-data')
  fs.mkdirSync(databaseDir)
  fs.mkdirSync(electronUserDataDir)
  fs.writeFileSync(
    path.join(databaseDir, '.purrtypos-novel-analysis-replacement-acceptance'),
    'isolated replacement acceptance\n',
    { flag: 'wx' },
  )
  fs.writeFileSync(
    path.join(electronUserDataDir, '.purrtypos-electron-acceptance'),
    'isolated Electron acceptance\n',
    { flag: 'wx' },
  )

  console.log(`Novel Analysis acceptance workspace: ${acceptanceRoot}`)
  console.log('The workspace is retained after exit for evidence inspection.')

  const child = spawn(
    packaged
      ? path.join(projectRoot, 'dist-electron', 'mac-arm64', 'PurrTypos.app', 'Contents', 'MacOS', 'PurrTypos')
      : 'npm',
    packaged ? [] : ['run', 'dev:electron:acceptance'],
    {
    cwd: projectRoot,
    env: {
      ...process.env,
      PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE: '1',
      PURRTYPOS_DATA_DIR: databaseDir,
      PURRTYPOS_ACCEPTANCE_ELECTRON_USER_DATA_DIR: electronUserDataDir,
    },
    stdio: 'inherit',
    },
  )

  let stopping = false
  function stop(signal = 'SIGTERM') {
    if (stopping) return
    stopping = true
    child.kill(signal)
  }

  process.on('SIGINT', () => stop('SIGINT'))
  process.on('SIGTERM', () => stop('SIGTERM'))
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal)
    process.exit(code ?? 1)
  })
}

main().catch((error) => {
  console.error(error.message)
  process.exit(1)
})
