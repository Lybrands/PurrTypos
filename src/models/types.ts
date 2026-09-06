import type { AiBuiltinProviderId, AiContextWindow, AiReasoningEffort } from '../types'

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
  /** 服务商公布的单次总生成上限，包含其计入同一配额的思考 token。 */
  maxGenerationTokens?: number
  supportsThinking: boolean
  thinkingOnly: boolean
  thinkingEnabled: boolean
  /** 该内置模型公开支持的用户可选思考强度。 */
  reasoningEffortOptions?: readonly AiReasoningEffort[]
  customizeTemperature: boolean
  temperatureThinking: number
  temperatureNonThinking: number
  recommended?: boolean
}

export interface BuiltinModelProfile {
  provider: AiBuiltinProvider
  preset: AiModelPreset
}

export interface BuiltinModelPresentation {
  provider: AiBuiltinProvider
  preset: Pick<AiModelPreset, 'id' | 'providerId' | 'name' | 'label' | 'summary' | 'recommended'>
}
