import type { AiBuiltinProviderId, AiContextWindow, AiModelConfig } from './types'

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
  /** 该模型在对话面板中提供的少量上下文档位，最后一项是模型上限。 */
  contextWindowOptions: readonly AiContextWindow[]
  contextWindow: AiContextWindow
  supportsThinking: boolean
  thinkingEnabled: boolean
  customizeTemperature: boolean
  temperatureThinking: number
  temperatureNonThinking: number
  recommended?: boolean
}

/**
 * 轻量内置目录：只提供当前自定义表单原本就能表达的默认值，
 * 不改变后端协议，也不阻止用户在“高级自定义”中接入其他服务。
 */
export const AI_BUILTIN_PROVIDERS: readonly AiBuiltinProvider[] = [
  {
    id: 'moonshot',
    name: 'Moonshot AI',
    apiProvider: 'openai',
    baseUrl: 'https://api.moonshot.cn/v1',
    keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    apiProvider: 'anthropic',
    baseUrl: 'https://api.minimaxi.com/anthropic',
    keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
  },
  {
    id: 'mimo',
    name: '小米 MiMo',
    apiProvider: 'openai',
    baseUrl: 'https://api.xiaomimimo.com/v1',
    keyPlaceholder: 'sk-xxxxxxxxxxxxxxxx',
  },
] as const

export const AI_MODEL_PRESETS: readonly AiModelPreset[] = [
  {
    id: 'moonshot:kimi-k2.6',
    providerId: 'moonshot',
    name: 'kimi-k2.6',
    label: 'Kimi K2.6',
    summary: '来自当前自定义配置',
    contextWindowOptions: ['32k', '128k', '256k'],
    contextWindow: '256k',
    supportsThinking: true,
    thinkingEnabled: true,
    customizeTemperature: true,
    temperatureThinking: 1,
    temperatureNonThinking: 0.6,
    recommended: true,
  },
  {
    id: 'minimax:MiniMax-M3',
    providerId: 'minimax',
    name: 'MiniMax-M3',
    label: 'MiniMax M3',
    summary: '来自当前自定义配置',
    contextWindowOptions: ['32k', '256k', '1m'],
    contextWindow: '1m',
    supportsThinking: true,
    thinkingEnabled: false,
    customizeTemperature: false,
    temperatureThinking: 0.6,
    temperatureNonThinking: 0.6,
    recommended: true,
  },
  {
    id: 'mimo:mimo-v2.5-pro',
    providerId: 'mimo',
    name: 'mimo-v2.5-pro',
    label: 'Mimo V2.5 Pro',
    summary: '来自当前自定义配置',
    contextWindowOptions: ['32k', '256k', '1m'],
    contextWindow: '1m',
    supportsThinking: true,
    thinkingEnabled: false,
    customizeTemperature: false,
    temperatureThinking: 0.6,
    temperatureNonThinking: 0.6,
    recommended: true,
  },
] as const

export function getBuiltinProvider(id: AiBuiltinProviderId | string | undefined) {
  return AI_BUILTIN_PROVIDERS.find((provider) => provider.id === id)
}

export function getModelPreset(id: string | undefined) {
  return AI_MODEL_PRESETS.find((preset) => preset.id === id)
}

export function getProviderPresets(providerId: AiBuiltinProviderId) {
  return AI_MODEL_PRESETS.filter((preset) => preset.providerId === providerId)
}

export function getDefaultPreset(providerId: AiBuiltinProviderId) {
  const presets = getProviderPresets(providerId)
  return presets.find((preset) => preset.recommended) ?? presets[0]
}

export const AI_CONTEXT_WINDOW_LABELS: Readonly<Record<AiContextWindow, string>> = {
  '32k': '32K',
  '64k': '64K',
  '128k': '128K',
  '200k': '200K',
  '256k': '256K',
  '300k': '300K',
  '1m': '1M',
}

const AI_CUSTOM_CONTEXT_WINDOWS: readonly AiContextWindow[] = ['32k', '128k', '256k', '1m']

/**
 * 内置模型只展示目录中声明且不超过模型上限的稀疏档位；高级自定义
 * 使用统一的四档，并临时保留其已存储的旧档位供用户识别和修改。
 */
