import React from "react";
import { Button, Typography, message as antdMessage } from "antd";
import { formatPolishPayload } from "./formatters";

interface PolishResultProps {
  payload: unknown;
  applyChapter: (content: string) => Promise<void>;
}

export default function PolishResult({ payload, applyChapter }: PolishResultProps) {
  const { body, summary, rawDump } = formatPolishPayload(payload);
  return (
    <div className="subagent-result-card">
      <div className="subagent-result-card__title">润色定稿</div>
      {summary ? (
        <Typography.Paragraph type="secondary" className="subagent-result-summary">
          {summary}
        </Typography.Paragraph>
      ) : null}
      {body ? (
        <pre className="subagent-result-pre subagent-result-pre--body">{body}</pre>
      ) : (
        <>
          <Typography.Text type="secondary">
            未解析到 finalText —— 模型未按约定返回 JSON。可复制下方原始输出反馈给我。
          </Typography.Text>
          <pre className="subagent-result-pre subagent-result-pre--body">
            {rawDump || "（空）"}
          </pre>
          <Button
            size="small"
            onClick={() => {
              void navigator.clipboard.writeText(rawDump);
              antdMessage.success("已复制原始输出");
            }}
          >
            复制原始输出
          </Button>
        </>
      )}
      <Button
        type="primary"
        size="small"
        disabled={!body}
        onClick={() => void applyChapter(body)}
        style={{ marginTop: 8 }}
      >
        生成 diff 应用到当前章
      </Button>
    </div>
  );
}
