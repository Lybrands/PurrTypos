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
  summary: '1M 上下文，Max 思考模式，最大 1M 输出',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  maxOutputTokens: 1_048_576,
  supportsThinking: true,
  thinkingOnly: true,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
  recommended: true,
} as const

export const kimiK3Profile: BuiltinModelProfile = {
  provider,
  preset,
}
