import type { BuiltinModelPresentation } from '../types'

const provider = {
  id: 'mimo',
  name: '小米 MiMo',
  apiProvider: 'openai',
  baseUrl: 'https://api.xiaomimimo.com/v1',
  keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
} as const

const preset = {
  id: 'mimo:mimo-v2.5-pro',
  providerId: 'mimo',
  name: 'mimo-v2.5-pro',
  label: 'Mimo V2.5 Pro',
  summary: '来自当前自定义配置',
  recommended: true,
} as const

export const mimoV2_5ProProfile: BuiltinModelPresentation = {
  provider,
  preset,
}
