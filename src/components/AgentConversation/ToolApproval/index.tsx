import React from "react";
import { PurrButton, PurrCard, PurrSpace, PurrTag } from '@/purr-components';
import { CheckIcon, CloseIcon } from '@/purr-components';
import type { ToolApprovalRequest } from "../../../types";
import { submitToolApprovalDecision } from "./submission";
import "./ToolApprovalCard.scss";

type ApprovalState = "pending" | "submitting" | "approved" | "rejected" | "error";

export interface ToolApprovalProps {
  approval: ToolApprovalRequest;
  onResolve: (
    approvalId: string,
    approved: boolean,
  ) => Promise<{ success: boolean; error?: string }>;
}

export default function ToolApproval({ approval, onResolve }: ToolApprovalProps) {
  const serverState: ApprovalState =
    approval.status === "approved" || approval.status === "rejected"
      ? approval.status
      : approval.status && approval.status !== "pending"
        ? "error"
        : "pending";
  const [state, setState] = React.useState<ApprovalState>(serverState);
  const [error, setError] = React.useState("");
  const [canRetry, setCanRetry] = React.useState(false);
  const submissionGuard = React.useRef({ pending: false });

  React.useEffect(() => {
    if (serverState === "pending") return;
    setState(serverState);
    setCanRetry(false);
    if (serverState === "error") {
      setError(
        approval.status === "timed_out"
          ? "确认请求已超时，操作未执行。"
          : "确认请求已取消，操作未执行。",
      );
    }
  }, [approval.status, serverState]);

  const decide = async (approved: boolean) => {
    if (state !== "pending" && !(state === "error" && canRetry)) return;
    setState("submitting");
    setError("");
    setCanRetry(false);
    const result = await submitToolApprovalDecision(
      submissionGuard.current,
      onResolve,
      approval.approvalId,
      approved,
    );
    if (!result) return;
    if (result.state === "error") {
      setError(result.error);
      setCanRetry(true);
    }
    setState(result.state);
  };

  const riskLabel = approval.riskLevel === "destructive" ? "高风险" : "写入操作";

  return (
    <PurrCard size="small" className="tool-approval-card">
      <div className="tool-approval-card__header">
        <strong>{approval.title}</strong>
        <PurrTag color={approval.riskLevel === "destructive" ? "red" : "orange"}>
          {riskLabel}
        </PurrTag>
      </div>
      <div className="tool-approval-card__hint">
        Agent 正在请求执行此操作。请核对参数后决定；未批准前后端不会执行。
      </div>
      <pre className="tool-approval-card__summary">{approval.summary}</pre>
      {state === "approved" ? (
        <div className="tool-approval-card__resolved">已批准，正在继续执行。</div>
      ) : state === "rejected" ? (
        <div className="tool-approval-card__resolved">已拒绝，该操作不会执行。</div>
      ) : (
        <>
          {state === "error" ? (
            <div className="tool-approval-card__error">{error}</div>
          ) : null}
          {state !== "error" || canRetry ? (
            <PurrSpace size="small" className="tool-approval-card__actions">
              <PurrButton
                size="small"
                type="primary"
                danger={approval.riskLevel === "destructive"}
                icon={<CheckIcon />}
                loading={state === "submitting"}
                onClick={() => void decide(true)}
              >
                批准执行
              </PurrButton>
              <PurrButton
                size="small"
                icon={<CloseIcon />}
                disabled={state === "submitting"}
                onClick={() => void decide(false)}
              >
                拒绝
              </PurrButton>
            </PurrSpace>
          ) : null}
        </>
      )}
    </PurrCard>
  );
}