export function getModelContextWindowOptions(config?: AiModelConfig | null) {
  const preset = getModelPreset(config?.presetId)
  if (preset) return preset.contextWindowOptions

  const current = config?.contextWindow
  if (!current || AI_CUSTOM_CONTEXT_WINDOWS.includes(current)) {
    return AI_CUSTOM_CONTEXT_WINDOWS
  }
  const values = [...AI_CUSTOM_CONTEXT_WINDOWS, current]
  return values.sort((left, right) => contextWindowTokens(left) - contextWindowTokens(right))
}

export function getDefaultModelContextWindow(config?: AiModelConfig | null): AiContextWindow {
  return getModelPreset(config?.presetId)?.contextWindow ?? config?.contextWindow ?? '128k'
}

export function createConfigFromPreset(params: {
  id: string
  preset: AiModelPreset
  apiKey: string
  nickname?: string
}): AiModelConfig {
  const provider = getBuiltinProvider(params.preset.providerId)
  if (!provider) throw new Error(`未知的内置模型服务商：${params.preset.providerId}`)

  return {
    id: params.id,
    presetId: params.preset.id,
    providerId: params.preset.providerId,
    apiProvider: provider.apiProvider,
    name: params.preset.name,
    nickname: params.nickname?.trim() || params.preset.label,
    supportsThinking: params.preset.supportsThinking,
    thinkingOnly: false,
    thinkingEnabled: params.preset.thinkingEnabled,
    contextWindow: params.preset.contextWindow,
    customizeTemperature: params.preset.customizeTemperature,
    temperatureThinking: params.preset.temperatureThinking,
    temperatureNonThinking: params.preset.temperatureNonThinking,
    apiKey: params.apiKey.trim(),
    baseUrl: provider.baseUrl,
  }
}

function normalizeBaseUrl(value: string | undefined) {
  return String(value ?? '').trim().replace(/\/+$/, '').toLowerCase()
}

function contextWindowTokens(value: AiContextWindow) {
  return value === '1m' ? 1_000_000 : Number.parseInt(value, 10) * 1_000
}

function normalizePresetContextWindow(
  value: AiContextWindow | undefined,
  preset: AiModelPreset,
) {
  if (!value) return preset.contextWindow
  return preset.contextWindowOptions.find(
    (candidate) => contextWindowTokens(candidate) >= contextWindowTokens(value),
  ) ?? preset.contextWindow
}

function hasSameFields(left: AiModelConfig, right: AiModelConfig) {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]) as Set<keyof AiModelConfig>
  return [...keys].every((key) => left[key] === right[key])
}

/**
 * 只迁移已经确认存在的三个旧配置。精确匹配模型名和服务地址，
 * 并且只替换旧默认上下文值，避免改动用户的其他高级自定义配置。
 */
export function migrateKnownModelConfigs(configs: readonly AiModelConfig[]) {
  let changed = false
  const migrated = configs.map((config) => {
    const baseUrl = normalizeBaseUrl(config.baseUrl)
    let next = config

    if (config.name === 'kimi-k2.6' && baseUrl === 'https://api.moonshot.cn/v1') {
      const preset = getModelPreset('moonshot:kimi-k2.6')!
      next = {
        ...config,
        presetId: preset.id,
        providerId: 'moonshot',
        contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
      }
    } else if (
      (config.name === 'MiniMax-M3.0' || config.name === 'MiniMax-M3') &&
      baseUrl === 'https://api.minimaxi.com/anthropic'
    ) {
      const preset = getModelPreset('minimax:MiniMax-M3')!
      next = {
        ...config,
        presetId: preset.id,
        providerId: 'minimax',
        name: 'MiniMax-M3',
        nickname: config.nickname === 'MiniMax M3.0' ? 'MiniMax M3' : config.nickname,
        contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
      }
    } else if (
      config.name === 'mimo-v2.5-pro' &&
      baseUrl === 'https://api.xiaomimimo.com/v1'
    ) {
      const preset = getModelPreset('mimo:mimo-v2.5-pro')!
      next = {
        ...config,
        presetId: preset.id,
        providerId: 'mimo',
        contextWindow: normalizePresetContextWindow(config.contextWindow, preset),
      }
    }

    if (!hasSameFields(config, next)) changed = true
    return next
  })

  return { configs: migrated, changed }
}
