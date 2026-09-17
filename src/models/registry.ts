import type { AiBuiltinProviderId } from '../types'
import { deepseekVendorProfile } from './profiles/deepseek'
import { zaiVendorProfile } from './profiles/zai'
import { moonshotVendorProfile } from './profiles/moonshot'
import { minimaxVendorProfile } from './profiles/minimax'
import { mimoVendorProfile } from './profiles/mimo'
import type { BuiltinModelProfile } from './types'
import type { AiContextWindow, AiReasoningEffort } from '../types'
import { MODEL_DESCRIPTORS } from './descriptors.generated'

const presentations = [
  zaiVendorProfile,
  deepseekVendorProfile,
  moonshotVendorProfile,
  minimaxVendorProfile,
  mimoVendorProfile,
]

type Descriptor = (typeof MODEL_DESCRIPTORS)[number]
let descriptors: readonly Descriptor[] = MODEL_DESCRIPTORS

function buildProfiles(): BuiltinModelProfile[] {
  return presentations.map(profile => {
  const descriptor = descriptors.find(d => d.profileId === profile.preset.id)
  if (!descriptor) throw new Error(`Missing model descriptor: ${profile.preset.id}`)
  return { ...profile, preset: {
    ...profile.preset,
    contextWindowOptions: descriptor.contextWindowOptions as readonly AiContextWindow[],
    contextWindow: descriptor.defaultContextWindow as AiContextWindow,
    maxGenerationTokens: descriptor.maxGenerationTokens,
    supportsThinking: descriptor.reasoningControl !== ('unavailable' as string),
    thinkingOnly: descriptor.reasoningControl === ('always_enabled' as string),
    thinkingEnabled: descriptor.defaultThinkingEnabled,
    reasoningEffortOptions: descriptor.reasoningEffortOptions as readonly AiReasoningEffort[],
    customizeTemperature: descriptor.customizeTemperature,
    temperatureThinking: descriptor.defaultTemperatureThinking,
    temperatureNonThinking: descriptor.defaultTemperatureNonThinking,
  } }
  })
}

export const AI_BUILTIN_MODEL_PROFILES = buildProfiles()

export const AI_BUILTIN_PROVIDERS = [
  ...new Map(
    AI_BUILTIN_MODEL_PROFILES.map((profile) => [profile.provider.id, profile.provider]),
  ).values(),
]
export const AI_MODEL_PRESETS = AI_BUILTIN_MODEL_PROFILES.map((profile) => profile.preset)

export function getModelDescriptorDigest(id: string): string | undefined {
  return descriptors.find(d => d.profileId === id)?.digest
}

export function installModelDescriptors(rows: Array<Record<string, unknown>>) {
  for (const row of rows) {
    if (row.schemaVersion !== 1 || typeof row.profileId !== 'string' || typeof row.digest !== 'string'
      || !Array.isArray(row.contextWindowOptions) || !Array.isArray(row.reasoningEffortOptions)
      || typeof row.defaultContextWindow !== 'string' || typeof row.maxGenerationTokens !== 'number') {
      throw new Error('模型能力描述无效，请更新应用')
    }
  }
  // 后端版本可能落后于前端（如开发中未重启）：下发行按 id 覆盖内置快照，
  // 缺失的条目保留内置版本，避免目录不完整导致设置加载中断。
  const served = new Map(rows.map((row) => [row.profileId as string, row]))
  descriptors = presentations.map((presentation) => {
    const bundled = MODEL_DESCRIPTORS.find(d => d.profileId === presentation.preset.id)
    if (!bundled) throw new Error(`Missing model descriptor: ${presentation.preset.id}`)
    return served.get(presentation.preset.id) as unknown as Descriptor ?? bundled
  })
  AI_BUILTIN_MODEL_PROFILES.splice(0, AI_BUILTIN_MODEL_PROFILES.length, ...buildProfiles())
  AI_MODEL_PRESETS.splice(0, AI_MODEL_PRESETS.length, ...AI_BUILTIN_MODEL_PROFILES.map(p => p.preset))
}

export function getBuiltinProvider(id: AiBuiltinProviderId | string | undefined) {
  return AI_BUILTIN_PROVIDERS.find((provider) => provider.id === id)
}

export function getModelPreset(id: string | undefined) {
  return AI_MODEL_PRESETS.find((preset) => preset.id === id)
}
