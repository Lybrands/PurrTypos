import type { AiContextWindow, AiModelConfig } from '../types.ts'
import {
  getDefaultModelContextWindow,
  isModelThinkingEnabled,
} from '../modelCatalog.ts'

export interface StreamRequestOptions {
  model: string;
  model_profile?: string;
  temperature?: number;
  thinking?: { type: 'enabled' | 'disabled' };
  context_window: AiContextWindow;
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
  const contextWindow = getDefaultModelContextWindow(cfg)
  const apiModelName = cfg?.name ?? selectedModel
  const useConfiguredTemperature =
    cfg?.customizeTemperature === undefined || cfg?.customizeTemperature === true
  const options: StreamRequestOptions = {
    model: apiModelName,
    ...(cfg?.presetId ? { model_profile: cfg.presetId } : {}),
    ...(useConfiguredTemperature && effectiveThinking !== undefined
      ? { temperature: effectiveThinking ? (cfg?.temperatureThinking ?? 0.6) : (cfg?.temperatureNonThinking ?? 0.6) }
      : {}),
    ...(effectiveThinking === undefined
      ? {}
      : { thinking: { type: effectiveThinking ? 'enabled' as const : 'disabled' as const } }),
    context_window: contextWindow,
  }
  return { options, effectiveThinking, apiModelName }
}
