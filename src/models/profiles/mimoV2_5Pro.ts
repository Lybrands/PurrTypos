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

export const mimoV2_5ProProfile: BuiltinModelProfile = {
  provider,
  preset,
}
