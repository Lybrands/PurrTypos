import React from "react";
import {
  CheckCircleIcon,
  AlertCircleIcon,
  LoadingIcon,
} from '@/purr-components';
import { type ChatMessage } from "../../hooks";
import Markdown from "../Markdown";
import ToolCallStatus from "../ToolCallStatus";
import SettingDiffCard from "../SettingDiffCard";
import ToolApprovalCard from "../ToolApprovalCard";
import WorkLog from "../WorkLog";
import SubAgentStatusList from "../SubAgentStatusList";
import StructuredQuestionCard from "../StructuredQuestionCard";
import ErrorReportNotice from "./ErrorReportNotice";
import { parseStructuredQuestions } from "../../structuredQuestions";
import {
  buildAssistantTimeline,
  getAssistantProcessingLabel,
  type AssistantTimelinePart,
  type TimelineStepPart,
} from "./assistantTimeline";
import "./AssistantMessageBody.scss";

export interface AssistantMessageBodyProps {
  index: number;
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean;
  showPlaceholder: boolean;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
  onStructuredAnswer?: (answer: string) => void;
}

const PROCESSING_STANDBY_DELAY_MS = 1000;

function getTimelineActivityKey(
  parts: AssistantTimelinePart[],
  processingLabel: string,
): string {
  const activity = parts.map((part) => {
    if (part.type === "thinking") return `thinking:${part.text.length}`;
    if (part.type === "text") return `text:${part.md.length}`;
    if (part.type === "commentary") return `commentary:${part.md.length}`;
    if (part.type === "tools") {
      return [
        "tools",
        part.segment.textBefore.length,
        part.segment.labels.length,
        part.segment.completedToolCount ?? 0,
        part.segment.labelOutcomes?.join(",") ?? "",
      ].join(":");
    }
    if (part.type === "delegations") {
      return `delegations:${JSON.stringify(part.items).length}`;
    }
    return `context:${JSON.stringify(part.state).length}`;
  });
  return `${processingLabel}|${activity.join("|")}`;
}

function useProcessingStandby(
  active: boolean,
  activityKey: string,
): boolean {
  const [settledActivityKey, setSettledActivityKey] = React.useState<
    string | null
  >(null);

  React.useEffect(() => {
    if (!active) {
      setSettledActivityKey(null);
      return;
    }
    const timer = window.setTimeout(
      () => setSettledActivityKey(activityKey),
      PROCESSING_STANDBY_DELAY_MS,
    );
    return () => window.clearTimeout(timer);
  }, [active, activityKey]);

  return active && settledActivityKey === activityKey;
}

function isVisibleWorkLogPart(part: AssistantTimelinePart): boolean {
  if (part.type !== "tools") return part.type !== "text";
  return part.segment.labels.some(
    (_label, labelIndex) => !part.segment.cachedFlags?.[labelIndex],
  );
}

function workLogHasError(parts: AssistantTimelinePart[]): boolean {
  return parts.some((part) => {
    if (part.type === "contextCompaction") {
      return part.state.status === "failed";
    }
    if (part.type === "delegations") {
      return part.items.some((item) => item.status === "failed");
    }
    if (part.type !== "tools") return false;
    return part.segment.labelOutcomes?.some(
      (outcome, labelIndex) =>
        outcome === "context_error" &&
        !part.segment.cachedFlags?.[labelIndex],
    );
  });
}

function AssistantMessageBodyInner({
  index,
  message,
  loading,
  isLastAssistant,
  showPlaceholder,
  setScrolledUpByReason,
  onStructuredAnswer,
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
  const hasAnswerContent = answerParts.length > 0;
  const hasWorkLog = workLogParts.length > 0;
  const processingLabel = getAssistantProcessingLabel(message);
  const activityKey = React.useMemo(
    () => getTimelineActivityKey(timeline, processingLabel),
    [timeline, processingLabel],
  );
  const showProcessingStandby = useProcessingStandby(
    isStreaming,
    activityKey,
  );

  const renderStepPart = (part: TimelineStepPart) => {
    if (part.type === "thinking") {
      const isActiveStream =
        isStreaming && part.regionKey.includes("-stream-");
      return (
        <div
          key={part.regionKey}
          className={`work-log__thinking ${isActiveStream ? "work-log__thinking--active" : ""}`}
          onWheel={(event) => {
            event.stopPropagation();
            if (event.deltaY < 0) handleWheelUp();
          }}
        >
          <Markdown>{part.text}</Markdown>
        </div>
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
      {isStreaming || hasWorkLog ? (
        <WorkLog
          logKey={message.agentRunId || `${index}-work-log`}
          active={isStreaming}
          autoOpen={isStreaming && hasWorkLog && !hasAnswerContent}
          startedAt={message.turnStartedAt}
          durationMs={message.durationMs}
          hasError={workLogHasError(workLogParts)}
        >
          {workLogParts.map((part, partIndex) => {
            if (part.type === "contextCompaction") {
              const running = part.state.status === "running";
              const failed = part.state.status === "failed";
              const turnCount =
                part.state.compactedTurnCount ??
                part.state.selectedTurnCount;
              return (
                <div
                  key={`context-compaction-${partIndex}`}
                  className={`work-log__context-compaction ${failed ? "work-log__context-compaction--failed" : ""}`}
                >
                  {running ? (
                    <LoadingIcon spin />
                  ) : failed ? (
                    <AlertCircleIcon />
                  ) : (
                    <CheckCircleIcon />
                  )}
                  <span>
                    {running
                      ? "正在压缩上下文"
                      : failed
                        ? "上下文压缩未完成，已使用安全回退"
                        : "已压缩上下文"}
                    {turnCount && !failed ? ` · ${turnCount} 个较早回合` : ""}
                  </span>
                  {running ? <span className="a-blink-dots">...</span> : null}
                </div>
              );
            }
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
            if (part.type === "delegations") {
              return (
                <SubAgentStatusList
                  key={`delegations-${partIndex}`}
                  items={part.items}
                  activities={message.subAgentActivities}
                />
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
        answerParts.map((part, partIndex) => {
          if (part.type !== "text") return null;
          const structuredQuestions = isStreaming
            ? null
            : parseStructuredQuestions(part.md);
          return structuredQuestions ? (
            <StructuredQuestionCard
              key={`questions-${partIndex}`}
              questions={structuredQuestions}
              disabled={loading}
              onAnswer={onStructuredAnswer}
            />
          ) : (
            <div key={`text-${partIndex}`} className="bubble-content">
              <Markdown>{part.md}</Markdown>
            </div>
          );
        })}

      {showProcessingStandby ? (
        <div
          className="bubble-processing-standby"
          role="status"
          aria-live="polite"
        >
          <span>{processingLabel}</span>
        </div>
      ) : null}
      {message.error ? (
        <ErrorReportNotice
          message={`生成中断：${message.error}`}
          report={message.errorReport}
        />
      ) : null}
      {message.termination ? (
        <div className="bubble-content bubble-content--termination">
          {message.termination}
        </div>
      ) : null}
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
