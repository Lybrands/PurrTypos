'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const { createObsidianConnection, readRegistry } = require('./obsidian_connection')

async function fixture(t, options = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'obsidian-connect-'))
  t.after(() => fs.rm(dir, { recursive: true, force: true }))
  const root = path.join(dir, '中文 #& 仓库')
  await fs.mkdir(root)
  const note = path.join(root, '人物 空格&?.md')
  await fs.writeFile(note, '# 临时人物\n测试内容')
  const registryPath = path.join(dir, 'obsidian.json')
  const original = { language: 'zh', vaults: { existing: { path: dir, ts: 123, open: true } }, other: { preserve: true } }
  // Existing vault is a sibling, not a parent of the new work.
  original.vaults.existing.path = path.join(dir, 'existing')
  await fs.mkdir(original.vaults.existing.path)
  await fs.writeFile(registryPath, JSON.stringify(original))
  const calls = []
  let running = options.running ?? true
  const connection = createObsidianConnection({ platform: 'darwin', registryPath, getWindow: () => null,
    dialog: { showMessageBox: async () => { calls.push('confirm'); return { response: options.cancel ? 1 : 0 } } },
    shell: { openExternal: async uri => { calls.push(uri) } },
    sleep: async () => {},
    lifecycle: { installed: async () => {}, running: async () => running, quit: async () => {
      calls.push('quit')
      if (!options.refuseQuit) running = false
      if (options.onQuit) await options.onQuit(registryPath)
    } },
  })
  return { connection, root, note, registryPath, original, calls, dir }
}
test('first library open registers after graceful exit, preserves settings, backs up and opens ID', async t => {
  const f = await fixture(t)
  assert.equal((await f.connection.open({ path: f.root, vaultPath: f.root })).status, 'dispatched')
  const data = (await readRegistry(f.registryPath)).data
  assert.deepEqual(data.vaults.existing, f.original.vaults.existing)
  assert.deepEqual(data.other, f.original.other)
  const id = Object.keys(data.vaults).find(id => id !== 'existing')
  assert.equal(data.vaults[id].path, await fs.realpath(f.root))
  assert.deepEqual(f.calls, ['confirm', 'quit', `obsidian://open?vault=${id}`])
  const backup = (await fs.readdir(f.dir)).find(file => file.endsWith('.bak'))
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(f.dir, backup))), f.original)
})
test('first note open and subsequent library open share registration, correct file encoding, no duplicate prompt', async t => {
  const f = await fixture(t)
  await f.connection.open({ path: f.note, vaultPath: f.root })
  const uri = new URL(f.calls.at(-1))
  assert.equal(uri.searchParams.get('file'), '人物 空格&?.md')
  await f.connection.open({ path: f.root, vaultPath: f.root })
  assert.equal(new URL(f.calls.at(-1)).searchParams.get('vault'), uri.searchParams.get('vault'))
  assert.equal(f.calls.filter(c => c === 'confirm').length, 1)
  assert.equal(await fs.readFile(f.note, 'utf8'), '# 临时人物\n测试内容')
})
test('cancel does not quit, mutate registry or dispatch URL', async t => {
  const f = await fixture(t, { cancel: true })
  assert.deepEqual(await f.connection.open({ path: f.root, vaultPath: f.root }), { status: 'canceled' })
  assert.deepEqual((await readRegistry(f.registryPath)).data, f.original)
  assert.deepEqual(f.calls, ['confirm'])
})
test('refused graceful exit never writes or forces process termination', async t => {
  const f = await fixture(t, { refuseQuit: true })
  await assert.rejects(f.connection.open({ path: f.root, vaultPath: f.root }), /尚未退出/)
  assert.deepEqual((await readRegistry(f.registryPath)).data, f.original)
  assert.deepEqual(f.calls, ['confirm', 'quit'])
})
test('re-reads final settings written by Obsidian at exit', async t => {
  const f = await fixture(t, { onQuit: async file => {
    const current = JSON.parse(await fs.readFile(file, 'utf8')); current.language = 'en';
    await fs.writeFile(file, JSON.stringify(current))
  } })
  await f.connection.open({ path: f.root, vaultPath: f.root })
  assert.equal((await readRegistry(f.registryPath)).data.language, 'en')
})
test('malformed registry and path escapes fail closed without dispatch', async t => {
  const f = await fixture(t)
  await assert.rejects(f.connection.open({ path: f.registryPath, vaultPath: f.root }), /不属于/)
  await fs.writeFile(f.registryPath, '{bad')
  await assert.rejects(f.connection.open({ path: f.root, vaultPath: f.root }), /无法读取/)
  assert.equal(await fs.readFile(f.registryPath, 'utf8'), '{bad')
  assert.deepEqual(f.calls, [])
})
test('uses existing enclosing vault and serializes simultaneous first opens', async t => {
  const f = await fixture(t)
  await Promise.all([f.connection.open({ path: f.root, vaultPath: f.root }), f.connection.open({ path: f.note, vaultPath: f.root })])
  assert.equal(f.calls.filter(c => c === 'confirm').length, 1)
  const nested = path.join(f.root, '资料'); await fs.mkdir(nested)
  const note = path.join(nested, '人物.md'); await fs.writeFile(note, '内容')
  await f.connection.open({ path: note, vaultPath: nested })
  assert.equal(new URL(f.calls.at(-1)).searchParams.get('file'), '资料/人物.md')
  assert.equal(f.calls.filter(c => c === 'confirm').length, 1)
})
