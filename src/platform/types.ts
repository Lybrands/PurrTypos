import type { ElectronAPI } from '../types'
import type { PlatformApiKey } from '../services/backendApi'

export type PlatformApi = Pick<ElectronAPI, PlatformApiKey>

export interface RuntimeCapabilities {
  runtime: 'electron' | 'browser'
  openLocalPath: boolean
  openDatabaseDirectory: boolean
  nativeSaveDialog: boolean
  directoryExport: boolean
}

export type DesktopBridge = PlatformApi
