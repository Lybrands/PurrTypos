import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'zai',
  name: '智谱 GLM',
  apiProvider: 'zai',
  baseUrl: 'https://open.bigmodel.cn/api/paas/v4/',
  keyPlaceholder: '请输入智谱 API Key',
} as const

const preset = {
  id: 'zai',
  providerId: 'zai',
  namePlaceholder: '如 glm-5.3-flash',
  label: '智谱 GLM',
  summary: '接入固定，模型名按智谱文档填写',
  recommended: true,
} as const

export const zaiVendorProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
