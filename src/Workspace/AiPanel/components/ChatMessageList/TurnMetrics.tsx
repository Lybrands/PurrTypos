import React from "react";
import {
  CheckCircleOutlined,
  LoadingOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { ChatMessage } from "../../hooks";

function useNow(active: boolean) {
  const [now, setNow] = React.useState(() => performance.now());
  React.useEffect(() => {
    if (!active) return;
    setNow(performance.now());
    const timer = window.setInterval(() => setNow(performance.now()), 500);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function formatDuration(ms: number) {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  const seconds = ms / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)}秒` : `${Math.round(seconds)}秒`;
}

export default function TurnMetrics({ message, streaming }: {
  message: ChatMessage;
  streaming: boolean;
}) {
  const now = useNow(streaming);
  const totalMs = streaming && message.turnStartedAt != null
    ? Math.max(0, now - message.turnStartedAt)
    : message.durationMs;
  const completedThinkingMs = (message.thinkingDurationsMs ?? []).reduce(
    (sum, value) => sum + Math.max(0, value),
    0,
  );
  const activeThinkingMs = streaming && message.thinkingStartedAt != null
    ? Math.max(0, now - message.thinkingStartedAt)
    : 0;
  const thinkingMs = completedThinkingMs + activeThinkingMs;
  const segments = message.toolCallSegments ?? [];
  const toolCount = segments.reduce(
    (sum, segment) => sum + segment.labels.filter((_, index) => !segment.cachedFlags?.[index]).length,
    0,
  );
  const toolMs = segments.reduce((sum, segment) => {
    if (segment.durationMs != null) return sum + segment.durationMs;
    if (streaming && segment.startedAt != null) return sum + Math.max(0, now - segment.startedAt);
    return sum;
  }, 0);

  if (totalMs == null && thinkingMs <= 0 && toolCount === 0) return null;

  return (
    <div className={`assistant-turn-meta${streaming ? " is-streaming" : ""}`}>
      {streaming ? <LoadingOutlined spin /> : <CheckCircleOutlined />}
      <span>{streaming ? "处理中" : "已完成"}</span>
      {totalMs != null && <span className="assistant-turn-stat">{formatDuration(totalMs)}</span>}
      {thinkingMs > 0 && <span className="assistant-turn-stat">思考 {formatDuration(thinkingMs)}</span>}
      {toolCount > 0 && (
        <span className="assistant-turn-stat">
          <ThunderboltOutlined /> {toolCount} 次工具{toolMs > 0 ? ` · ${formatDuration(toolMs)}` : ""}
        </span>
      )}
    </div>
  );
}
