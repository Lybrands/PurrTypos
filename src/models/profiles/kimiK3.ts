import type { BuiltinModelPresentation } from '../types'

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
  recommended: true,
} as const

export const kimiK3Profile: BuiltinModelPresentation = {
  provider,
  preset,
}
