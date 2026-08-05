import type { AiModelConfig } from '../../types'
import { normalizeBaseUrl, normalizePresetContextWindow, normalizePresetOutputTokenBudget } from '../shared'
import type { BuiltinModelProfile } from '../types'

const provider = {
  id: 'moonshot',
  name: 'Moonshot AI',
  apiProvider: 'openai',
  baseUrl: 'https://api.moonshot.cn/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'moonshot:kimi-k3',
  providerId: 'moonshot',
  name: 'kimi-k3',
  label: 'Kimi K3',
  summary: '1M 上下文，Max 思考模式',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  defaultOutputTokens: 16_384,
  supportsThinking: true,
  thinkingOnly: true,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
  recommended: true,
} as const

function matches(config: AiModelConfig) {
  return config.name === preset.name && normalizeBaseUrl(config.baseUrl) === provider.baseUrl
}

export const kimiK3Profile: BuiltinModelProfile = {
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
      thinkingEnabled: preset.thinkingEnabled,
      contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
      outputTokenBudget: normalizePresetOutputTokenBudget(config.outputTokenBudget, preset),
      customizeTemperature: preset.customizeTemperature,
    }
  },
}
