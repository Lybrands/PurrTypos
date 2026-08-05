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

const LEGACY_APP_OUTPUT_BUDGETS = new Set([32_768, 65_536])

/** 迁移曾由应用自动写入、且没有设置页入口可由用户选择的旧输出策略值。 */
export function normalizePresetOutputTokenBudget(
  value: number | undefined,
  preset: AiModelPreset,
) {
  const normalized = Number(value)
  if (!Number.isFinite(normalized) || normalized <= 0) {
    return preset.defaultOutputTokens
  }
  if (LEGACY_APP_OUTPUT_BUDGETS.has(normalized)) {
    return preset.defaultOutputTokens
  }
  return preset.maxOutputTokens
    ? Math.min(Math.floor(normalized), preset.maxOutputTokens)
    : Math.floor(normalized)
}

export function hasSameModelFields(left: AiModelConfig, right: AiModelConfig) {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]) as Set<keyof AiModelConfig>
  return [...keys].every((key) => left[key] === right[key])
}
