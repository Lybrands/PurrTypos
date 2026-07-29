import { browserPlatformApi } from './browser'
import { createElectronPlatformApi } from './electron'
import type { RuntimeCapabilities } from './types'

const isElectron = typeof window !== 'undefined' && Boolean(window.purrDesktop)

export const platformApi = isElectron
  ? createElectronPlatformApi()
  : browserPlatformApi

export const runtimeCapabilities: RuntimeCapabilities = {
  runtime: isElectron ? 'electron' : 'browser',
  openLocalPath: isElectron,
  openDatabaseDirectory: isElectron,
  nativeSaveDialog: isElectron,
  directoryExport: isElectron,
}

export type { DesktopBridge, PlatformApi, RuntimeCapabilities } from './types'
