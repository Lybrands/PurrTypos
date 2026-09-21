import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'minimax',
  name: 'MiniMax',
  apiProvider: 'openai',
  baseUrl: 'https://api.minimaxi.com/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'minimax',
  providerId: 'minimax',
  namePlaceholder: '如 MiniMax-M3',
  label: 'MiniMax',
  summary: '接入固定，模型名按 MiniMax 文档填写',
  recommended: true,
} as const

export const minimaxVendorProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
