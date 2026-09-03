import type { AiApiProvider, AiContextWindow, AiModelConfig } from '../types'
import { contextWindowTokens } from './shared'
import { getModelPreset } from './registry'

export function normalizeApiProvider(value: unknown): AiApiProvider {
  return value === 'anthropic' || value === 'zai' ? value : 'openai'
}

export const AI_CONTEXT_WINDOW_LABELS: Readonly<Record<AiContextWindow, string>> = {
  '32k': '32K',
  '64k': '64K',
  '128k': '128K',
  '200k': '200K',
  '256k': '256K',
  '300k': '300K',
  '1m': '1M',
}

const AI_CUSTOM_CONTEXT_WINDOWS: readonly AiContextWindow[] = ['32k', '128k', '256k', '1m']

export function getModelContextWindowOptions(config?: AiModelConfig | null) {
  const preset = getModelPreset(config?.presetId)
  if (preset) return preset.contextWindowOptions

  const current = config?.contextWindow
  if (!current || AI_CUSTOM_CONTEXT_WINDOWS.includes(current)) return AI_CUSTOM_CONTEXT_WINDOWS
  return [...AI_CUSTOM_CONTEXT_WINDOWS, current].sort(
    (left, right) => contextWindowTokens(left) - contextWindowTokens(right),
  )
}

export function getDefaultModelContextWindow(config?: AiModelConfig | null): AiContextWindow {
  return config?.contextWindow ?? getModelPreset(config?.presetId)?.contextWindow ?? '128k'
}

/** 返回供应商已登记的模型能力上限；任务预算由后端 PurrA 决定。 */
export function getModelMaxOutputTokens(config?: AiModelConfig | null): number | undefined {
  return getModelPreset(config?.presetId)?.maxOutputTokens
}

export function isModelThinkingEnabled(config?: AiModelConfig | null) {
  return config?.thinkingEnabled
}

export function applyModelRuntimeConfigPatch(
  config: AiModelConfig,
  patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
): AiModelConfig {
  return {
    ...config,
    ...patch,
    thinkingOnly: config.thinkingOnly,
  }
}
