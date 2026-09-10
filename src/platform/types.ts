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

export type DesktopBridge = PlatformApi & {
  selectNovelKnowledge?: (bookId: string) => Promise<import('../types').ApiResult<{ selectionToken: string }>>
  openNovelKnowledge?: (args: { bookId: string; documentId: string; revision?: string; anchor?: string; action: 'open' | 'copy' | 'reveal' }) => Promise<import('../types').ApiResult<{ status: string; currentMatches?: boolean; anchorFallback?: boolean }>>
  openNovelKnowledgeLibrary?: (bookId: string) => Promise<import('../types').ApiResult<{ status: string }>>
}
