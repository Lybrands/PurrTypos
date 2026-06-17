import React from "react";
import { Button } from "antd";
import {
  EditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  ClockCircleOutlined,
  DownOutlined,
  RightOutlined,
} from "@ant-design/icons";
import "./index.scss";

export type ToolCallLabelOutcome = "ok" | "context_error";

export interface ToolCallStatusProps {
  labels: string[];
  labelOutcomes?: ToolCallLabelOutcome[];
  cachedFlags?: boolean[];
  completedToolCount: number;
}

type RowPhase = "done" | "running" | "pending";

type ToolRow = {
  idx: number;
  label: string;
  outcome: ToolCallLabelOutcome;
  phase: RowPhase;
};

function rowPhase(
  idx: number,
  completedToolCount: number,
  labelCount: number,
): RowPhase {
  if (idx < completedToolCount) return "done";
  if (idx === completedToolCount && completedToolCount < labelCount) {
    return "running";
  }
  return "pending";
}

function getRunningText(label: string): string {
  return `正在执行 ${label}`;
}

function getDoneText(label: string): string {
  return `已完成 ${label}`;
}

function getPendingText(label: string): string {
  return `待执行 ${label}`;
}

function renderToolRow(row: ToolRow) {
  const { idx, label, outcome, phase } = row;
  if (outcome === "context_error") {
    return (
      <div
        key={idx}
        className="bubble-tool-call-line bubble-tool-call-line--error"
      >
        <CloseCircleOutlined className="bubble-tool-call-icon" />
        <span>
          失败：{label}
          — 信息有误（当前书籍章节目录中无对应章节或工具参数无效）
        </span>
      </div>
    );
  }

  const isEditing = label.startsWith("编辑");
  const statusText =
    phase === "done"
      ? getDoneText(label)
      : phase === "running"
        ? getRunningText(label)
        : getPendingText(label);
  const flicker = phase === "running";

  return (
    <div
      key={idx}
      className={`bubble-tool-call-line ${flicker ? "a-flicker-opacity" : ""} bubble-tool-call-line--${phase}`}
    >
      {phase === "done" ? (
        <CheckCircleOutlined className="bubble-tool-call-icon" />
      ) : phase === "running" ? (
        isEditing ? (
          <EditOutlined className="bubble-tool-call-icon" />
        ) : (
          <FileSearchOutlined className="bubble-tool-call-icon" />
        )
      ) : (
        <ClockCircleOutlined className="bubble-tool-call-icon bubble-tool-call-icon--pending" />
      )}
      <span>{statusText}</span>
    </div>
  );
}

function getSummaryText(rows: ToolRow[], done: number, total: number): string {
  const noun = rows.length === 1 ? "工具" : `${rows.length} 个工具`;
  const hasError = rows.some((row) => row.outcome === "context_error");
  if (hasError) return rows.length === 1 ? "工具执行异常" : `工具执行异常 · ${rows.length} 项`;
  if (done >= total) return `已完成 ${noun}`;
  if (rows.some((row) => row.phase === "running")) {
    return rows.length === 1 ? "正在执行工具" : `正在执行 ${noun} · ${done}/${total}`;
  }
  return `待执行 ${noun}`;
}

/** 工具调用：逐条展示完成 / 进行中 / 待执行（与思考区卡片样式区分） */
export default function ToolCallStatus({
  labels,
  labelOutcomes,
  cachedFlags,
  completedToolCount,
}: ToolCallStatusProps) {
  const [expanded, setExpanded] = React.useState(false);
  const n = labels.length;
  const done = Math.min(Math.max(0, completedToolCount), n);
  const rows = labels
    .map<ToolRow | null>((label, idx) => {
      if (cachedFlags?.[idx]) return null;
      return {
        idx,
        label,
        outcome: labelOutcomes?.[idx] ?? "ok",
        phase: rowPhase(idx, done, n),
      };
    })
    .filter((row): row is ToolRow => Boolean(row));

  if (rows.length === 0) return null;

  return (
    <div className="bubble-tool-calls">
      <Button
        type="text"
        size="small"
        className="bubble-tool-call-summary"
        icon={
          expanded ? (
            <DownOutlined className="bubble-tool-call-icon" />
          ) : (
            <RightOutlined className="bubble-tool-call-icon" />
          )
        }
        onClick={() => setExpanded((v) => !v)}
      >
        {getSummaryText(rows, done, n)}
      </Button>
      {expanded ? (
        <div className="bubble-tool-call-details">
          {rows.map((row) => renderToolRow(row))}
        </div>
      ) : null}
    </div>
  );
}
