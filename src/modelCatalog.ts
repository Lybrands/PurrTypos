/** Public facade retained for existing UI imports and external tests. */
export type { AiBuiltinProvider, AiModelPreset, BuiltinModelProfile } from './models/types'
export {
  AI_BUILTIN_MODEL_PROFILES,
  AI_BUILTIN_PROVIDERS,
  AI_MODEL_PRESETS,
  getBuiltinProvider,
  getDefaultPreset,
  getModelPreset,
  getProviderPresets,
  migrateKnownModelConfigs,
} from './models/registry'
export {
  AI_CONTEXT_WINDOW_LABELS,
  applyModelRuntimeConfigPatch,
  createConfigFromPreset,
  getDefaultModelContextWindow,
  getModelContextWindowOptions,
  isModelThinkingEnabled,
} from './models/runtime'
