import type { BuiltinModelPresentation } from '../types'

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
  recommended: true,
} as const

export const minimaxM3Profile: BuiltinModelPresentation = {
  provider,
  preset,
}
