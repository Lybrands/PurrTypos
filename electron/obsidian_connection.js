'use strict'
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const crypto = require('node:crypto')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const run = promisify(execFile)

function contains(root, target) {
  const relative = path.relative(root, target)
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
}
async function readRegistry(file) {
  let raw
  try {
    const stat = await fs.lstat(file)
    if (!stat.isFile() || stat.size > 4 * 1024 * 1024) throw new Error('Obsidian 仓库配置格式不受支持')
    raw = await fs.readFile(file, 'utf8')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    return { raw: null, data: {} }
  }
  let data
  try { data = JSON.parse(raw) } catch { throw new Error('Obsidian 仓库配置无法读取，未修改原配置') }
  if (!data || Array.isArray(data) || typeof data !== 'object'
    || (data.vaults != null && (Array.isArray(data.vaults) || typeof data.vaults !== 'object'))
    || Object.values(data.vaults || {}).some(v => !v || typeof v.path !== 'string' || !path.isAbsolute(v.path))) {
    throw new Error('Obsidian 仓库配置格式不受支持，未修改原配置')
  }
  return { raw, data }
}
async function findVault(data, root, target) {
  const matches = []
  for (const [id, vault] of Object.entries(data.vaults || {})) {
    let registered
    try { registered = await fs.realpath(vault.path) } catch { continue }
    if (contains(registered, root) && contains(registered, target)) matches.push({ id, root: registered })
  }
  return matches.sort((a, b) => b.root.length - a.root.length)[0]
}
function macLifecycle() {
  let applicationPath
  const query = async expression => {
    const result = await run('/usr/bin/osascript', ['-l', 'JavaScript', '-e',
      'ObjC.import("AppKit"); ' + expression], { timeout: 10000 })
    return result.stdout.trim()
  }
  const apps = '$.NSRunningApplication.runningApplicationsWithBundleIdentifier("md.obsidian")'
  return {
    async installed() {
      for (const candidate of ['/Applications/Obsidian.app', path.join(os.homedir(), 'Applications', 'Obsidian.app')]) {
        try { await fs.access(candidate); applicationPath = candidate; return } catch {}
      }
      const found = await query('var url = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier("md.obsidian"); url ? ObjC.unwrap(url.path) : ""')
      if (!found) throw new Error('未找到 Obsidian，请安装后重试')
      applicationPath = found
    },
    async open(uri) { await run('/usr/bin/open', ['-a', applicationPath, uri], { timeout: 10000 }) },
    async running() { return await query(apps + '.count > 0') === 'true' },
    async quit() {
      const accepted = await query('var apps = ' + apps + '; var accepted = true; for (var i = 0; i < apps.count; i++) { if (!apps.objectAtIndex(i).terminate) accepted = false; } accepted')
      if (accepted !== 'true') throw new Error('Obsidian 尚未安全退出，连接已暂停；原配置未修改')
    },
  }
}

// Obsidian caches its vault registry in memory. Only register after graceful exit;
// keep a byte-for-byte backup and preserve all unrelated settings and vaults.
function createObsidianConnection({ shell, dialog, getWindow, platform = process.platform,
  registryPath = path.join(platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Application Support') : platform === 'win32' ? (process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming')) : (process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config')), 'obsidian', 'obsidian.json'),
  lifecycle = platform === 'darwin' ? macLifecycle() : { installed: async () => {} }, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
  let tail = Promise.resolve()
  const open = async ({ path: targetPath, vaultPath }) => {
    if (!path.isAbsolute(targetPath || '') || !path.isAbsolute(vaultPath || '')) throw new Error('资料库路径无效，请重启 PurrTypos 后重试')
    const root = await fs.realpath(vaultPath)
    const target = await fs.realpath(targetPath)
    if (!(await fs.stat(root)).isDirectory() || !contains(root, target)) throw new Error('资料不属于当前作品目录')
    await lifecycle.installed()
    let snapshot = await readRegistry(registryPath)
    let vault = await findVault(snapshot.data, root, target)
    if (!vault) {
      if (platform !== 'darwin') throw new Error('当前系统暂不支持首次自动登记 Obsidian 仓库')
      const answer = await dialog.showMessageBox(getWindow(), {
        type: 'question', title: '连接 Obsidian', message: '首次连接此资料库',
        detail: 'PurrTypos 将自动登记此资料库。若 Obsidian 正在运行，会先正常退出再重新打开，保留已有仓库。',
        buttons: ['连接并打开', '取消'], defaultId: 0, cancelId: 1, noLink: true,
      })
      if (answer.response !== 0) return { status: 'canceled' }
      await fs.mkdir(path.dirname(registryPath), { recursive: true })
      const lock = `${registryPath}.purrtypos-lock`
      try { await fs.mkdir(lock) } catch { throw new Error('另一项 Obsidian 连接正在处理，请稍后重试') }
      let stopped = false
      try {
        if (await lifecycle.running()) {
          await lifecycle.quit()
          for (let i = 0; i < 40 && await lifecycle.running(); i++) await sleep(250)
          if (await lifecycle.running()) throw new Error('Obsidian 尚未退出，原配置未修改')
          stopped = true
        }
        snapshot = await readRegistry(registryPath)
        vault = await findVault(snapshot.data, root, target)
        if (!vault) {
          const id = crypto.randomBytes(8).toString('hex')
          const data = { ...snapshot.data, vaults: { ...snapshot.data.vaults, [id]: { path: root, ts: Date.now(), open: true } } }
          const token = crypto.randomBytes(8).toString('hex')
          const temp = `${registryPath}.${token}.tmp`
          if (snapshot.raw !== null) await fs.writeFile(`${registryPath}.purrtypos-${token}.bak`, snapshot.raw, { flag: 'wx', mode: 0o600 })
          try {
            await fs.writeFile(temp, JSON.stringify(data), { flag: 'wx', mode: 0o600 })
            if (await lifecycle.running() || (await readRegistry(registryPath)).raw !== snapshot.raw) throw new Error('Obsidian 配置正在变化，本次未覆盖，请重试')
            await fs.rename(temp, registryPath)
          } finally { await fs.rm(temp, { force: true }) }
          vault = { id, root }
        }
      } catch (error) {
        if (stopped) await (lifecycle.open ? lifecycle.open('obsidian://choose-vault') : shell.openExternal('obsidian://choose-vault')).catch(() => {})
        throw error
      } finally { await fs.rmdir(lock) }
    }
    // Use the registered ID for root opens and a relative path for note opens.
    let uri = 'obsidian://open?vault=' + encodeURIComponent(vault.id)
    const relative = path.relative(vault.root, target).split(path.sep).join('/')
    if (relative.includes('#')) throw new Error('Obsidian 将文件名中的 # 识别为标题定位，无法直接打开此笔记')
    if (relative) uri += '&file=' + encodeURIComponent(relative)
    await (lifecycle.open ? lifecycle.open(uri) : shell.openExternal(uri))
    return { status: 'dispatched' }
  }
  return { open(source) {
    const result = tail.then(() => open(source))
    tail = result.catch(() => {})
    return result
  } }
}
module.exports = { createObsidianConnection, readRegistry, contains }
