import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'moonshot',
  name: 'Kimi (Moonshot)',
  apiProvider: 'openai',
  baseUrl: 'https://api.moonshot.cn/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'moonshot',
  providerId: 'moonshot',
  namePlaceholder: '如 kimi-k3、kimi-k2.6',
  label: 'Kimi (Moonshot)',
  summary: '接入固定，模型名按 Kimi 文档填写',
  recommended: true,
} as const

export const moonshotVendorProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
