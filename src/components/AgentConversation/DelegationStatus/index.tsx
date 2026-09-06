import React from "react";
import type { AiAgentDelegation } from "../../../types";
import type {
  AgentConversationMessage,
  AiSubAgentActivity,
} from "../../../agent-runtime/contracts";
import Markdown from "../../Markdown";
import { ExecutionLogStepGroup } from "../ExecutionLog";
import ToolCallStatus from "../ToolCallStatus";
import { buildAssistantTimeline } from "../AssistantOutput/timeline";
import {
  buildSubAgentTimelineItems,
  presentableStructuredResponse,
} from "./presentation";

const STATUS_LABELS: Record<AiAgentDelegation["status"], string> = {
  queued: "等待中",
  claimed: "已认领",
  running: "执行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

function presentableChildMessage(
  message: AgentConversationMessage,
): AgentConversationMessage {
  return {
    ...message,
    content: presentableStructuredResponse(message.content),
  };
}

export default function DelegationStatus({
  items,
  activities = [],
}: {
  items: AiAgentDelegation[];
  activities?: AiSubAgentActivity[];
}) {
  const completed = items.filter((item) => item.status === "done").length;
  return (
    <div className="work-log__subagents">
      <div className="work-log__subagents-header">
        <span>子 Agent</span>
        <span className="work-log__plan-count">
          {completed}/{items.length}
        </span>
      </div>
      <div className="work-log__subagent-list">
        {items.map((item, itemIndex) => {
          const active = ["queued", "claimed", "running"].includes(item.status);
          const activity = activities.find(
            (candidate) => candidate.delegationId === item.delegationId,
          );
          const activityMessage = activity?.message
            ? presentableChildMessage(activity.message)
            : undefined;
          const rawTimeline = activityMessage
            ? buildAssistantTimeline(activityMessage, {
                messageIndex: itemIndex,
                isStreaming: active,
                isLastAssistant: true,
                loading: active,
                allowStreamingText: true,
              })
            : [];
          const timeline = buildSubAgentTimelineItems(
            rawTimeline,
            `delegation-${item.delegationId}`,
          );
          const renderToolPart = (
            part: Extract<(typeof rawTimeline)[number], { type: "tools" }>,
            key: React.Key,
          ) => (
            <ToolCallStatus
              key={key}
              labels={part.segment.labels}
              labelOutcomes={part.segment.labelOutcomes}
              cachedFlags={part.segment.cachedFlags}
              completedToolCount={part.isLive
                ? part.segment.completedToolCount ?? 0
                : part.segment.labels.length}
              itemDurationsMs={part.segment.itemDurationsMs}
              activeItemStartedAt={part.isLive
                ? part.segment.activeItemStartedAt
                : undefined}
            />
          );
          return (
            <div
              className={`work-log__subagent work-log__subagent--${item.status}`}
              key={item.delegationId}
            >
              <span className="work-log__subagent-indicator" aria-hidden="true" />
              <div className="work-log__subagent-content">
                <div className="work-log__subagent-line">
                  <span className="work-log__subagent-role">
                    {item.agentTitle || item.agentName}
                  </span>
                  <span className="work-log__subagent-status">
                    {STATUS_LABELS[item.status]}
                    {active && <span className="a-blink-dots">...</span>}
                  </span>
                </div>
                {(item.unitId || item.attempt) && (
                  <div className="work-log__subagent-objective">
                    {item.unitId ? `任务单元 ${item.unitId}` : ""}
                    {item.attempt && item.attempt > 1
                      ? `${item.unitId ? " · " : ""}第 ${item.attempt} 次尝试`
                      : ""}
                  </div>
                )}
                {item.objective && (
                  <div className="work-log__subagent-objective">{item.objective}</div>
                )}
                {timeline.map((part, partIndex) => {
                  if (part.type === "tools") {
                    return renderToolPart(
                      part,
                      `${item.delegationId}-tools-${part.segmentIndex}`,
                    );
                  }
                  if (part.type === "stepGroup") {
                    const toolParts = part.parts.filter(
                      (candidate) => candidate.type === "tools",
                    );
                    const activeGroup = toolParts.some((candidate) => candidate.isLive);
                    const completedDurationMs = toolParts.reduce(
                      (total, candidate) => total + (
                        candidate.segment.itemDurationsMs?.reduce<number>(
                          (subtotal, duration) => subtotal + Math.max(0, duration ?? 0),
                          0,
                        ) ?? (candidate.isLive ? 0 : candidate.segment.durationMs ?? 0)
                      ),
                      0,
                    );
                    return (
                      <ExecutionLogStepGroup
                        key={part.groupKey}
                        groupKey={part.groupKey}
                        stepCount={toolParts.reduce(
                          (total, candidate) => total + candidate.segment.labels.filter(
                            (_label, labelIndex) =>
                              !candidate.segment.cachedFlags?.[labelIndex],
                          ).length,
                          0,
                        )}
                        completedDurationMs={completedDurationMs}
                        activeStartedAt={toolParts
                          .filter((candidate) => candidate.isLive)
                          .map((candidate) =>
                            candidate.segment.activeItemStartedAt
                              ?? candidate.segment.startedAt
                          )
                          .find((startedAt) => startedAt != null)}
                        active={activeGroup}
                        hasError={toolParts.some((candidate) =>
                          candidate.segment.labelOutcomes?.some(
                            (outcome, labelIndex) =>
                              outcome === "context_error"
                              && !candidate.segment.cachedFlags?.[labelIndex],
                          )
                        )}
                      >
                        {toolParts.map((candidate) => renderToolPart(
                          candidate,
                          `${part.groupKey}-tools-${candidate.segmentIndex}`,
                        ))}
                      </ExecutionLogStepGroup>
                    );
                  }
                  if (part.type === "commentary" || part.type === "text") {
                    return (
                      <div
                        key={`${item.delegationId}-${part.type}-${partIndex}`}
                        className="work-log__subagent-response"
                      >
                        <Markdown>{part.md}</Markdown>
                      </div>
                    );
                  }
                  return null;
                })}
                {item.status === "done" && item.resultSummary && (
                  !timeline.some((part) => part.type === "text") ? (
                    <div className="work-log__subagent-result">{item.resultSummary}</div>
                  ) : null
                )}
                {item.status === "failed" && item.error && (
                  <div className="work-log__subagent-error">{item.error}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
