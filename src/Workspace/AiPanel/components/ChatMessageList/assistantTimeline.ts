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
export type TimelineOperationPart =
  | TimelineToolsPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart;

export type WorkLogTimelineItem =
  | AssistantTimelinePart
  | {
      type: "stepGroup";
      groupKey: string;
      parts: TimelineOperationPart[];
    };

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
}

export function getAssistantProcessingLabel(_message: ChatMessage): string {
  return "";
}

export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  messageIndex: number,
): WorkLogTimelineItem[] {
  const items: WorkLogTimelineItem[] = [];
  let stepParts: TimelineOperationPart[] = [];
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
    if (
      part.type === "tools" ||
      part.type === "delegations" ||
      part.type === "contextCompaction"
    ) {
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
