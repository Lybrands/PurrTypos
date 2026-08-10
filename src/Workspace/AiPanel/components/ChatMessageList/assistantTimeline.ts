import { getAssistantRenderableMarkdown } from "../../rendering";
import type { ChatMessage, ToolCallSegment } from "../../hooks/chat.types";

export type TimelineCommentaryPart = {
  type: "commentary";
  md: string;
  durationMs?: number;
  startedAt?: number;
  regionKey: string;
};

export type TimelineToolsPart = {
  type: "tools";
  segment: ToolCallSegment;
  segmentIndex: number;
  isLive?: boolean;
};

export type TimelineTextPart = { type: "text"; md: string };
export type TimelineDelegationsPart = {
  type: "delegations";
  items: NonNullable<ChatMessage["delegations"]>;
};
export type TimelineContextCompactionPart = {
  type: "contextCompaction";
  state: NonNullable<ChatMessage["contextCompaction"]>;
};

export type AssistantTimelinePart =
  | TimelineCommentaryPart
  | TimelineToolsPart
  | TimelineTextPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart;

export type TimelineStepPart = TimelineCommentaryPart | TimelineToolsPart;

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

export function getAssistantProcessingLabel(message: ChatMessage): string {
  if (
    message.toolApprovals?.some(
      (approval) => !approval.status || approval.status === "pending",
    )
  ) {
    return "等待确认";
  }
  if (message.contextCompaction?.status === "running") return "整理上下文";
  if (message.toolCalling) return "执行操作";
  if (
    message.delegations?.some((item) =>
      ["queued", "claimed", "running"].includes(item.status)
    )
  ) {
    return "协调任务";
  }
  if ((message.commentary ?? "").trim()) return "推进任务";
  if (
    message.taskPlan?.status === "planned" ||
    message.taskPlan?.status === "running"
  ) {
    return message.taskPlan.steps.some((step) => step.status === "running")
      ? "推进任务"
      : "拆解任务";
  }
  if (message.toolCallSegments?.some((segment) => segment.labels.length > 0)) {
    return "核对结果";
  }
  if (message.content.trim()) return "组织回复";
  if (message.contextBudget) return "准备上下文";
  return "理解请求";
}

export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  messageIndex: number,
): WorkLogTimelineItem[] {
  const items: WorkLogTimelineItem[] = [];
  let stepParts: TimelineStepPart[] = [];
  let groupStartIndex = 0;

  const flushSteps = () => {
    if (stepParts.length === 0) return;
    items.push(
      stepParts.length === 1
        ? stepParts[0]
        : {
            type: "stepGroup",
            groupKey: `${messageIndex}-work-steps-${groupStartIndex}`,
            parts: stepParts,
          },
    );
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
    if (part.type === "commentary" || part.type === "tools") {
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

export function buildAssistantTimeline(
  message: ChatMessage,
  opts: BuildAssistantTimelineOptions,
): AssistantTimelinePart[] {
  const parts: AssistantTimelinePart[] = [];
  const segments = message.toolCallSegments ?? [];
  const blocks = message.commentaryBlocks ?? [];
  const durations = message.commentaryDurationsMs ?? [];
  const { messageIndex, isStreaming, isLastAssistant, loading } = opts;
  const emittedBlocks = new Set<number>();

  const appendCommentary = (
    blockIndex: number | null | undefined,
    region: string,
  ) => {
    if (typeof blockIndex !== "number" || emittedBlocks.has(blockIndex)) return;
    const md = blocks[blockIndex]?.trim();
    if (!md) return;
    emittedBlocks.add(blockIndex);
    parts.push({
      type: "commentary",
      md,
      durationMs: durations[blockIndex],
      regionKey: `${messageIndex}-${region}-${blockIndex}`,
    });
  };

  if (message.contextCompaction) {
    parts.push({ type: "contextCompaction", state: message.contextCompaction });
  }
  if (message.delegations?.length) {
    parts.push({ type: "delegations", items: message.delegations });
  }

  segments.forEach((segment, segmentIndex) => {
    appendCommentary(segment.commentaryBlockIndex, `tool-${segmentIndex}`);
    if (segment.labels.length === 0) return;
    parts.push({
      type: "tools",
      segment,
      segmentIndex,
      isLive:
        Boolean(isLastAssistant) &&
        Boolean(loading) &&
        segmentIndex === segments.length - 1 &&
        Boolean(message.toolCalling),
    });
  });

  blocks.forEach((_block, blockIndex) => {
    appendCommentary(blockIndex, "tail");
  });

  const assistantMarkdown = getAssistantRenderableMarkdown(message);
  // Defense in depth: even stale/replayed state must not expose answer text
  // while the root turn is still receiving process events.
  if (!isStreaming && assistantMarkdown.trim()) {
    parts.push({ type: "text", md: assistantMarkdown });
  }

  if (isStreaming && message.commentary?.trim()) {
    parts.push({
      type: "commentary",
      md: message.commentary.trim(),
      startedAt: message.commentaryStartedAt,
      regionKey: `${messageIndex}-stream-${blocks.length}`,
    });
  }

  return parts;
}
