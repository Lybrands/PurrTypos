import React from "react";
import { PurrTooltip } from '@/purr-components';
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
  /** 尚未发送的输入框内容，属于下一次请求的当前上下文。 */
  draft?: string;
}

export default function ContextUsageIndicator({
  conversations,
  selectedModelConfig,
  draft = "",
}: ContextUsageIndicatorProps) {
  const windowTokens = contextWindowTokens(
    getDefaultModelContextWindow(selectedModelConfig),
  );
  const usage = React.useMemo(
    () => calculateContextUsage({
      messages: conversations,
      windowTokens,
      draft,
      modelConfigId: selectedModelConfig?.id,
      modelName: selectedModelConfig?.name,
    }),
    [
      conversations,
      draft,
      selectedModelConfig?.id,
      selectedModelConfig?.name,
      windowTokens,
    ],
  );
  if (!selectedModelConfig) return null;

  const percent = Math.max(0, Math.round(usage.ratio * 100));
  const formattedWindow = formatContextTokens(usage.windowTokens);
  const formattedUsed = formatContextTokens(usage.usedTokens);
  const remainingTokens = Math.max(0, usage.windowTokens - usage.usedTokens);
  const formattedRemaining = formatContextTokens(remainingTokens);
  const ariaLabel = `当前已用 ${formattedUsed}，剩余可用 ${formattedRemaining}，最大上下文 ${formattedWindow}，已用 ${percent}%`;
  const tooltip = (
    <div className="context-usage-tooltip">
      <span className="context-usage-tooltip__label">当前已用</span>
      <span className="context-usage-tooltip__value">{formattedUsed}</span>
      <span className="context-usage-tooltip__label">剩余可用</span>
      <span className="context-usage-tooltip__value">{formattedRemaining}</span>
      <span className="context-usage-tooltip__label">最大上下文</span>
      <span className="context-usage-tooltip__value">{formattedWindow}</span>
      <span className="context-usage-tooltip__label">已用比例</span>
      <span className="context-usage-tooltip__value">{percent}%</span>
    </div>
  );

  return (
    <PurrTooltip title={tooltip} placement="top">
      <span
        className="context-usage"
        aria-label={ariaLabel}
      >
        <span
          className="context-usage__gauge"
          style={{ "--context-usage": `${Math.min(100, percent)}%` } as React.CSSProperties}
        />
      </span>
    </PurrTooltip>
  );
}
