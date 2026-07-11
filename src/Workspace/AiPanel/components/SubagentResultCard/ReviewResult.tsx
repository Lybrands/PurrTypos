import React from "react";
import { Button, Collapse, Typography, message as antdMessage } from "antd";
import {
  formatReviewIssues,
  formatReviewIssuesForClipboard,
} from "./formatters";

interface ReviewResultProps {
  payload: unknown;
}

export default function ReviewResult({ payload }: ReviewResultProps) {
  const issues = formatReviewIssues(payload);
  if (issues.length === 0) {
    return (
      <div className="subagent-result-card">
        <Typography.Text type="secondary">未发现需列出的审校问题。</Typography.Text>
      </div>
    );
  }

  return (
    <div className="subagent-result-card">
      <div className="subagent-result-card__title">审校结果（{issues.length} 条）</div>
      <Collapse
        size="small"
        items={issues.map((issue, index) => ({
          key: String(index),
          label: `${issue.severity} · ${issue.issueType} · ${issue.span.slice(0, 40)}`,
          children: (
            <div className="subagent-result-issue-body">
              <div>
                <strong>建议：</strong>
                {issue.suggestion}
              </div>
              {issue.hasContext ? (
                <div className="subagent-result-muted">
                  <strong>上下文：</strong>
                  {issue.context}
                </div>
              ) : null}
            </div>
          ),
        }))}
      />
      <Button
        size="small"
        className="subagent-result-copy"
        onClick={() => {
          void navigator.clipboard.writeText(formatReviewIssuesForClipboard(issues));
          antdMessage.success("已复制审校列表");
        }}
      >
        复制审校列表
      </Button>
    </div>
  );
}
