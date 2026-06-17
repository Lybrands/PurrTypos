import { getAssistantRenderableMarkdown } from "../../rendering";
import type { AiTaskPlan, ChatMessage, ToolCallSegment } from "../../hooks/chat.types";

export type TimelineThinkingPart = {
  type: "thinking";
  text: string;
  durationMs?: number;
  startedAt?: number;
  regionKey: string;
};

export type TimelineToolsPart = {
  type: "tools";
  segment: ToolCallSegment;
  segmentIndex: number;
  /** 本段工具是否仍在执行（仅最后一条助手消息流式时有效） */
  isLive?: boolean;
};

export type TimelineTextPart = {
  type: "text";
  md: string;
};

export type TimelineDigestPart = {
  type: "digest";
  md: string;
};

export type TimelineTaskPlanPart = {
  type: "taskPlan";
  plan: AiTaskPlan;
};

export type AssistantTimelinePart =
  | TimelineThinkingPart
  | TimelineToolsPart
  | TimelineTextPart
  | TimelineDigestPart
  | TimelineTaskPlanPart;

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
}

function visibleToolSegment(seg: ToolCallSegment): boolean {
  return seg.labels.length > 0 || Boolean(seg.textBefore?.trim());
}

/** 将助手消息拆成有序渲染片段：思考 / 工具 / 正文 */
export function buildAssistantTimeline(
  message: ChatMessage,
  opts: BuildAssistantTimelineOptions,
): AssistantTimelinePart[] {
  const parts: AssistantTimelinePart[] = [];
  const segments = message.toolCallSegments ?? [];
  const blocks = message.thinkingBlocks ?? [];
  const durations = message.thinkingDurationsMs ?? [];
  const { messageIndex, isStreaming, isLastAssistant, loading } = opts;

  const digest = (message.subagentPipelineDigest || "").trim();
  if (digest) {
    parts.push({ type: "digest", md: digest });
  }

  if (message.taskPlan) {
    parts.push({ type: "taskPlan", plan: message.taskPlan });
  }

  for (let i = 0; i < segments.length; i++) {
    const block = blocks[i]?.trim();
    if (block) {
      parts.push({
        type: "thinking",
        text: block,
        durationMs: durations[i],
        regionKey: `${messageIndex}-seg-${i}`,
      });
    }

    const seg = segments[i];
    const textBefore = seg.textBefore?.trim();
    if (textBefore) {
      parts.push({ type: "text", md: textBefore });
    }

    if (visibleToolSegment(seg) && seg.labels.length > 0) {
      const isToolLive =
        Boolean(isLastAssistant) &&
        Boolean(loading) &&
        i === segments.length - 1 &&
        Boolean(message.toolCalling) &&
        seg.labels.length > 0;
      parts.push({
        type: "tools",
        segment: seg,
        segmentIndex: i,
        isLive: isToolLive,
      });
    }
  }

  const tailBlockIndex = segments.length;
  const tailBlock = blocks[tailBlockIndex]?.trim();
  if (tailBlock) {
    parts.push({
      type: "thinking",
      text: tailBlock,
      durationMs: durations[tailBlockIndex],
      regionKey: `${messageIndex}-tail-${tailBlockIndex}`,
    });
  }

  const assistantMarkdownRaw = getAssistantRenderableMarkdown(message);
  const assistantMarkdown = message.subagentResult ? "" : assistantMarkdownRaw;
  if (assistantMarkdown.trim()) {
    parts.push({ type: "text", md: assistantMarkdown });
  }

  if (isStreaming && (message.thinking ?? "").trim()) {
    const streamBlockIndex = blocks.length;
    parts.push({
      type: "thinking",
      text: message.thinking!.trim(),
      startedAt: message.thinkingStartedAt,
      regionKey: `${messageIndex}-stream-${streamBlockIndex}`,
    });
  }

  return parts;
}
