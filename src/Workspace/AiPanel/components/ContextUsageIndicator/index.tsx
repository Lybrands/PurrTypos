import React from "react";
import { Tooltip } from "antd";
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
  prompt: string;
  selectedModelConfig: AiModelConfig | null;
  loading: boolean;
}

export default function ContextUsageIndicator({
  conversations,
  prompt,
  selectedModelConfig,
  loading,
}: ContextUsageIndicatorProps) {
  const usage = React.useMemo(
    () => calculateContextUsage({
      messages: conversations,
      prompt,
      windowTokens: contextWindowTokens(
        getDefaultModelContextWindow(selectedModelConfig),
      ),
      loading,
    }),
    [conversations, loading, prompt, selectedModelConfig],
  );
  const percent = Math.max(0, Math.round(usage.ratio * 100));
  const tooltip = (
    <div className="context-usage-tooltip">
      <div>上下文已用约 {formatContextTokens(usage.usedTokens)} / {formatContextTokens(usage.windowTokens)}</div>
      <div className="context-usage-tooltip__hint">
        {usage.source === "backend"
          ? "基于最近一次后端实际输入预算，当前新增文字为估算。"
          : "尚无运行预算，当前按对话文字保守估算。"}
      </div>
    </div>
  );

  return (
    <Tooltip title={tooltip} placement="top">
      <span
        className="context-usage"
        aria-label={`上下文已用 ${percent}%`}
      >
        <span
          className="context-usage__gauge"
          style={{ "--context-usage": `${Math.min(100, percent)}%` } as React.CSSProperties}
        />
        <span className="context-usage__value">
          {formatContextTokens(usage.usedTokens)} / {formatContextTokens(usage.windowTokens)}
        </span>
      </span>
    </Tooltip>
  );
}
