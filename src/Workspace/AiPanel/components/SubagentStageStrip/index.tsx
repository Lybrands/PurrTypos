import React from "react";
import { CheckCircleOutlined, LoadingOutlined } from "@ant-design/icons";
import type { ChatMessage } from "../../hooks";
import "./index.scss";

export interface SubagentStageStripProps {
  message: ChatMessage;
  /** 是否为当前列表最后一条助手消息 */
  isLastAssistant: boolean;
  /** 会话是否仍在请求中 */
  loading: boolean;
}

/** 写作专家模式：气泡底部展示各专家阶段 / 主稿专家进度与完成态 */
export default function SubagentStageStrip({
  message,
  isLastAssistant,
  loading,
}: SubagentStageStripProps) {
  const stages = message.subagentStages ?? [];
  const allStagesDone =
    stages.length > 0 && stages.every((s) => s.status === "done");
  const bridging = message.subagentBridging;
  const mainPresenter = message.subagentMainPresenter;

  let runningName: string | undefined;
  for (let i = stages.length - 1; i >= 0; i--) {
    if (stages[i].status === "running") {
      runningName = stages[i].name;
      break;
    }
  }
  const fallbackName = message.subagentStageName?.trim();
  const working = message.subagentStageWorking;

  if (!runningName && working && fallbackName) {
    runningName = fallbackName;
  }

  if (bridging && isLastAssistant && loading) {
    return (
      <div
        className="subagent-stage-strip subagent-stage-strip--main"
        aria-live="polite"
      >
        <span className="subagent-stage-strip__label">主稿专家</span>
        <span className="subagent-stage-strip__current">
          <LoadingOutlined className="subagent-stage-strip__icon" spin />
          <span className="subagent-stage-strip__name">正在衔接各阶段…</span>
        </span>
      </div>
    );
  }

  if (runningName) {
    return (
      <div className="subagent-stage-strip" aria-live="polite">
        <span className="subagent-stage-strip__label subagent-stage-strip__label--online">
          当前在线
        </span>
        <span className="subagent-stage-strip__current">
          <LoadingOutlined className="subagent-stage-strip__icon" spin />
          <span className="subagent-stage-strip__name">{runningName}</span>
        </span>
      </div>
    );
  }

  if (allStagesDone && isLastAssistant && loading && mainPresenter) {
    return (
      <div
        className="subagent-stage-strip subagent-stage-strip--main"
        aria-live="polite"
      >
        <span className="subagent-stage-strip__label">主稿专家</span>
        <span className="subagent-stage-strip__current">
          <LoadingOutlined className="subagent-stage-strip__icon" spin />
          <span className="subagent-stage-strip__name">正在组织回复…</span>
        </span>
      </div>
    );
  }

  if (allStagesDone && isLastAssistant && !loading) {
    return (
      <div
        className="subagent-stage-strip subagent-stage-strip--done"
        aria-live="polite"
      >
        <CheckCircleOutlined className="subagent-stage-strip__icon subagent-stage-strip__icon--done" />
        <span className="subagent-stage-strip__done-text">写作专家流程已完成</span>
      </div>
    );
  }

  return null;
}
