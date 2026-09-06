import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'deepseek',
  name: 'DeepSeek',
  apiProvider: 'openai',
  baseUrl: 'https://api.deepseek.com',
  keyPlaceholder: '请输入 DeepSeek API Key',
} as const

const sharedPreset = {
  providerId: provider.id,
  summary: '1M 上下文，384K 最大输出，支持深度思考与工具调用',
} as const

const flashPreset = {
  ...sharedPreset,
  id: 'deepseek:deepseek-v4-flash',
  name: 'deepseek-v4-flash',
  label: 'DeepSeek V4 Flash',
  recommended: true,
} as const

export const deepseekV4FlashProfile: BuiltinModelPresentation = {
  provider,
  preset: flashPreset,
}
