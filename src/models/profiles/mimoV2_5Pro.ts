import type { AiModelConfig } from '../../types'
import { normalizeBaseUrl, normalizePresetContextWindow } from '../shared'
import type { BuiltinModelProfile } from '../types'

const provider = {
  id: 'mimo',
  name: '小米 MiMo',
  apiProvider: 'openai',
  baseUrl: 'https://api.xiaomimimo.com/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'mimo:mimo-v2.5-pro',
  providerId: 'mimo',
  name: 'mimo-v2.5-pro',
  label: 'Mimo V2.5 Pro',
  summary: '来自当前自定义配置',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  maxOutputTokens: 131_072,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: false,
  customizeTemperature: false,
  temperatureThinking: 0.6,
  temperatureNonThinking: 0.6,
  recommended: true,
} as const

function matches(config: AiModelConfig) {
  return config.name === preset.name && normalizeBaseUrl(config.baseUrl) === provider.baseUrl
}

export const mimoV2_5ProProfile: BuiltinModelProfile = {
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
    }
  },
}
