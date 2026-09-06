import type {
  AiApiProvider,
  AiContextWindow,
  AiModelConfig,
  AiReasoningEffort,
} from '../types'
import { contextWindowTokens } from './shared'
import { getModelPreset } from './registry'

export function normalizeApiProvider(value: unknown): AiApiProvider {
  if (value == null || value === '' || value === 'openai') return 'openai'
  if (value === 'anthropic' || value === 'zai') return value
  throw new Error('不支持的模型协议')
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

export const AI_REASONING_EFFORT_LABELS: Readonly<Record<AiReasoningEffort, string>> = {
  low: '低',
  high: '高',
  max: '最大',
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

/** 返回版本化 profile 或自定义配置声明的模型能力上限。 */
export function getModelProfileMaxGenerationTokens(config?: AiModelConfig | null): number | undefined {
  return getModelPreset(config?.presetId)?.maxGenerationTokens
    ?? config?.profileMaxGenerationTokens
}

export function isModelThinkingEnabled(config?: AiModelConfig | null) {
  const choice = config?.modelPreferences?.reasoning_mode
  if (choice) return choice.state === 'explicit' ? choice.value === 'enabled' : undefined
  return config?.thinkingEnabled
}

export function getModelReasoningEffortOptions(
  config?: AiModelConfig | null,
): readonly AiReasoningEffort[] {
  return getModelPreset(config?.presetId)?.reasoningEffortOptions ?? []
}

export function getModelReasoningEffort(
  config?: AiModelConfig | null,
): AiReasoningEffort | undefined {
  const choice = config?.modelPreferences?.reasoning_effort
  if (choice) return choice.state === 'explicit' ? choice.value : undefined
  return config?.reasoningEffort
}

export function applyModelRuntimeConfigPatch(
  config: AiModelConfig,
  patch: Partial<Pick<
    AiModelConfig,
    'contextWindow' | 'thinkingEnabled' | 'reasoningEffort' | 'modelPreferences'
  >>,
): AiModelConfig {
  const preferences = { ...config.modelPreferences, ...patch.modelPreferences }
  if ('thinkingEnabled' in patch && !patch.modelPreferences?.reasoning_mode) {
    preferences.reasoning_mode = patch.thinkingEnabled === undefined
      ? { state: 'provider_default' } : { state: 'explicit', value: patch.thinkingEnabled ? 'enabled' : 'disabled' }
    if (preferences.temperature?.state === 'explicit') {
      const temperature = patch.thinkingEnabled ? config.temperatureThinking : config.temperatureNonThinking
      preferences.temperature = temperature === undefined || patch.thinkingEnabled === undefined
        ? { state: 'provider_default' } : { state: 'explicit', value: temperature }
    }
  }
  if ('reasoningEffort' in patch && !patch.modelPreferences?.reasoning_effort) {
    preferences.reasoning_effort = patch.reasoningEffort === undefined
      ? { state: 'provider_default' } : { state: 'explicit', value: patch.reasoningEffort }
  }
  return {
    ...config,
    ...patch,
    modelSettingsVersion: 1,
    modelPreferences: preferences,
    thinkingOnly: config.thinkingOnly,
  }
}
