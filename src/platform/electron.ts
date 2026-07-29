import type { PlatformApi } from './types'

export function createElectronPlatformApi(): PlatformApi {
  const bridge = window.purrDesktop
  if (!bridge) {
    throw new Error('Electron desktop bridge is unavailable')
  }
  return bridge
}
