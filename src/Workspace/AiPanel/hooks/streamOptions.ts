import type { AiModelConfig } from "../../../types";

export interface StreamRequestOptions {
  model: string;
  temperature?: number;
  thinking: { type: "enabled" | "disabled" };
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
 * - thinkingOnly 的模型一律视为开启 thinking；其余看 cfg.supportsThinking + thinkingEnabled。
 * - cfg.customizeTemperature 显式 false 时不传 temperature（让模型走默认）。
 */
export function buildStreamOptions(params: {
  cfg: AiModelConfig | null;
  modelConfigs: Record<string, { max_tokens?: number }>;
  selectedModel: string;
  thinkingEnabled: boolean;
}): BuiltStream {
  const { cfg, modelConfigs, selectedModel, thinkingEnabled } = params;
  const modelConfig = modelConfigs[selectedModel];

  const effectiveThinking = Boolean(
    cfg?.thinkingOnly ||
      (cfg?.supportsThinking && thinkingEnabled) ||
      (!cfg && thinkingEnabled),
  );

  const apiModelName = cfg?.name ?? selectedModel;
  const useConfiguredTemperature =
    cfg?.customizeTemperature === undefined || cfg?.customizeTemperature === true;

  const options: StreamRequestOptions = {
    model: apiModelName,
    ...(useConfiguredTemperature
      ? {
          temperature: effectiveThinking
            ? (cfg?.temperatureThinking ?? 0.6)
            : (cfg?.temperatureNonThinking ?? 0.6),
        }
      : {}),
    thinking: { type: effectiveThinking ? "enabled" : "disabled" },
    max_tokens: modelConfig?.max_tokens ?? 8192,
  };

  return { options, effectiveThinking, apiModelName };
}
