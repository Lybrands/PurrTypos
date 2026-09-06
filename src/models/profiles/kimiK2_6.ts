import type { BuiltinModelPresentation } from '../types'

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
  recommended: false,
} as const

export const kimiK2_6Profile: BuiltinModelPresentation = {
  provider,
  preset,
}
