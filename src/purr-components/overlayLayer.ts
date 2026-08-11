import type { CSSProperties } from 'react'

const GLOBAL_LAYER_MIN = 1000
const GLOBAL_LAYER_MAX = 1999

export function isValidOverlayZIndex(zIndex: number): boolean {
  return Number.isFinite(zIndex)
    && Number.isInteger(zIndex)
    && zIndex >= GLOBAL_LAYER_MIN
    && zIndex <= GLOBAL_LAYER_MAX
}

function warnInvalidOverlayZIndex(componentName: string, zIndex: number): void {
  if (!import.meta.env?.DEV || isValidOverlayZIndex(zIndex)) return
  console.warn(
    `[${componentName}] zIndex 应为 ${GLOBAL_LAYER_MIN}–${GLOBAL_LAYER_MAX} 的有限整数；当前值仍会按显式覆盖应用：${String(zIndex)}`,
  )
}

export function getOverlayLayerStyle(
  componentName: string,
  zIndex?: number,
): CSSProperties | undefined {
  if (zIndex === undefined) return undefined
  warnInvalidOverlayZIndex(componentName, zIndex)
  return { zIndex }
}

export function getBlockingLayerStyles(
  componentName: string,
  zIndex?: number,
): { backdrop?: CSSProperties; surface?: CSSProperties } {
  if (zIndex === undefined) return {}
  warnInvalidOverlayZIndex(componentName, zIndex)
  return {
    backdrop: { zIndex: zIndex - 10 },
    surface: { zIndex },
  }
}
