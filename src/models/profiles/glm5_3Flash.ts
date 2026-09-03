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

export const glm5_3FlashProfile: BuiltinModelProfile = {
  provider,
  preset,
}
