import React from "react";
import {
  CheckCircleIcon,
  AlertCircleIcon,
  LoadingIcon,
} from '@/purr-components';
import type { AgentConversationMessage } from "../../../agent-runtime";
import type { CanonicalOperation } from "../../../agent-runtime/canonicalOutput";
import Markdown from "../../Markdown";
import ToolCallStatus from "../ToolCallStatus";
import ToolApproval from "../ToolApproval";
import ExecutionLog, { ExecutionLogStepGroup } from "../ExecutionLog";
import DelegationStatus from "../DelegationStatus";
import StructuredQuestion from "../StructuredQuestion";
import ErrorReportNotice from "../ErrorReportNotice";
import { parseStructuredQuestions } from "../StructuredQuestion/parser";
import { getErrorNoticeMessage } from "./errorNoticeMessage";
import {
  buildAssistantTimeline,
  getExecutionPanelLogKey,
  getExecutionPanelPresentation,
  getAssistantProcessingLabel,
  getOperationGroupProgress,
  groupConsecutiveWorkSteps,
  type AssistantTimelinePart,
  type TimelineOperationPart,
  type TimelineStepPart,
} from "./timeline";
import "./index.scss";

export interface AssistantOutputProps {
  index: number;
  message: AgentConversationMessage;
  loading: boolean;
  isLastAssistant: boolean;
  showPlaceholder: boolean;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
  onStructuredAnswer?: (answer: string) => void;
  onResolveToolApproval: (
    approvalId: string,
    approved: boolean,
  ) => Promise<{ success: boolean; error?: string }>;
  onSubmitErrorReport?: (
    reportId: string,
  ) => Promise<{ success: boolean; error?: string }>;
}

const PROCESSING_STANDBY_DELAY_MS = 1000;

function getTimelineActivityKey(
  parts: AssistantTimelinePart[],
  processingLabel: string,
): string {
  const activity = parts.map((part) => {
    if (part.type === "commentary") return `commentary:${part.md.length}`;
    if (part.type === "text") return `text:${part.md.length}`;
    if (part.type === "tools") {
      return [
        "tools",
        part.segment.labels.length,
        part.segment.completedToolCount ?? 0,
        part.segment.labelOutcomes?.join(",") ?? "",
      ].join(":");
    }
    if (part.type === "delegations") {
      return `delegations:${JSON.stringify(part.items).length}`;
    }
    if (part.type === "operation") {
      return `operation:${part.operation.operationId}:${part.operation.status}`;
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
    if (part.type === "operation") {
      return part.operation.status === "failed";
    }
    if (part.type !== "tools") return false;
    return part.segment.labelOutcomes?.some(
      (outcome, labelIndex) =>
        outcome === "context_error" &&
        !part.segment.cachedFlags?.[labelIndex],
    );
  });
}

function operationPartIsActive(part: TimelineOperationPart): boolean {
  if (part.type === "operation") return part.operation.status === "running";
  if (part.type === "tools") return Boolean(part.isLive);
  if (part.type === "contextCompaction") {
    return part.state.status === "running";
  }
  return part.items.some((item) =>
    ["queued", "claimed", "running"].includes(item.status)
  );
}

function operationPartCompletedDuration(part: TimelineOperationPart): number {
  if (part.type === "operation") {
    return part.operation.status === "running"
      ? 0
      : Math.max(0, part.operation.durationMs ?? 0);
  }
  if (part.type !== "tools") return 0;
  if (part.segment.itemDurationsMs) {
    return part.segment.itemDurationsMs.reduce<number>(
      (total, duration) => total + Math.max(0, duration ?? 0),
      0,
    );
  }
  return part.isLive ? 0 : Math.max(0, part.segment.durationMs ?? 0);
}

function operationPartActiveStartedAt(
  part: TimelineOperationPart,
): number | undefined {
  if (part.type !== "tools" || !part.isLive) return undefined;
  return part.segment.activeItemStartedAt ?? part.segment.startedAt;
}

function CanonicalOperationRow({
  operation,
  label,
}: {
  operation: CanonicalOperation;
  label: string;
}) {
  const running = operation.status === "running";
  const failed = operation.status === "failed";
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [running]);
  const startedAt = Date.parse(operation.startedAt);
  const durationMs = operation.durationMs ?? (
    running && Number.isFinite(startedAt)
      ? Math.max(0, now - startedAt)
      : undefined
  );
  const text = running
    ? `正在执行 ${label}`
    : failed
      ? `执行失败 ${label}`
      : operation.status === "canceled"
        ? `已取消 ${label}`
        : `已完成 ${label}`;
  return (
    <div
      className={`bubble-tool-call-line ${running ? "a-flicker-opacity" : ""} bubble-tool-call-line--${running ? "running" : "done"}`}
    >
      {running ? (
        <LoadingIcon spin className="bubble-tool-call-icon" />
      ) : failed ? (
        <AlertCircleIcon className="bubble-tool-call-icon" />
      ) : (
        <CheckCircleIcon className="bubble-tool-call-icon" />
      )}
      <span>{text}</span>
      {durationMs != null ? (
        <span className="bubble-tool-call-duration">
          · {formatOperationDuration(durationMs)}
        </span>
      ) : null}
    </div>
  );
}

function formatOperationDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}分${remainder}秒` : `${minutes}分钟`;
}

function AssistantOutputInner({
  index,
  message,
  loading,
  isLastAssistant,
  showPlaceholder,
  setScrolledUpByReason,
  onStructuredAnswer,
  onResolveToolApproval,
  onSubmitErrorReport,
}: AssistantOutputProps) {
  const isStreaming = loading && isLastAssistant;
  const handleWheelUp = () =>
    setScrolledUpByReason(true, "commentary-wheel-up");

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
  const executionLogParts = timeline.filter(isVisibleWorkLogPart);
  const executionPanel = getExecutionPanelPresentation(executionLogParts, {
    isStreaming,
    durationMs: message.durationMs,
  });
  const executionPanelLogKey = getExecutionPanelLogKey(message)
    ?? `message-${index}-execution-log`;
  const executionLogItems = React.useMemo(
    () => groupConsecutiveWorkSteps(
      executionLogParts,
      executionPanelLogKey,
    ),
    [executionLogParts, executionPanelLogKey],
  );
  const processingLabel = getAssistantProcessingLabel(message);
  const activityKey = React.useMemo(
    () => getTimelineActivityKey(timeline, processingLabel),
    [timeline, processingLabel],
  );
  const processingStandbyReady = useProcessingStandby(
    isStreaming,
    activityKey,
  );
  const showProcessingStandby = Boolean(processingLabel) && processingStandbyReady;

  const renderStepPart = (part: TimelineStepPart) => {
    if (part.type === "commentary") {
      const isActiveStream =
        isStreaming && part.regionKey.includes("-stream-");
      return (
        <div
          key={part.regionKey}
          className={`work-log__commentary ${isActiveStream ? "work-log__commentary--active" : ""}`}
          onWheel={(event) => {
            event.stopPropagation();
            if (event.deltaY < 0) handleWheelUp();
          }}
        >
          <Markdown preserveSoftBreaks>{part.md}</Markdown>
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
        itemDurationsMs={seg.itemDurationsMs}
        activeItemStartedAt={part.isLive ? seg.activeItemStartedAt : undefined}
      />
    );
  };

  const renderOperationPart = (
    part: TimelineOperationPart,
    key: React.Key,
  ) => {
    if (part.type === "operation") {
      return (
        <div key={key} className="bubble-tool-call-details">
          <CanonicalOperationRow
            operation={part.operation}
            label={part.label}
          />
        </div>
      );
    }
    if (part.type === "contextCompaction") {
      const running = part.state.status === "running";
      const failed = part.state.status === "failed";
      const turnCount =
        part.state.compactedTurnCount ?? part.state.selectedTurnCount;
      return (
        <div
          key={key}
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
    if (part.type === "delegations") {
      return (
        <DelegationStatus
          key={key}
          items={part.items}
          activities={message.subAgentActivities}
        />
      );
    }
    return renderStepPart(part);
  };

  const renderExecutionLogItem = (
    part: ReturnType<typeof groupConsecutiveWorkSteps>[number],
    partIndex: number,
  ) => {
    if (part.type === "stepGroup") {
      const progress = getOperationGroupProgress(part.parts);
      return (
        <ExecutionLogStepGroup
          key={part.groupKey}
          groupKey={part.groupKey}
          stepCount={progress.total}
          completedDurationMs={part.parts.reduce(
            (total, item) => total + operationPartCompletedDuration(item),
            0,
          )}
          activeStartedAt={part.parts
            .map(operationPartActiveStartedAt)
            .find((startedAt) => startedAt != null)}
          active={part.parts.some(operationPartIsActive)}
          hasError={workLogHasError(part.parts)}
        >
          {part.parts.map((item, itemIndex) => renderOperationPart(
            item,
            `${part.groupKey}-${item.type}-${itemIndex}`,
          ))}
        </ExecutionLogStepGroup>
      );
    }
    if (part.type === "commentary") return renderStepPart(part);
    if (
      part.type === "tools" ||
      part.type === "operation" ||
      part.type === "delegations" ||
      part.type === "contextCompaction"
    ) {
      return renderOperationPart(
        part,
        `${index}-operation-${part.type}-${partIndex}`,
      );
    }
    return null;
  };

  return (
    <div className="bubble-assistant-body">
      {executionPanel.visible ? (
        <ExecutionLog
          key={executionPanelLogKey}
          logKey={executionPanelLogKey}
          title={executionPanel.title}
          active={executionPanel.active}
          autoOpen={executionPanel.autoOpen}
          startedAt={message.turnStartedAt}
          durationMs={message.durationMs}
          hasError={workLogHasError(executionLogParts)}
        >
          {executionLogItems.map(renderExecutionLogItem)}
        </ExecutionLog>
      ) : null}

      {!message.isError && !showPlaceholder &&
        answerParts.map((part, partIndex) => {
          if (part.type !== "text") return null;
          const structuredQuestions = isStreaming
            ? null
            : parseStructuredQuestions(part.md);
          return structuredQuestions ? (
            <StructuredQuestion
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
      {message.isError || message.error ? (
        <ErrorReportNotice
          message={message.isError
            ? getErrorNoticeMessage(message)
            : `生成中断：${message.error}`}
          report={message.errorReport}
          onSubmitErrorReport={onSubmitErrorReport}
        />
      ) : null}
      {message.termination ? (
        <div className="bubble-content bubble-content--termination">
          {message.termination}
        </div>
      ) : null}
      {(message.toolApprovals || []).map((approval) => (
        <ToolApproval
          key={approval.approvalId}
          approval={approval}
          onResolve={onResolveToolApproval}
        />
      ))}
    </div>
  );
}

export default React.memo(AssistantOutputInner);
