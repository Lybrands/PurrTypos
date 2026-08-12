import React from "react";
import type { AiErrorReport } from "../../types";
import { recordAiDebugErrorReportStatus } from "../AiDevInspector/store";

export interface ErrorReportNoticeProps {
  message: string;
  report?: AiErrorReport;
  onSubmitErrorReport?: (
    reportId: string,
  ) => Promise<{ success: boolean; error?: string }>;
}

export default function ErrorReportNotice({
  message,
  report,
  onSubmitErrorReport,
}: ErrorReportNoticeProps) {
  const [status, setStatus] = React.useState(report?.status);
  const [busy, setBusy] = React.useState(false);
  const [feedback, setFeedback] = React.useState("");

  React.useEffect(() => {
    setStatus(report?.status);
    setFeedback("");
  }, [report?.id, report?.status]);

  const submit = async () => {
    if (!report || !onSubmitErrorReport || busy || status !== "captured") return;
    setBusy(true);
    setFeedback("");
    try {
      const result = await onSubmitErrorReport(report.id);
      if (!result.success) {
        setFeedback(result.error || "上报失败");
        return;
      }
      setStatus("submitted");
      recordAiDebugErrorReportStatus(report.streamId, "submitted");
      setFeedback("已加入待排查");
    } catch {
      setFeedback("上报失败");
    } finally {
      setBusy(false);
    }
  };

  const copyId = async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report.id);
      setFeedback("已复制编号");
    } catch {
      setFeedback("复制失败");
    }
  };

  return (
    <div className="bubble-content bubble-content--error">
      <span>{message}</span>
      {report ? (
        <div className="bubble-error-report">
          <code className="bubble-error-report-id">错误报告 {report.id}</code>
          <button type="button" onClick={copyId}>复制编号</button>
          {status === "captured" && onSubmitErrorReport ? (
            <button type="button" onClick={submit} disabled={busy}>
              {busy ? "处理中…" : "加入待排查"}
            </button>
          ) : (
            <span>{status === "resolved" ? "已解决" : "待排查"}</span>
          )}
          {feedback ? <small>{feedback}</small> : null}
        </div>
      ) : null}
    </div>
  );
}
