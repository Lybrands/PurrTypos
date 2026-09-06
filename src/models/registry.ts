import type { AiBuiltinProviderId } from '../types'
import { deepseekV4FlashProfile } from './profiles/deepseekV4'
import { glm5_3FlashProfile } from './profiles/glm5_3Flash'
import { kimiK3Profile } from './profiles/kimiK3'
import { kimiK2_6Profile } from './profiles/kimiK2_6'
import { minimaxM3Profile } from './profiles/minimaxM3'
import { mimoV2_5ProProfile } from './profiles/mimoV2_5Pro'
import type { BuiltinModelProfile } from './types'
import type { AiContextWindow, AiReasoningEffort } from '../types'
import { MODEL_DESCRIPTORS } from './descriptors.generated'

const presentations = [
  glm5_3FlashProfile,
  deepseekV4FlashProfile,
  kimiK3Profile,
  kimiK2_6Profile,
  minimaxM3Profile,
  mimoV2_5ProProfile,
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
    thinkingOnly: descriptor.reasoningControl === 'always_enabled',
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
  if (presentations.some(p => !rows.some(d => d.profileId === p.preset.id))) throw new Error('模型能力目录不完整')
  descriptors = rows as unknown as readonly Descriptor[]
  AI_BUILTIN_MODEL_PROFILES.splice(0, AI_BUILTIN_MODEL_PROFILES.length, ...buildProfiles())
  AI_MODEL_PRESETS.splice(0, AI_MODEL_PRESETS.length, ...AI_BUILTIN_MODEL_PROFILES.map(p => p.preset))
}

export function getBuiltinProvider(id: AiBuiltinProviderId | string | undefined) {
  return AI_BUILTIN_PROVIDERS.find((provider) => provider.id === id)
}

export function getModelPreset(id: string | undefined) {
  return AI_MODEL_PRESETS.find((preset) => preset.id === id)
}
