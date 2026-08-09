import type { AiModelConfig } from '../../types'
import { normalizeBaseUrl, normalizePresetContextWindow } from '../shared'
import type { BuiltinModelProfile } from '../types'

const provider = {
  id: 'minimax',
  name: 'MiniMax',
  apiProvider: 'openai',
  baseUrl: 'https://api.minimaxi.com/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'minimax:MiniMax-M3',
  providerId: 'minimax',
  name: 'MiniMax-M3',
  label: 'MiniMax M3',
  summary: '来自当前自定义配置',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  supportsThinking: true,
  thinkingOnly: true,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 0.6,
  temperatureNonThinking: 0.6,
  recommended: true,
} as const

const legacyNames = new Set(['MiniMax-M3.0', preset.name])
const knownBaseUrls = new Set([
  'https://api.minimaxi.com/anthropic',
  provider.baseUrl,
])

function matches(config: AiModelConfig) {
  return legacyNames.has(config.name) && knownBaseUrls.has(normalizeBaseUrl(config.baseUrl))
}

export const minimaxM3Profile: BuiltinModelProfile = {
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
      name: preset.name,
      nickname: config.nickname === 'MiniMax M3.0' ? preset.label : config.nickname,
      supportsThinking: preset.supportsThinking,
      thinkingOnly: preset.thinkingOnly,
      thinkingEnabled: preset.thinkingEnabled,
      contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
    }
  },
}
