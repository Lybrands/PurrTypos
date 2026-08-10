import type { ChatMessage } from "./chat.types";

export const EMPTY_RESPONSE_MESSAGE =
  "本轮处理已结束，但模型没有生成可展示的答复。请重试或更换模型。";

/** 助手轮只有工具记录而正文为空时，拼出可供后续模型阅读的摘要。 */
export function synthesizeAssistantTextFromToolSegments(msg: ChatMessage): string {
  const segs = msg.toolCallSegments;
  if (!segs?.length) return "";
  const parts: string[] = [];
  for (const s of segs) {
    const labels = (s.labels || []).filter(Boolean);
    if (labels.length) parts.push(`[已调用工具] ${labels.join("、")}`);
  }
  return parts.join("\n");
}

export function isSynthesizedToolOnlyResponse(msg: ChatMessage): boolean {
  const response = String(msg.content ?? "").trim();
  if (!response || !msg.toolCallSegments?.length) return false;
  return response === synthesizeAssistantTextFromToolSegments(msg).trim();
}

/**
 * 将界面 ChatMessage 转成发给后端的 history 项；
 * 错误轮、system 轮和空内容都过滤掉。
 */
export function buildHistoryConverter(): (
  m: ChatMessage | { role: "user" | "assistant"; content: string },
) => { role: string; content: string } | null {
  return (m) => {
    const cm = m as ChatMessage;
    if (cm.isError || cm.role === "system") return null;
    let text = String(cm.content ?? "").trim();
    if (!text && cm.role === "assistant") {
      text = synthesizeAssistantTextFromToolSegments(cm).trim();
    }
    if (!text) return null;
    return { role: cm.role, content: text };
  };
}
