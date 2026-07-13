import React from "react";
import { Button, Card, Space, Tag } from "antd";
import { CheckOutlined, CloseOutlined } from "@ant-design/icons";
import type { ToolApprovalRequest } from "../../../../types";
import "./ToolApprovalCard.scss";

type ApprovalState = "pending" | "submitting" | "approved" | "rejected" | "error";

interface ToolApprovalCardProps {
  approval: ToolApprovalRequest;
}

export default function ToolApprovalCard({ approval }: ToolApprovalCardProps) {
  const [state, setState] = React.useState<ApprovalState>("pending");
  const [error, setError] = React.useState("");

  const decide = async (approved: boolean) => {
    if (state !== "pending") return;
    setState("submitting");
    const result = await window.electronAPI.resolveAiToolApproval({
      approvalId: approval.approvalId,
      approved,
    });
    if (result.success) {
      setState(approved ? "approved" : "rejected");
      return;
    }
    setError(result.error || "确认请求已过期或处理失败。");
    setState("error");
  };

  const riskLabel = approval.riskLevel === "destructive" ? "高风险" : "写入操作";

  return (
    <Card size="small" className="tool-approval-card">
      <div className="tool-approval-card__header">
        <strong>{approval.title}</strong>
        <Tag color={approval.riskLevel === "destructive" ? "red" : "orange"}>
          {riskLabel}
        </Tag>
      </div>
      <div className="tool-approval-card__hint">
        Agent 正在请求执行此操作。请核对参数后决定；未批准前后端不会执行。
      </div>
      <pre className="tool-approval-card__summary">{approval.summary}</pre>
      {state === "approved" ? (
        <div className="tool-approval-card__resolved">已批准，正在继续执行。</div>
      ) : state === "rejected" ? (
        <div className="tool-approval-card__resolved">已拒绝，该操作不会执行。</div>
      ) : state === "error" ? (
        <div className="tool-approval-card__error">{error}</div>
      ) : (
        <Space size="small" className="tool-approval-card__actions">
          <Button
            size="small"
            type="primary"
            danger={approval.riskLevel === "destructive"}
            icon={<CheckOutlined />}
            loading={state === "submitting"}
            onClick={() => void decide(true)}
          >
            批准执行
          </Button>
          <Button
            size="small"
            icon={<CloseOutlined />}
            disabled={state === "submitting"}
            onClick={() => void decide(false)}
          >
            拒绝
          </Button>
        </Space>
      )}
    </Card>
  );
}
