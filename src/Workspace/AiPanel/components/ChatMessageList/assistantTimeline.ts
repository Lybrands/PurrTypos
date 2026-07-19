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

export type TimelineCommentaryPart = {
  type: "commentary";
  md: string;
};

export type TimelineTaskPlanPart = {
  type: "taskPlan";
  plan: AiTaskPlan;
};

export type TimelineDelegationsPart = {
  type: "delegations";
  items: NonNullable<ChatMessage["delegations"]>;
};

export type TimelineContextCompactionPart = {
  type: "contextCompaction";
  state: NonNullable<ChatMessage["contextCompaction"]>;
};

export type AssistantTimelinePart =
  | TimelineThinkingPart
  | TimelineToolsPart
  | TimelineTextPart
  | TimelineCommentaryPart
  | TimelineTaskPlanPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart;

export type TimelineStepPart = TimelineThinkingPart | TimelineToolsPart;

export type WorkLogTimelineItem =
  | AssistantTimelinePart
  | {
      type: "stepGroup";
      groupKey: string;
      parts: TimelineStepPart[];
    };

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
}

function visibleToolSegment(seg: ToolCallSegment): boolean {
  return seg.labels.length > 0 || Boolean(seg.textBefore?.trim());
}

/** 将连续的思考与工具调用聚合为工作日志中的二级步骤组。 */
export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  messageIndex: number,
): WorkLogTimelineItem[] {
  const items: WorkLogTimelineItem[] = [];
  let stepParts: TimelineStepPart[] = [];
  let groupStartIndex = 0;

  const flushSteps = () => {
    if (stepParts.length === 0) return;
    if (stepParts.length === 1) {
      items.push(stepParts[0]);
    } else {
      items.push({
        type: "stepGroup",
        groupKey: `${messageIndex}-work-steps-${groupStartIndex}`,
        parts: stepParts,
      });
    }
    stepParts = [];
  };

  parts.forEach((part, partIndex) => {
    if (
      part.type === "tools" &&
      part.segment.labels.every(
        (_label, labelIndex) => part.segment.cachedFlags?.[labelIndex],
      )
    ) {
      return;
    }
    if (part.type === "thinking" || part.type === "tools") {
      if (stepParts.length === 0) groupStartIndex = partIndex;
      stepParts.push(part);
      return;
    }
    flushSteps();
    items.push(part);
  });
  flushSteps();

  return items;
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

  if (message.contextCompaction) {
    parts.push({
      type: "contextCompaction",
      state: message.contextCompaction,
    });
  }
  if (message.taskPlan) {
    parts.push({ type: "taskPlan", plan: message.taskPlan });
  }
  if (message.delegations?.length) {
    parts.push({ type: "delegations", items: message.delegations });
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
      parts.push({ type: "commentary", md: textBefore });
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

  const assistantMarkdown = getAssistantRenderableMarkdown(message);
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
