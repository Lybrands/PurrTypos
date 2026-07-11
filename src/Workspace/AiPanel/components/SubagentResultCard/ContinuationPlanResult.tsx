import React from "react";
import { Button, Typography, message as antdMessage } from "antd";
import { formatContinuationPlanText } from "./formatters";

interface ContinuationPlanResultProps {
  payload: unknown;
}

export default function ContinuationPlanResult({ payload }: ContinuationPlanResultProps) {
  const text = formatContinuationPlanText(payload);
  return (
    <div className="subagent-result-card">
      <div className="subagent-result-card__title">续写规划</div>
      {text ? (
        <pre className="subagent-result-pre subagent-result-pre--body subagent-plan-text">
          {text}
        </pre>
      ) : (
        <Typography.Text type="secondary">未解析到结构化蓝图。</Typography.Text>
      )}
      <Button
        size="small"
        onClick={() => {
          void navigator.clipboard.writeText(text);
          antdMessage.success("已复制规划文案");
        }}
      >
        复制
      </Button>
    </div>
  );
}
