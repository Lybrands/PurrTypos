import type { AiBuiltinProviderId, AiContextWindow, AiReasoningEffort } from '../types'

export interface AiBuiltinProvider {
  id: AiBuiltinProviderId
  name: string
  apiProvider: 'openai' | 'anthropic' | 'zai'
  baseUrl: string
  keyPlaceholder: string
}

export interface AiModelPreset {
  /** 服务商级 preset id（如 zai、deepseek、moonshot、minimax、mimo）。 */
  id: string
  providerId: AiBuiltinProviderId
  /** 模型名由用户填写；这里只提供输入提示，不内置具体名称。 */
  namePlaceholder: string
  label: string
  summary: string
  contextWindowOptions: readonly AiContextWindow[]
  contextWindow: AiContextWindow
  /** 服务商主推模型登记的默认能力上限；用户可按实际模型覆盖。 */
  maxGenerationTokens?: number
  supportsThinking: boolean
  thinkingOnly: boolean
  thinkingEnabled: boolean
  /** 该服务商模型公开支持的用户可选思考强度。 */
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
  preset: Pick<AiModelPreset, 'id' | 'providerId' | 'namePlaceholder' | 'label' | 'summary' | 'recommended'>
}
