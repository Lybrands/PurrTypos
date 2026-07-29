'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {
  configureAppIcon,
  getAppIconCandidates,
  loadAppIcon,
} = require('./app_icon')

test('development prefers the padded application icon', () => {
  const candidates = getAppIconCandidates({
    app: { isPackaged: false },
    platform: 'darwin',
    moduleDir: '/project/electron',
    resourcesPath: '/resources',
  })

  assert.deepEqual(candidates, [
    path.join('/project/electron', '..', 'build', 'icon.png'),
    path.join('/project/electron', '..', 'public', 'PurrTypos.png'),
  ])
})

test('packaged macOS prefers the copied PNG over Windows ICO', () => {
  const candidates = getAppIconCandidates({
    app: { isPackaged: true, getAppPath: () => '/resources/app.asar' },
    platform: 'darwin',
    resourcesPath: '/resources',
  })

  assert.deepEqual(candidates, [
    path.join('/resources', 'icon.png'),
    path.join('/resources/app.asar', 'public', 'PurrTypos.png'),
    path.join('/resources', 'icon.ico'),
  ])
})

test('macOS Dock receives the first valid app icon', () => {
  const dockIcons = []
  const image = { isEmpty: () => false }
  const loaded = configureAppIcon({
    app: {
      isPackaged: false,
      dock: { setIcon: (value) => dockIcons.push(value) },
    },
    nativeImage: { createFromPath: () => image },
    platform: 'darwin',
    moduleDir: '/project/electron',
    fsImpl: { existsSync: () => true },
    logger: { warn() {} },
  })

  assert.equal(loaded.image, image)
  assert.deepEqual(dockIcons, [image])
})

test('empty native images are ignored', () => {
  const loaded = loadAppIcon({
    app: { isPackaged: false },
    nativeImage: { createFromPath: () => ({ isEmpty: () => true }) },
    platform: 'linux',
    moduleDir: '/project/electron',
    fsImpl: { existsSync: () => true },
  })

  assert.equal(loaded.image, null)
  assert.equal(loaded.path, null)
})
