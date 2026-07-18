import React from "react";
import type { AiAgentDelegation } from "../../../../types";

const STATUS_LABELS: Record<AiAgentDelegation["status"], string> = {
  queued: "等待中",
  claimed: "已认领",
  running: "执行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export default function SubAgentStatusList({
  items,
}: {
  items: AiAgentDelegation[];
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
        {items.map((item) => {
          const active = ["queued", "claimed", "running"].includes(item.status);
          return (
            <div
              className={`work-log__subagent work-log__subagent--${item.status}`}
              key={item.delegationId}
            >
              <span className="work-log__subagent-indicator" aria-hidden="true" />
              <div className="work-log__subagent-content">
                <div className="work-log__subagent-line">
                  <span className="work-log__subagent-role">
                    {item.agentTitle || item.agentRole}
                  </span>
                  <span className="work-log__subagent-status">
                    {STATUS_LABELS[item.status]}
                    {active && <span className="a-blink-dots">...</span>}
                  </span>
                </div>
                {item.objective && (
                  <div className="work-log__subagent-objective">{item.objective}</div>
                )}
                {item.status === "done" && item.resultSummary && (
                  <div className="work-log__subagent-result">{item.resultSummary}</div>
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
