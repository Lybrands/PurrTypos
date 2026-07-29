import React from "react";
import { Tooltip } from "../../../../ui";
import type { AiModelConfig } from "../../../../types";
import { contextWindowTokens } from "../../../../models/shared";
import { getDefaultModelContextWindow } from "../../../../models/runtime";
import {
  calculateContextUsage,
  formatContextTokens,
} from "../../contextUsage";
import type { ChatMessage } from "../../hooks/chat.types";
import "./index.scss";

export interface ContextUsageIndicatorProps {
  conversations: ChatMessage[];
  selectedModelConfig: AiModelConfig | null;
}

export default function ContextUsageIndicator({
  conversations,
  selectedModelConfig,
}: ContextUsageIndicatorProps) {
  const usage = React.useMemo(
    () => calculateContextUsage({
      messages: conversations,
      windowTokens: contextWindowTokens(
        getDefaultModelContextWindow(selectedModelConfig),
      ),
    }),
    [conversations, selectedModelConfig],
  );
  if (!usage) return null;

  const percent = Math.max(0, Math.round(usage.ratio * 100));
  const formattedUsage =
    `${formatContextTokens(usage.usedTokens)} / ${formatContextTokens(usage.windowTokens)}`;
  const tooltip = (
    <div className="context-usage-tooltip">
      <div>最近一次模型实际输入 {formattedUsage}</div>
      <div className="context-usage-tooltip__hint">
        仅使用模型供应商上报值，不包含本地估算。
      </div>
    </div>
  );

  return (
    <Tooltip title={tooltip} placement="top">
      <span
        className="context-usage"
        aria-label={`最近一次模型实际输入 ${formattedUsage}`}
      >
        <span
          className="context-usage__gauge"
          style={{ "--context-usage": `${Math.min(100, percent)}%` } as React.CSSProperties}
        />
        <span className="context-usage__value">{formattedUsage}</span>
      </span>
    </Tooltip>
  );
}
