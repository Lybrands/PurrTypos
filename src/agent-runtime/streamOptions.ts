import type { AiContextWindow, AiModelConfig, AiReasoningEffort } from '../types.ts'
import {
  getDefaultModelContextWindow,
  getModelReasoningEffort,
  isModelThinkingEnabled,
} from '../modelCatalog.ts'
import { getModelDescriptorDigest } from '../models/registry.ts'

export interface StreamRequestOptions {
  model_preferences?: AiModelConfig['modelPreferences'];
  model_descriptor_digest?: string;
  profile_binding?: 'compatible';
  model: string;
  model_profile?: string;
  temperature?: number;
  thinking?: { type: 'enabled' | 'disabled'; budget_tokens?: number };
  reasoning_effort?: AiReasoningEffort;
  profile_max_generation_tokens?: number;
  max_generation_tokens?: number;
  supports_thinking?: boolean;
  thinking_only?: boolean;
  context_window?: AiContextWindow;
}

export interface BuiltStream {
  options: StreamRequestOptions;
  /** 实际生效的 thinking 开关 */
  effectiveThinking: boolean | undefined;
  /** 真正发给 API 的 model 名（cfg.name 优先，缺失退回 selectedModel） */
  apiModelName: string;
}

export function buildStreamOptions(params: {
  cfg: AiModelConfig | null;
  selectedModel: string;
}): BuiltStream {
  const { cfg, selectedModel } = params
  const effectiveThinking = isModelThinkingEnabled(cfg)
  const reasoningEffort = getModelReasoningEffort(cfg)
  const contextWindow = cfg?.presetId ? getDefaultModelContextWindow(cfg) : cfg?.contextWindow
  const apiModelName = cfg?.name ?? selectedModel
  const useConfiguredTemperature =
    cfg?.customizeTemperature === undefined || cfg?.customizeTemperature === true
  const options: StreamRequestOptions = {
    model: apiModelName,
    ...(cfg?.presetId ? { model_profile: cfg.presetId } : {}),
    ...(cfg?.modelPreferences ? { model_preferences: cfg.modelPreferences } : {}),
    ...(cfg?.presetId ? { model_descriptor_digest: getModelDescriptorDigest(cfg.presetId) } : {}),
    ...(cfg?.profileBinding ? { profile_binding: cfg.profileBinding } : {}),
    ...(useConfiguredTemperature && effectiveThinking !== undefined
      ? { temperature: effectiveThinking ? cfg?.temperatureThinking : cfg?.temperatureNonThinking }
      : {}),
    ...(effectiveThinking === undefined
      ? {}
      : {
          thinking: {
            type: effectiveThinking ? 'enabled' as const : 'disabled' as const,
            ...(effectiveThinking && cfg?.thinkingBudgetTokens
              ? { budget_tokens: cfg.thinkingBudgetTokens }
              : {}),
          },
        }),
    ...(reasoningEffort
      ? { reasoning_effort: reasoningEffort }
      : {}),
    ...(cfg?.maxGenerationTokens
      ? { max_generation_tokens: cfg.maxGenerationTokens }
      : {}),
    ...(!cfg?.presetId
      ? {
          ...(cfg?.profileMaxGenerationTokens
            ? { profile_max_generation_tokens: cfg.profileMaxGenerationTokens }
            : {}),
          supports_thinking: cfg?.supportsThinking === true,
          thinking_only: cfg?.thinkingOnly === true,
        }
      : {}),
    ...(contextWindow ? { context_window: contextWindow } : {}),
  }
  return { options, effectiveThinking, apiModelName }
}
