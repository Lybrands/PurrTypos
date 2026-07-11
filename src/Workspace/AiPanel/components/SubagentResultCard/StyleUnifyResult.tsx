import React from "react";
import { Button, Typography } from "antd";
import { formatStyleUnifyPayload } from "./formatters";

interface StyleUnifyResultProps {
  payload: unknown;
  applyChapter: (content: string) => Promise<void>;
}

export default function StyleUnifyResult({
  payload,
  applyChapter,
}: StyleUnifyResultProps) {
  const { body, summary, anchors } = formatStyleUnifyPayload(payload);
  return (
    <div className="subagent-result-card">
      <div className="subagent-result-card__title">风格统一</div>
      {anchors ? (
        <Typography.Paragraph type="secondary" className="subagent-result-summary">
          <strong>文风锚点：</strong>
          {anchors.slice(0, 800)}
          {anchors.length > 800 ? "…" : ""}
        </Typography.Paragraph>
      ) : null}
      {summary ? (
        <Typography.Paragraph type="secondary">{summary}</Typography.Paragraph>
      ) : null}
      {body ? (
        <pre className="subagent-result-pre subagent-result-pre--body">{body}</pre>
      ) : (
        <Typography.Text type="secondary">未解析到正文 content。</Typography.Text>
      )}
      <Button
        type="primary"
        size="small"
        disabled={!body}
        onClick={() => void applyChapter(body)}
      >
        生成 diff 应用到当前章
      </Button>
    </div>
  );
}
