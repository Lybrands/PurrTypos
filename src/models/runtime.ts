import type { AiApiProvider, AiContextWindow, AiModelConfig } from '../types'
import { contextWindowTokens } from './shared'
import { getBuiltinProvider, getModelPreset } from './registry'
import type { AiModelPreset } from './types'

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
  return Boolean(config?.thinkingOnly || config?.thinkingEnabled)
}

export function applyModelRuntimeConfigPatch(
  config: AiModelConfig,
  patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
): AiModelConfig {
  const thinkingEnabled = config.thinkingOnly
    ? true
    : (patch.thinkingEnabled ?? isModelThinkingEnabled(config))
  return {
    ...config,
    ...patch,
    supportsThinking: thinkingEnabled === true || config.supportsThinking,
    thinkingOnly: config.thinkingOnly,
    thinkingEnabled,
  }
}

export function createConfigFromPreset(params: {
  id: string
  preset: AiModelPreset
  apiKey: string
  nickname?: string
}): AiModelConfig {
  const provider = getBuiltinProvider(params.preset.providerId)
  if (!provider) throw new Error(`未知的内置模型服务商：${params.preset.providerId}`)

  return {
    id: params.id,
    presetId: params.preset.id,
    providerId: params.preset.providerId,
    apiProvider: provider.apiProvider,
    name: params.preset.name,
    nickname: params.nickname?.trim() || params.preset.label,
    supportsThinking: params.preset.supportsThinking,
    thinkingOnly: params.preset.thinkingOnly,
    thinkingEnabled: params.preset.thinkingEnabled,
    contextWindow: params.preset.contextWindow,
    customizeTemperature: params.preset.customizeTemperature,
    temperatureThinking: params.preset.temperatureThinking,
    temperatureNonThinking: params.preset.temperatureNonThinking,
    apiKey: params.apiKey.trim(),
    baseUrl: provider.baseUrl,
  }
}
