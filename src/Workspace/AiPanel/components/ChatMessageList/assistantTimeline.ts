import { getAssistantRenderableMarkdown } from "../../rendering";
import type { ChatMessage, ToolCallSegment } from "../../hooks/chat.types";

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

export function getAssistantProcessingLabel(message: ChatMessage): string {
  if (
    message.toolApprovals?.some(
      (approval) => !approval.status || approval.status === "pending",
    )
  ) {
    return "等待你确认下一步";
  }
  if (message.contextCompaction?.status === "running") {
    return "正在整理对话上下文";
  }
  if (message.toolCalling) {
    return "正在执行必要操作";
  }
  if (
    message.delegations?.some((item) =>
      item.status === "queued" ||
      item.status === "claimed" ||
      item.status === "running"
    )
  ) {
    return "正在协调多个处理任务";
  }
  if ((message.thinking ?? "").trim()) {
    return "正在推演处理方案";
  }
  if ((message.contentAfterToolCalls ?? "").trim()) {
    return "正在组织回复内容";
  }
  if (
    message.taskPlan?.status === "planned" ||
    message.taskPlan?.status === "running"
  ) {
    return message.taskPlan.steps.some((step) => step.status === "running")
      ? "正在推进任务步骤"
      : "正在拆解任务步骤";
  }
  if (message.toolCallSegments?.some((segment) => segment.labels.length > 0)) {
    return "正在核对执行结果";
  }
  if (message.content.trim()) {
    return "正在组织回复内容";
  }
  if (message.contextBudget) {
    return "正在装配相关上下文";
  }
  return "正在理解请求并准备处理";
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
  const emittedThinkingBlocks = new Set<number>();
  const hasExplicitThinkingOrder = segments.some((segment) =>
    Object.prototype.hasOwnProperty.call(segment, "thinkingBlockIndex"),
  );
  // 旧版工具轮会吞掉工具前思考；当思考块少于工具段时，现存块实际来自
  // 最终回答阶段，应统一放到工具之后，不能再按数组下标硬配。
  const legacyBlocksAreTail =
    !hasExplicitThinkingOrder &&
    segments.length > 0 &&
    blocks.length < segments.length;
  const appendThinkingBlock = (
    blockIndex: number | null | undefined,
    region: string,
  ) => {
    if (
      typeof blockIndex !== "number" ||
      emittedThinkingBlocks.has(blockIndex)
    ) {
      return;
    }
    const block = blocks[blockIndex]?.trim();
    if (!block) return;
    emittedThinkingBlocks.add(blockIndex);
    parts.push({
      type: "thinking",
      text: block,
      durationMs: durations[blockIndex],
      regionKey: `${messageIndex}-${region}-${blockIndex}`,
    });
  };

  if (message.contextCompaction) {
    parts.push({
      type: "contextCompaction",
      state: message.contextCompaction,
    });
  }
  if (message.delegations?.length) {
    parts.push({ type: "delegations", items: message.delegations });
  }

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    appendThinkingBlock(
      hasExplicitThinkingOrder
        ? seg.thinkingBlockIndex
        : legacyBlocksAreTail
          ? undefined
          : i,
      `seg-${i}`,
    );
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

  for (let blockIndex = 0; blockIndex < blocks.length; blockIndex += 1) {
    appendThinkingBlock(blockIndex, "tail");
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
