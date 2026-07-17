import type { AiContextWindow, AiModelConfig } from "../../../types";
import { getDefaultModelContextWindow, isModelThinkingEnabled } from "../../../modelCatalog";

export interface StreamRequestOptions {
  model: string;
  model_profile?: string;
  temperature?: number;
  thinking: { type: "enabled" | "disabled" };
  context_window: AiContextWindow;
  max_tokens: number;
}

export interface BuiltStream {
  options: StreamRequestOptions;
  /** 实际生效的 thinking 开关 */
  effectiveThinking: boolean;
  /** 真正发给 API 的 model 名（cfg.name 优先，缺失退回 selectedModel） */
  apiModelName: string;
}

/**
 * 构造 aiChatStream 的 options 字段。规则：
 * - Context / Thinking 来自当前模型自己的配置，不在对话输入区临时覆盖。
 * - cfg.customizeTemperature 显式 false 时不传 temperature（让模型走默认）。
 */
export function buildStreamOptions(params: {
  cfg: AiModelConfig | null;
  modelConfigs: Record<string, { max_tokens?: number }>;
  selectedModel: string;
}): BuiltStream {
  const { cfg, modelConfigs, selectedModel } = params;
  const modelConfig = modelConfigs[selectedModel];

  const effectiveThinking = isModelThinkingEnabled(cfg);
  const contextWindow = getDefaultModelContextWindow(cfg);

  const apiModelName = cfg?.name ?? selectedModel;
  const useConfiguredTemperature =
    cfg?.customizeTemperature === undefined || cfg?.customizeTemperature === true;

  const options: StreamRequestOptions = {
    model: apiModelName,
    ...(cfg?.presetId ? { model_profile: cfg.presetId } : {}),
    ...(useConfiguredTemperature
      ? {
          temperature: effectiveThinking
            ? (cfg?.temperatureThinking ?? 0.6)
            : (cfg?.temperatureNonThinking ?? 0.6),
        }
      : {}),
    thinking: { type: effectiveThinking ? "enabled" : "disabled" },
    context_window: contextWindow,
    max_tokens: modelConfig?.max_tokens ?? 8192,
  };

  return { options, effectiveThinking, apiModelName };
}
