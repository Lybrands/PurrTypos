import type { AiContextWindow } from '../types'

export function contextWindowTokens(value: AiContextWindow) {
  return value === '1m' ? 1_000_000 : Number.parseInt(value, 10) * 1_000
}
