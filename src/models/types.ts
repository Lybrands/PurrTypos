import type { AiBuiltinProviderId, AiContextWindow } from '../types'

export interface AiBuiltinProvider {
  id: AiBuiltinProviderId
  name: string
  apiProvider: 'openai' | 'anthropic' | 'zai'
  baseUrl: string
  keyPlaceholder: string
}

export interface AiModelPreset {
  id: string
  providerId: AiBuiltinProviderId
  name: string
  label: string
  summary: string
  contextWindowOptions: readonly AiContextWindow[]
  contextWindow: AiContextWindow
  /** 服务商公布的单次响应输出上限；未知时不填写。 */
  maxOutputTokens?: number
  supportsThinking: boolean
  thinkingOnly: boolean
  thinkingEnabled: boolean
  customizeTemperature: boolean
  temperatureThinking: number
  temperatureNonThinking: number
  recommended?: boolean
}

export interface BuiltinModelProfile {
  provider: AiBuiltinProvider
  preset: AiModelPreset
}
