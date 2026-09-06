import type { BuiltinModelPresentation } from '../types'

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
  recommended: true,
} as const

export const glm5_3FlashProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
