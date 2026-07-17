import type { AiBuiltinProviderId, AiContextWindow, AiModelConfig } from '../types'

export interface AiBuiltinProvider {
  id: AiBuiltinProviderId
  name: string
  apiProvider: 'openai' | 'anthropic'
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
  matches(config: AiModelConfig): boolean
  migrate(config: AiModelConfig): AiModelConfig
}
