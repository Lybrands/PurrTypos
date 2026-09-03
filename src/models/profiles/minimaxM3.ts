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
  summary: '1M 上下文，512K 最大输出，支持自适应思考',
  contextWindowOptions: ['32k', '256k', '1m'],
  contextWindow: '1m',
  maxOutputTokens: 524_288,
  supportsThinking: true,
  thinkingOnly: false,
  thinkingEnabled: true,
  customizeTemperature: false,
  temperatureThinking: 1,
  temperatureNonThinking: 1,
  recommended: true,
} as const

export const minimaxM3Profile: BuiltinModelProfile = {
  provider,
  preset,
}
