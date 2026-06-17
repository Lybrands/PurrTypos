import type { ChatMessage } from "./chat.types";
import { isWritingExpertPipeline } from "./chat.types";

/** 历史子专家轮仅有 tool 气泡、正文为空时，拼出可供模型阅读的摘要，避免主会话上下文断裂。 */
export function synthesizeAssistantTextFromToolSegments(msg: ChatMessage): string {
  const segs = msg.toolCallSegments;
  if (!segs?.length) return "";
  const parts: string[] = [];
  for (const s of segs) {
    const tb = (s.textBefore || "").trim();
    if (tb) parts.push(tb);
    const labels = (s.labels || []).filter(Boolean);
    if (labels.length) parts.push(`[已调用工具] ${labels.join("、")}`);
  }
  const after = (msg.contentAfterToolCalls || "").trim();
  if (after) parts.push(after);
  return parts.join("\n");
}

export function summarizeSubagentResult(cm: ChatMessage): string {
  const sr = cm.subagentResult;
  if (!sr) return "";
  if (sr.role === "review") {
    const n = ((sr.payload as { issues?: unknown[] })?.issues ?? []).length;
    return `审校完成：共 ${n} 条问题（见下方卡片）。`;
  }
  if (sr.role === "continuation_plan") {
    return "续写规划已生成（见下方 Blueprint 卡片）。";
  }
  if (sr.role === "polish") {
    return "润色结果已生成（见下方卡片，可「应用到当前章」）。";
  }
  if (sr.role === "style_unify") {
    return "风格统一结果已生成（见下方卡片，可「应用到当前章」）。";
  }
  return "";
}

/**
 * 将界面 ChatMessage 转成发给后端的 history 项；
 * 错误轮、system 轮和空内容都过滤掉，并对历史子专家的"仅工具气泡"轮做摘要回填。
 */
export function buildHistoryConverter(
  agentMode: "legacy" | "subagent" | undefined,
): (m: ChatMessage | { role: "user" | "assistant"; content: string }) => { role: string; content: string } | null {
  return (m) => {
    const cm = m as ChatMessage;
    if (cm.isError || cm.role === "system") return null;
    let text = String(cm.content ?? "").trim();
    const pipeDigest = (cm.subagentPipelineDigest ?? "").trim();
    if (pipeDigest && cm.role === "assistant") {
      text = text ? `${pipeDigest}\n\n${text}` : pipeDigest;
    }
    if (
      !text &&
      isWritingExpertPipeline(agentMode) &&
      cm.role === "assistant"
    ) {
      text = synthesizeAssistantTextFromToolSegments(cm).trim();
    }
    if (!text) return null;
    return { role: cm.role, content: text };
  };
}
