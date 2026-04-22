import type { Outline, EntityId } from "../../../types";
import type { ChatMessage } from "./chat.types";
import { isWritingExpertPipeline } from "./chat.types";

/** 写作专家模式：助手轮仅有 tool 气泡、正文为空时，拼出可供模型阅读的摘要，避免主会话上下文断裂 */
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
 * 把"关联章节/大纲"渲染成强约束块：直接列出 chapterId/outlineId，
 * 并在末尾给出"必须立即调用 batchGetChapterContents / queryOutline"的硬指令。
 * 如果只给标题（旧实现），LLM 经常忽略关联或匹配错 id，故统一改为 id-first。
 */
export function buildAssociationBlocks(
  associatedChapterIds: EntityId[],
  associatedOutlineIds: EntityId[],
  writingChapters: { id: EntityId; title: string }[],
  availableOutlines: Outline[],
): string[] {
  const blocks: string[] = [];
  if (associatedChapterIds.length > 0) {
    const pairs = associatedChapterIds
      .map((id) => {
        const sid = String(id).trim();
        if (!sid) return null;
        const c = writingChapters.find((w) => String(w.id) === sid);
        return { id: sid, title: c?.title || "（未匹配章节标题）" };
      })
      .filter((x): x is { id: string; title: string } => x != null);
    if (pairs.length > 0) {
      const idList = `[${pairs.map((p) => `"${p.id}"`).join(",")}]`;
      const lines = [
        "【用户在本轮已关联以下写作章节 — 回复前必须读取其正文，不读视为忽略用户上下文，回答无效】",
        ...pairs.map((p) => `- chapterId=${p.id} 《${p.title}》`),
        `→ 立即调用 batchGetChapterContents，参数 chapterIds=${idList}，一次性把上述全部章节正文读入；若已在前序工具结果中读过可复用，否则不得跳过。`,
      ];
      blocks.push(lines.join("\n"));
    }
  }
  if (associatedOutlineIds.length > 0) {
    const pairs = associatedOutlineIds
      .map((oid) => {
        const sid = String(oid).trim();
        if (!sid) return null;
        const o = availableOutlines.find((x) => String(x.id) === sid);
        return { id: sid, title: o?.title || "（未匹配大纲标题）" };
      })
      .filter((x): x is { id: string; title: string } => x != null);
    if (pairs.length > 0) {
      const idList = `[${pairs.map((p) => `"${p.id}"`).join(",")}]`;
      const lines = [
        "【用户在本轮已关联以下大纲 — 回复前必须读取其完整内容，不读视为忽略用户上下文，回答无效】",
        ...pairs.map((p) => `- outlineId=${p.id} 《${p.title}》`),
        `→ 立即调用 queryOutline，参数 outlineIds=${idList}、includeText=true、includeChapters=false，一次性把上述全部大纲读入；若已在前序工具结果中读过可复用，否则不得跳过。`,
      ];
      blocks.push(lines.join("\n"));
    }
  }
  return blocks;
}

/**
 * 将界面 ChatMessage 转成发给后端的 history 项；
 * 错误轮、system 轮和空内容都过滤掉，并对写作专家模式的"仅工具气泡"轮做摘要回填。
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
