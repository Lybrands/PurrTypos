import React from "react";
import type { EntityId } from "../../../../types";
import { type ChatMessage } from "../../hooks";
import Markdown from "../Markdown";
import ToolCallStatus from "../ToolCallStatus";
import SettingDiffCard from "../SettingDiffCard";
import ThinkingRegion from "../ThinkingRegion";
import SubagentResultCard from "../SubagentResultCard";
import TaskPlanCard from "../TaskPlanCard";
import { buildAssistantTimeline } from "./assistantTimeline";

export interface AssistantMessageBodyProps {
  index: number;
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean;
  showPlaceholder: boolean;
  chapterId: EntityId | null | undefined;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
}

function AssistantMessageBodyInner({
  index,
  message,
  loading,
  isLastAssistant,
  showPlaceholder,
  chapterId,
  setScrolledUpByReason,
}: AssistantMessageBodyProps) {
  const isStreaming = loading && isLastAssistant;
  const handleWheelUp = () =>
    setScrolledUpByReason(true, "thinking-region-wheel-up");

  const timeline = React.useMemo(
    () =>
      buildAssistantTimeline(message, {
        messageIndex: index,
        isStreaming,
        isLastAssistant,
        loading,
      }),
    [message, index, isStreaming, isLastAssistant, loading],
  );

  const hasGeneratedContent = timeline.some(
    (p) => p.type === "text" || p.type === "digest" || p.type === "tools",
  );

  return (
    <div className="bubble-assistant-body">
      {showPlaceholder && (
        <div className="bubble-content bubble-content--thinking-placeholder">
          <span className="bubble-placeholder-text">正在思考</span>
          <span className="a-blink-dots">...</span>
        </div>
      )}
      {!showPlaceholder &&
        timeline.map((part, partIdx) => {
          if (part.type === "digest") {
            return (
              <div
                key={`digest-${partIdx}`}
                className="bubble-content bubble-content--subagent-digest"
              >
                <Markdown>{part.md}</Markdown>
              </div>
            );
          }
          if (part.type === "taskPlan") {
            return (
              <TaskPlanCard
                key={`task-plan-${message.agentRunId || part.plan.title || "local"}`}
                plan={part.plan}
              />
            );
          }
          if (part.type === "thinking") {
            const isActiveStream =
              isStreaming &&
              part.regionKey.includes("-stream-") &&
              partIdx === timeline.length - 1;
            return (
              <ThinkingRegion
                key={part.regionKey}
                regionKey={part.regionKey}
                content={part.text}
                streaming={isActiveStream}
                startedAt={part.startedAt}
                durationMs={part.durationMs}
                showCursor={
                  isActiveStream &&
                  !(message.content || message.contentAfterToolCalls)
                }
                onWheelUp={handleWheelUp}
              />
            );
          }
          if (part.type === "tools") {
            const seg = part.segment;
            const toolCompletedCount = part.isLive
              ? (seg.completedToolCount ?? 0)
              : seg.labels.length;
            return (
              <ToolCallStatus
                key={`tools-${part.segmentIndex}`}
                labels={seg.labels}
                labelOutcomes={seg.labelOutcomes}
                cachedFlags={seg.cachedFlags}
                completedToolCount={toolCompletedCount}
                startedAt={seg.startedAt}
                durationMs={seg.durationMs}
                streaming={Boolean(part.isLive)}
              />
            );
          }
          if (part.type === "text") {
            return (
              <div key={`text-${partIdx}`} className="bubble-content">
                <Markdown>{part.md}</Markdown>
              </div>
            );
          }
          return null;
        })}
      {!showPlaceholder && isLastAssistant && loading && hasGeneratedContent && (
        <div className="bubble-content bubble-content--waiting-dots">
          <span className="a-blink-dots">...</span>
        </div>
      )}
      {message.writingSubagentActive ? (
        <div className="bubble-content bubble-content--waiting-dots">
          <span className="a-blink-dots">
            {message.writingSubagentLabel || "子专家"}处理中…
          </span>
        </div>
      ) : null}
      {message.subagentResult ? (
        <SubagentResultCard
          role={message.subagentResult.role}
          payload={message.subagentResult.payload}
          chapterId={chapterId}
        />
      ) : null}
      {(message.settingDiffCards || []).map((card) => (
        <SettingDiffCard key={card.sessionKey} card={card} />
      ))}
    </div>
  );
}

export default React.memo(AssistantMessageBodyInner);
