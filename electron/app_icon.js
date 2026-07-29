'use strict'

const fs = require('node:fs')
const path = require('node:path')

function getAppIconCandidates({
  app,
  platform = process.platform,
  resourcesPath = process.resourcesPath,
  moduleDir = __dirname,
}) {
  const developmentIcon = path.join(moduleDir, '..', 'build', 'icon.png')
  const developmentFallback = path.join(moduleDir, '..', 'public', 'PurrTypos.png')
  if (!app.isPackaged) return [developmentIcon, developmentFallback]

  const packagedPng = path.join(resourcesPath, 'icon.png')
  const bundledPng = path.join(app.getAppPath(), 'public', 'PurrTypos.png')
  const packagedIco = path.join(resourcesPath, 'icon.ico')

  return platform === 'win32'
    ? [packagedIco, packagedPng, bundledPng]
    : [packagedPng, bundledPng, packagedIco]
}

function loadAppIcon({
  app,
  nativeImage,
  platform = process.platform,
  resourcesPath = process.resourcesPath,
  moduleDir = __dirname,
  fsImpl = fs,
}) {
  const candidates = getAppIconCandidates({
    app,
    platform,
    resourcesPath,
    moduleDir,
  })

  for (const iconPath of candidates) {
    if (!fsImpl.existsSync(iconPath)) continue
    const image = nativeImage.createFromPath(iconPath)
    if (!image.isEmpty()) return { image, path: iconPath }
  }

  return { image: null, path: null }
}

function configureAppIcon({
  app,
  nativeImage,
  platform = process.platform,
  resourcesPath = process.resourcesPath,
  moduleDir = __dirname,
  fsImpl = fs,
  logger = console,
}) {
  const loaded = loadAppIcon({
    app,
    nativeImage,
    platform,
    resourcesPath,
    moduleDir,
    fsImpl,
  })

  if (!loaded.image) {
    logger.warn('PurrTypos app icon could not be loaded')
    return loaded
  }

  if (platform === 'darwin' && app.dock?.setIcon) {
    app.dock.setIcon(loaded.image)
  }

  return loaded
}

module.exports = {
  configureAppIcon,
  getAppIconCandidates,
  loadAppIcon,
}
