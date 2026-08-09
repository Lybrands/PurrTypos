import type { AiBuiltinProviderId, AiModelConfig } from '../types'
import { deepseekV4FlashProfile, deepseekV4ProProfile } from './profiles/deepseekV4'
import { glm5_2Profile } from './profiles/glm5_2'
import { kimiK3Profile } from './profiles/kimiK3'
import { kimiK2_6Profile } from './profiles/kimiK2_6'
import { minimaxM3Profile } from './profiles/minimaxM3'
import { mimoV2_5ProProfile } from './profiles/mimoV2_5Pro'
import { hasSameModelFields } from './shared'
import type { BuiltinModelProfile } from './types'

export const AI_BUILTIN_MODEL_PROFILES: readonly BuiltinModelProfile[] = [
  glm5_2Profile,
  deepseekV4ProProfile,
  deepseekV4FlashProfile,
  kimiK3Profile,
  kimiK2_6Profile,
  minimaxM3Profile,
  mimoV2_5ProProfile,
]

export const AI_BUILTIN_PROVIDERS = [
  ...new Map(
    AI_BUILTIN_MODEL_PROFILES.map((profile) => [profile.provider.id, profile.provider]),
  ).values(),
]
export const AI_MODEL_PRESETS = AI_BUILTIN_MODEL_PROFILES.map((profile) => profile.preset)

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

export function migrateKnownModelConfigs(configs: readonly AiModelConfig[]) {
  let changed = false
  const migrated: AiModelConfig[] = configs.map((config) => {
    const current = config.outputTokenBudget === undefined ? config : { ...config }
    if (current !== config) {
      delete current.outputTokenBudget
      changed = true
    }
    const profile = AI_BUILTIN_MODEL_PROFILES.find((candidate) => candidate.matches(current))
    const next = profile?.migrate(current) ?? current
    if (!hasSameModelFields(config, next)) changed = true
    return next
  })

  for (const profile of AI_BUILTIN_MODEL_PROFILES) {
    if (migrated.some((config) => config.presetId === profile.preset.id)) continue
    const providerKey = migrated.find(
      (config) => config.providerId === profile.provider.id && config.apiKey?.trim(),
    )?.apiKey ?? ''
    const stableId = `builtin_${profile.preset.id.replace(/[^a-zA-Z0-9]+/g, '_')}`
    migrated.push({
      id: stableId,
      presetId: profile.preset.id,
      providerId: profile.provider.id,
      apiProvider: profile.provider.apiProvider,
      name: profile.preset.name,
      nickname: profile.preset.label,
      supportsThinking: profile.preset.supportsThinking,
      thinkingOnly: profile.preset.thinkingOnly,
      thinkingEnabled: profile.preset.thinkingEnabled,
      contextWindow: profile.preset.contextWindow,
      customizeTemperature: profile.preset.customizeTemperature,
      temperatureThinking: profile.preset.temperatureThinking,
      temperatureNonThinking: profile.preset.temperatureNonThinking,
      apiKey: providerKey,
      baseUrl: profile.provider.baseUrl,
    })
    changed = true
  }
  const ordered = [
    ...AI_BUILTIN_MODEL_PROFILES.flatMap((profile) =>
      migrated.filter((config) => config.presetId === profile.preset.id),
    ),
    ...migrated.filter((config) => !getModelPreset(config.presetId)),
  ]
  if (ordered.some((config, index) => config !== migrated[index])) changed = true
  return { configs: ordered, changed }
}
