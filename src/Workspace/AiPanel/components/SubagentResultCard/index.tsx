import React from "react";
import { Button, Collapse, Typography, message as antdMessage } from "antd";
import type { EntityId } from "../../../../types";
import type { WritingSubagentRole } from "../../pipelineStages";
import "./index.scss";

/** 把 keyPoints / requiredMaterials 里的元素拍成可读字符串。
 *  既兼容 ['xxx','yyy'] 这种字符串数组，
 *  也兼容 [{ name, reason }] / [{ title, description }] 这类对象数组，
 *  避免直接 String(obj) 出现 [object Object]。 */
function stringifyItem(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v.trim();
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v !== "object") return "";
  const o = v as Record<string, unknown>;
  // 优先用常见的"标题字段"
  const titleKey = ["name", "title", "label", "key", "subject"].find(
    (k) => typeof o[k] === "string" && (o[k] as string).trim(),
  );
  // 再找一个"描述字段"
  const descKey = [
    "reason",
    "description",
    "desc",
    "detail",
    "content",
    "purpose",
    "note",
  ].find((k) => typeof o[k] === "string" && (o[k] as string).trim());
  if (titleKey && descKey) {
    return `${(o[titleKey] as string).trim()}：${(o[descKey] as string).trim()}`;
  }
  if (titleKey) return (o[titleKey] as string).trim();
  if (descKey) return (o[descKey] as string).trim();
  // 兜底：把所有字符串字段拼起来
  const fallback = Object.entries(o)
    .filter(([, val]) => typeof val === "string" && (val as string).trim())
    .map(([k, val]) => `${k}=${(val as string).trim()}`)
    .join("；");
  return fallback;
}

/** 把 continuation_plan 的 blueprint JSON 拍成纯文本，方便用户复制。 */
function formatContinuationPlanText(bp: Record<string, unknown>): string {
  const lines: string[] = [];
  const goal = String(bp.chapterGoal ?? "").trim();
  const tone = String(bp.tone ?? "").trim();
  const constraints = String(bp.constraints ?? "").trim();
  const matsRaw = bp.requiredMaterials;
  const materials: string[] = Array.isArray(matsRaw)
    ? (matsRaw as unknown[]).map(stringifyItem).filter(Boolean)
    : typeof matsRaw === "string" && matsRaw.trim()
      ? [matsRaw.trim()]
      : [];
  const beatsRaw = bp.beats;
  const beats: Record<string, unknown>[] = Array.isArray(beatsRaw)
    ? (beatsRaw as unknown[]).filter(
        (b): b is Record<string, unknown> => !!b && typeof b === "object",
      )
    : [];

  if (goal) {
    lines.push("【本章目标】");
    lines.push(goal);
    lines.push("");
  }

  if (beats.length > 0) {
    lines.push("【节拍编排】");
    beats.forEach((b, i) => {
      const id = String(b.beatId ?? i + 1);
      const type = String(b.type ?? "").trim();
      const est = String(b.estimatedWords ?? "").trim();
      const head = [`#${id}`, type, est ? `约 ${est} 字` : ""]
        .filter(Boolean)
        .join(" · ");
      lines.push(head);
      const content = String(b.content ?? "").trim();
      if (content) lines.push(`  内容：${content}`);
      const purpose = String(b.purpose ?? "").trim();
      if (purpose) lines.push(`  目的：${purpose}`);
      const kpRaw = b.keyPoints;
      const kps: string[] = Array.isArray(kpRaw)
        ? (kpRaw as unknown[]).map(stringifyItem).filter(Boolean)
        : [];
      if (kps.length > 0) {
        lines.push("  关键点：");
        kps.forEach((k) => lines.push(`    · ${k}`));
      }
      lines.push("");
    });
  }

  if (tone) {
    lines.push("【语气 / 文风】");
    lines.push(tone);
    lines.push("");
  }

  if (constraints) {
    lines.push("【硬性约束】");
    lines.push(constraints);
    lines.push("");
  }

  if (materials.length > 0) {
    lines.push("【需要的素材】");
    materials.forEach((m) => lines.push(`· ${m}`));
    lines.push("");
  }

  return lines.join("\n").trimEnd();
}

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
    const root = (payload ?? {}) as Record<string, unknown>;
    const bp = (root.blueprint && typeof root.blueprint === "object"
      ? (root.blueprint as Record<string, unknown>)
      : root);
    const text = formatContinuationPlanText(bp);
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

  if (role === "polish") {
    const p = payload as { finalText?: string; changeSummary?: string };
    const body = (p.finalText ?? "").trim();
    const summary = (p.changeSummary ?? "").trim();
    const rawDump = !body ? JSON.stringify(payload ?? {}, null, 2) : "";
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
