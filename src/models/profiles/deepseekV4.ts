import type { AiModelConfig } from '../../types'
import {
  normalizeBaseUrl,
  normalizePresetContextWindow,
} from '../shared'
import type { AiModelPreset, BuiltinModelProfile } from '../types'

const provider = {
  id: 'deepseek',
  name: 'DeepSeek',
  apiProvider: 'openai',
  baseUrl: 'https://api.deepseek.com',
  keyPlaceholder: '请输入 DeepSeek API Key',
} as const

const knownBaseUrls = new Set([
  provider.baseUrl,
  `${provider.baseUrl}/v1`,
])

const sharedPreset = {
  providerId: provider.id,
  summary: '1M 上下文，384K 最大输出，支持深度思考与工具调用',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  maxOutputTokens: 393_216,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
} as const

const proPreset = {
  ...sharedPreset,
  id: 'deepseek:deepseek-v4-pro',
  name: 'deepseek-v4-pro',
  label: 'DeepSeek V4 Pro',
  recommended: true,
} as const

const flashPreset = {
  ...sharedPreset,
  id: 'deepseek:deepseek-v4-flash',
  name: 'deepseek-v4-flash',
  label: 'DeepSeek V4 Flash',
  recommended: false,
} as const

function createProfile(preset: AiModelPreset): BuiltinModelProfile {
  const matches = (config: AiModelConfig) => (
    config.name === preset.name
    && knownBaseUrls.has(normalizeBaseUrl(config.baseUrl))
  )

  return {
    provider,
    preset,
    matches,
    migrate(config) {
      if (!matches(config)) return config
      return {
        ...config,
        presetId: preset.id,
        providerId: provider.id,
        apiProvider: provider.apiProvider,
        baseUrl: provider.baseUrl,
        supportsThinking: preset.supportsThinking,
        thinkingOnly: preset.thinkingOnly,
        thinkingEnabled: config.thinkingEnabled ?? preset.thinkingEnabled,
        contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
        customizeTemperature: preset.customizeTemperature,
        temperatureThinking: preset.temperatureThinking,
        temperatureNonThinking: preset.temperatureNonThinking,
      }
    },
  }
}

export const deepseekV4ProProfile = createProfile(proPreset)
export const deepseekV4FlashProfile = createProfile(flashPreset)
