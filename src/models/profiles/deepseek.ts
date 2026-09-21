import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'deepseek',
  name: 'DeepSeek',
  apiProvider: 'openai',
  baseUrl: 'https://api.deepseek.com',
  keyPlaceholder: '请输入 DeepSeek API Key',
} as const

const preset = {
  id: 'deepseek',
  providerId: 'deepseek',
  namePlaceholder: '如 deepseek-v4-flash',
  label: 'DeepSeek',
  summary: '接入固定，模型名按 DeepSeek 文档填写',
  recommended: true,
} as const

export const deepseekVendorProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
