import type { AiContextWindow, AiModelConfig } from '../types'
import type { AiModelPreset } from './types'

export function normalizeBaseUrl(value: string | undefined) {
  return String(value ?? '').trim().replace(/\/+$/, '').toLowerCase()
}

export function contextWindowTokens(value: AiContextWindow) {
  return value === '1m' ? 1_000_000 : Number.parseInt(value, 10) * 1_000
}

export function normalizePresetContextWindow(
  value: AiContextWindow | undefined,
  preset: AiModelPreset,
) {
  if (!value) return preset.contextWindow
  return preset.contextWindowOptions.find(
    (candidate) => contextWindowTokens(candidate) >= contextWindowTokens(value),
  ) ?? preset.contextWindow
}

export function hasSameModelFields(left: AiModelConfig, right: AiModelConfig) {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]) as Set<keyof AiModelConfig>
  return [...keys].every((key) => left[key] === right[key])
}
