import type { AiModelConfig } from '../../types'
import { normalizeBaseUrl, normalizePresetContextWindow, normalizePresetOutputTokenBudget } from '../shared'
import type { BuiltinModelProfile } from '../types'

const provider = {
  id: 'zai',
  name: '智谱 AI',
  apiProvider: 'zai',
  baseUrl: 'https://open.bigmodel.cn/api/paas/v4/',
  keyPlaceholder: '请输入智谱 API Key',
} as const

const preset = {
  id: 'zai:glm-5.2',
  providerId: 'zai',
  name: 'glm-5.2',
  label: 'GLM-5.2',
  summary: '1M 上下文，支持深度思考与工具调用',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  defaultOutputTokens: 16_384,
  maxOutputTokens: 131_072,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: true,
  customizeTemperature: true,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
  recommended: true,
} as const

function matches(config: AiModelConfig) {
  return config.name === preset.name
    && normalizeBaseUrl(config.baseUrl) === normalizeBaseUrl(provider.baseUrl)
}

export const glm5_2Profile: BuiltinModelProfile = {
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
      customizeTemperature: config.customizeTemperature ?? preset.customizeTemperature,
      temperatureThinking: config.temperatureThinking ?? preset.temperatureThinking,
      temperatureNonThinking: config.temperatureNonThinking ?? preset.temperatureNonThinking,
    }
  },
}
