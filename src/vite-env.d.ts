/// <reference types="vite/client" />
/// <reference path="./modules.d.ts" />

import type { DesktopBridge } from './platform/types'

declare global {
  interface Window {
    purrDesktop?: DesktopBridge
  }
}

export {}
