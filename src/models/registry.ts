import type { AiBuiltinProviderId } from '../types'
import { deepseekV4FlashProfile } from './profiles/deepseekV4'
import { glm5_3FlashProfile } from './profiles/glm5_3Flash'
import { kimiK3Profile } from './profiles/kimiK3'
import { kimiK2_6Profile } from './profiles/kimiK2_6'
import { minimaxM3Profile } from './profiles/minimaxM3'
import { mimoV2_5ProProfile } from './profiles/mimoV2_5Pro'
import type { BuiltinModelProfile } from './types'

export const AI_BUILTIN_MODEL_PROFILES: readonly BuiltinModelProfile[] = [
  glm5_3FlashProfile,
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
