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
  id: 'moonshot:kimi-k2.6',
  providerId: 'moonshot',
  name: 'kimi-k2.6',
  label: 'Kimi K2.6',
  summary: '来自当前自定义配置',
  contextWindowOptions: ['32k', '128k', '256k'],
  contextWindow: '256k',
  defaultOutputTokens: 16_384,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: true,
  customizeTemperature: true,
  temperatureThinking: 1,
  temperatureNonThinking: 0.6,
  recommended: false,
} as const

function matches(config: AiModelConfig) {
  return config.name === preset.name && normalizeBaseUrl(config.baseUrl) === provider.baseUrl
}

export const kimiK2_6Profile: BuiltinModelProfile = {
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
      outputTokenBudget: normalizePresetOutputTokenBudget(config.outputTokenBudget, preset),
    }
  },
}
