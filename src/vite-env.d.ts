/// <reference types="vite/client" />
/// <reference path="./modules.d.ts" />

import type { ElectronAPI } from './types'

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export {}
