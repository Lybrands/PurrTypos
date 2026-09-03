/** Shared model catalog and runtime settings for the UI. */
export type { AiBuiltinProvider, AiModelPreset, BuiltinModelProfile } from './models/types'
export {
  AI_BUILTIN_MODEL_PROFILES,
  AI_BUILTIN_PROVIDERS,
  AI_MODEL_PRESETS,
  getBuiltinProvider,
  getModelPreset,
} from './models/registry'
export {
  AI_CONTEXT_WINDOW_LABELS,
  applyModelRuntimeConfigPatch,
  getDefaultModelContextWindow,
  getModelMaxOutputTokens,
  getModelContextWindowOptions,
  isModelThinkingEnabled,
  normalizeApiProvider,
} from './models/runtime'
