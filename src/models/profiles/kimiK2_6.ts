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
  summary: '256K 上下文，支持思考切换与工具调用',
  contextWindowOptions: ['32k', '128k', '256k'],
  contextWindow: '256k',
  maxOutputTokens: 262_144,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 1,
  temperatureNonThinking: 0.6,
  recommended: false,
} as const

export const kimiK2_6Profile: BuiltinModelProfile = {
  provider,
  preset,
}
