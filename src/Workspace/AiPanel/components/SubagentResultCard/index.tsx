import React from "react";
import { Button, Collapse, Typography, message as antdMessage } from "antd";
import type { EntityId } from "../../../../types";
import type { WritingSubagentRole } from "../../pipelineStages";
import "./index.scss";

export interface SubagentResultCardProps {
  role: WritingSubagentRole;
  payload: unknown;
  chapterId: EntityId | null | undefined;
}

export default function SubagentResultCard({
  role,
  payload,
  chapterId,
}: SubagentResultCardProps) {
  // 自 v3.1：所有"AI 改正文"入口统一走 diff 提议，
  // 由 DiffProvider 监听 ai-propose-chapter-diff 事件并启动 diff 会话。
  const applyChapter = React.useCallback(
    async (content: string) => {
      if (!chapterId) {
        antdMessage.warning("未选择章节，无法写入");
        return;
      }
      if (!content?.trim()) {
        antdMessage.warning("无正文可写入");
        return;
      }
      try {
        const res = await window.electronAPI.getArticle({ chapterId });
        const beforeText = res?.success && res.data ? res.data.content || "" : "";
        window.dispatchEvent(
          new CustomEvent("ai-propose-chapter-diff", {
            detail: {
              chapterId,
              beforeText,
              proposedText: content,
              source: `subagent_${role}`,
            },
          }),
        );
        antdMessage.success("已生成 diff，请到写作区接受/拒绝");
      } catch (e) {
        console.error("[SubagentResultCard] propose diff failed", e);
        antdMessage.error("生成 diff 失败");
      }
    },
    [chapterId, role],
  );

  if (role === "review") {
    const issuesRaw = (payload as { issues?: unknown[] })?.issues ?? [];
    const issues = Array.isArray(issuesRaw)
      ? (issuesRaw as Record<string, unknown>[])
      : [];
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
          items={issues.map((it, i) => ({
            key: String(i),
            label: `${String(it.severity ?? "")} · ${String(it.issueType ?? "")} · ${String(it.span ?? "").slice(0, 40)}`,
            children: (
              <div className="subagent-result-issue-body">
                <div>
                  <strong>建议：</strong>
                  {String(it.suggestion ?? "")}
                </div>
                {it.context ? (
                  <div className="subagent-result-muted">
                    <strong>上下文：</strong>
                    {String(it.context)}
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
            const text = issues
              .map(
                (it) =>
                  `- [${it.severity}] ${it.span}\n  ${it.suggestion}`,
              )
              .join("\n");
            void navigator.clipboard.writeText(text);
            antdMessage.success("已复制审校列表");
          }}
        >
          复制审校列表
        </Button>
      </div>
    );
  }

  if (role === "continuation_plan") {
    const bp = (payload as { blueprint?: Record<string, unknown> })?.blueprint;
    const json = JSON.stringify(bp ?? payload, null, 2);
    return (
      <div className="subagent-result-card">
        <div className="subagent-result-card__title">续写规划（Blueprint）</div>
        <pre className="subagent-result-pre">{json}</pre>
        <Button
          size="small"
          onClick={() => {
            void navigator.clipboard.writeText(json);
            antdMessage.success("已复制");
          }}
        >
          复制 JSON
        </Button>
      </div>
    );
  }

  if (role === "polish") {
    const p = payload as { finalText?: string; changeSummary?: string };
    const body = (p.finalText ?? "").trim();
    const summary = (p.changeSummary ?? "").trim();
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
          <Typography.Text type="secondary">未解析到 finalText。</Typography.Text>
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

  // style_unify
  const s = payload as {
    styleAnchors?: string;
    content?: string;
    changeSummary?: string;
  };
  const body = (s.content ?? "").trim();
  const summary = (s.changeSummary ?? "").trim();
  const anchors = (s.styleAnchors ?? "").trim();
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
