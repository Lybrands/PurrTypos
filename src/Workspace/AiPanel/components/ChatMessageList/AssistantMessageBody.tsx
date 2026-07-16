import React from "react";
import { type ChatMessage } from "../../hooks";
import Markdown from "../Markdown";
import ToolCallStatus from "../ToolCallStatus";
import SettingDiffCard from "../SettingDiffCard";
import ThinkingRegion from "../ThinkingRegion";
import { TaskPlanSteps } from "../TaskPlanCard";
import ToolApprovalCard from "../ToolApprovalCard";
import WorkLog, { WorkLogStepGroup } from "../WorkLog";
import {
  buildAssistantTimeline,
  groupConsecutiveWorkSteps,
  type AssistantTimelinePart,
  type TimelineStepPart,
} from "./assistantTimeline";

export interface AssistantMessageBodyProps {
  index: number;
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean;
  showPlaceholder: boolean;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
}

function isVisibleWorkLogPart(part: AssistantTimelinePart): boolean {
  if (part.type !== "tools") return part.type !== "text";
  return part.segment.labels.some(
    (_label, labelIndex) => !part.segment.cachedFlags?.[labelIndex],
  );
}

function workLogHasError(parts: AssistantTimelinePart[]): boolean {
  return parts.some((part) => {
    if (part.type === "taskPlan") return part.plan.status === "failed";
    if (part.type !== "tools") return false;
    return part.segment.labelOutcomes?.some(
      (outcome, labelIndex) =>
        outcome === "context_error" &&
        !part.segment.cachedFlags?.[labelIndex],
    );
  });
}

function getStepCount(parts: TimelineStepPart[]): number {
  return parts.reduce((count, part) => {
    if (part.type === "thinking") return count + 1;
    return (
      count +
      part.segment.labels.filter(
        (_label, labelIndex) => !part.segment.cachedFlags?.[labelIndex],
      ).length
    );
  }, 0);
}

function stepPartsHaveError(parts: TimelineStepPart[]): boolean {
  return parts.some(
    (part) =>
      part.type === "tools" &&
      part.segment.labelOutcomes?.some(
        (outcome, labelIndex) =>
          outcome === "context_error" &&
          !part.segment.cachedFlags?.[labelIndex],
      ),
  );
}

function getStepDuration(parts: TimelineStepPart[]): number {
  return parts.reduce((duration, part) => {
    const partDuration =
      part.type === "thinking" ? part.durationMs : part.segment.durationMs;
    return duration + (partDuration ?? 0);
  }, 0);
}

function getActiveStepStartedAt(
  parts: TimelineStepPart[],
): number | undefined {
  const lastPart = parts.at(-1);
  if (!lastPart) return undefined;
  if (lastPart.type === "thinking") {
    return lastPart.durationMs == null ? lastPart.startedAt : undefined;
  }
  return lastPart.segment.durationMs == null
    ? lastPart.segment.startedAt
    : undefined;
}

function AssistantMessageBodyInner({
  index,
  message,
  loading,
  isLastAssistant,
  showPlaceholder,
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

  const answerParts = timeline.filter((part) => part.type === "text");
  const workLogParts = timeline.filter(isVisibleWorkLogPart);
  const workLogItems = groupConsecutiveWorkSteps(workLogParts, index);
  const hasAnswerContent = answerParts.length > 0;
  const hasWorkLog = workLogParts.length > 0;

  const renderStepPart = (part: TimelineStepPart) => {
    if (part.type === "thinking") {
      const isActiveStream =
        isStreaming && part.regionKey.includes("-stream-");
      return (
        <ThinkingRegion
          key={part.regionKey}
          regionKey={part.regionKey}
          content={part.text}
          streaming={isActiveStream}
          startedAt={part.startedAt}
          durationMs={part.durationMs}
          showCursor={isActiveStream && !hasAnswerContent}
          onWheelUp={handleWheelUp}
        />
      );
    }

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
  };

  return (
    <div className="bubble-assistant-body">
      {showPlaceholder && (
        <div className="bubble-content bubble-content--thinking-placeholder">
          <span className="bubble-placeholder-text">正在思考</span>
          <span className="a-blink-dots">...</span>
        </div>
      )}

      {!showPlaceholder && hasWorkLog ? (
        <WorkLog
          logKey={message.agentRunId || `${index}-work-log`}
          active={isStreaming}
          autoOpen={isStreaming && !hasAnswerContent}
          startedAt={message.turnStartedAt}
          durationMs={message.durationMs}
          hasError={workLogHasError(workLogParts)}
        >
          {workLogItems.map((part, partIndex) => {
            if (part.type === "commentary") {
              return (
                <div
                  key={`${part.type}-${partIndex}`}
                  className="work-log__commentary"
                >
                  <Markdown>{part.md}</Markdown>
                </div>
              );
            }
            if (part.type === "taskPlan") {
              const completed = part.plan.steps.filter(
                (step) => step.status === "done",
              ).length;
              return (
                <div key={`task-plan-${partIndex}`} className="work-log__plan">
                  <div className="work-log__plan-header">
                    <span>任务计划</span>
                    <span className="work-log__plan-count">
                      {completed}/{part.plan.steps.length}
                    </span>
                  </div>
                  <TaskPlanSteps plan={part.plan} />
                </div>
              );
            }
            if (part.type === "stepGroup") {
              const groupActive =
                isStreaming &&
                part.parts.some(
                  (stepPart) =>
                    (stepPart.type === "thinking" &&
                      stepPart.regionKey.includes("-stream-")) ||
                    (stepPart.type === "tools" && Boolean(stepPart.isLive)),
                );
              return (
                <WorkLogStepGroup
                  key={part.groupKey}
                  groupKey={part.groupKey}
                  stepCount={getStepCount(part.parts)}
                  completedDurationMs={getStepDuration(part.parts)}
                  activeStartedAt={
                    groupActive
                      ? getActiveStepStartedAt(part.parts)
                      : undefined
                  }
                  active={groupActive}
                  hasError={stepPartsHaveError(part.parts)}
                >
                  {part.parts.map(renderStepPart)}
                </WorkLogStepGroup>
              );
            }
            if (part.type === "thinking" || part.type === "tools") {
              return renderStepPart(part);
            }
            return null;
          })}
        </WorkLog>
      ) : null}

      {!showPlaceholder &&
        answerParts.map((part, partIndex) =>
          part.type === "text" ? (
            <div key={`text-${partIndex}`} className="bubble-content">
              <Markdown>{part.md}</Markdown>
            </div>
          ) : null,
        )}

      {!showPlaceholder && isLastAssistant && loading && hasAnswerContent && (
        <div className="bubble-content bubble-content--waiting-dots">
          <span className="a-blink-dots">...</span>
        </div>
      )}
      {(message.settingDiffCards || []).map((card) => (
        <SettingDiffCard key={card.sessionKey} card={card} />
      ))}
      {(message.toolApprovals || []).map((approval) => (
        <ToolApprovalCard key={approval.approvalId} approval={approval} />
      ))}
    </div>
  );
}

export default React.memo(AssistantMessageBodyInner);
