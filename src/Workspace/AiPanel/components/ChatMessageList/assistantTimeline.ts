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

export interface OperationGroupProgress {
  total: number;
  completed: number;
  current: number;
  active: boolean;
}

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
}

export function getAssistantProcessingLabel(_message: ChatMessage): string {
  return "";
}

export function getOperationGroupProgress(
  parts: TimelineOperationPart[],
): OperationGroupProgress {
  let total = 0;
  let completed = 0;
  let activeCount = 0;

  for (const part of parts) {
    if (part.type === "tools") {
      const visibleIndexes = part.segment.labels.flatMap((_label, index) =>
        part.segment.cachedFlags?.[index] ? [] : [index],
      );
      const completedToolCount = part.isLive
        ? Math.max(0, part.segment.completedToolCount ?? 0)
        : part.segment.labels.length;
      total += visibleIndexes.length;
      completed += visibleIndexes.filter(
        (index) => index < completedToolCount,
      ).length;
      if (part.isLive) activeCount += 1;
      continue;
    }
    if (part.type === "contextCompaction") {
      total += 1;
      if (part.state.status === "running") activeCount += 1;
      else completed += 1;
      continue;
    }
    total += Math.max(1, part.items.length);
    completed += part.items.filter((item) =>
      ["done", "failed", "canceled"].includes(item.status),
    ).length;
    activeCount += part.items.filter((item) =>
      ["queued", "claimed", "running"].includes(item.status),
    ).length;
  }

  const active = activeCount > 0;
  return {
    total,
    completed,
    current: active
      ? Math.min(total, completed + Math.max(1, activeCount))
      : completed,
    active,
  };
}

export interface ExecutionPanelPresentation {
  visible: boolean;
  active: boolean;
  autoOpen: boolean;
  stepCount: number;
}

function isTimelineOperationPart(
  part: AssistantTimelinePart,
): part is TimelineOperationPart {
  return part.type === "tools"
    || part.type === "delegations"
    || part.type === "contextCompaction";
}

export function getExecutionPanelPresentation(
  parts: AssistantTimelinePart[],
  input: { isStreaming: boolean; durationMs?: number },
): ExecutionPanelPresentation {
  const progress = getOperationGroupProgress(parts.filter(isTimelineOperationPart));
  return {
    visible: input.isStreaming
      || parts.length > 0
      || (input.durationMs != null && input.durationMs > 0),
    active: input.isStreaming,
    autoOpen: input.isStreaming,
    stepCount: progress.total,
  };
}

export function getExecutionPanelLogKey(
  message: Pick<
    ChatMessage,
    "clientTurnId" | "conversationId" | "agentRunId" | "turnStartedAt"
  >,
): string | null {
  if (message.clientTurnId) {
    return `client-turn-${message.clientTurnId}-work-log`;
  }
  if (message.conversationId != null) {
    return `conversation-${message.conversationId}-work-log`;
  }
  if (message.agentRunId) {
    return `agent-run-${message.agentRunId}-work-log`;
  }
  if (message.turnStartedAt != null) {
    return `live-turn-${message.turnStartedAt}-work-log`;
  }
  return null;
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
