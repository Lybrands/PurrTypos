import type { AiModelConfig } from '../../types'
import { normalizeBaseUrl, normalizePresetContextWindow } from '../shared'
import type { BuiltinModelProfile } from '../types'

const provider = {
  id: 'zai',
  name: '智谱 AI',
  apiProvider: 'zai',
  baseUrl: 'https://open.bigmodel.cn/api/paas/v4/',
  keyPlaceholder: '请输入智谱 API Key',
} as const

const preset = {
  id: 'zai:glm-5.3-flash',
  providerId: 'zai',
  name: 'glm-5.3-flash',
  label: 'GLM-5.3-Flash',
  summary: '1M 上下文，支持深度思考与工具调用',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  maxOutputTokens: 131_072,
  supportsThinking: true,
  thinkingOnly: true,
  thinkingEnabled: true,
  customizeTemperature: true,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
  recommended: true,
} as const

function matches(config: AiModelConfig) {
  return (config.name === preset.name || config.name === 'glm-5.2')
    && normalizeBaseUrl(config.baseUrl) === normalizeBaseUrl(provider.baseUrl)
}

export const glm5_3FlashProfile: BuiltinModelProfile = {
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
      name: preset.name,
      nickname: config.nickname === 'GLM-5.2' ? preset.label : config.nickname,
      baseUrl: provider.baseUrl,
      supportsThinking: preset.supportsThinking,
      thinkingOnly: preset.thinkingOnly,
      thinkingEnabled: true,
      contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
      customizeTemperature: config.customizeTemperature ?? preset.customizeTemperature,
      temperatureThinking: config.temperatureThinking ?? preset.temperatureThinking,
      temperatureNonThinking: config.temperatureNonThinking ?? preset.temperatureNonThinking,
    }
  },
}
