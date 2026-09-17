import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'mimo',
  name: '小米 MiMo',
  apiProvider: 'openai',
  baseUrl: 'https://api.xiaomimimo.com/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'mimo',
  providerId: 'mimo',
  namePlaceholder: '如 mimo-v2.5-pro',
  label: '小米 MiMo',
  summary: '接入固定，模型名按 MiMo 文档填写',
  recommended: true,
} as const

export const mimoVendorProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
