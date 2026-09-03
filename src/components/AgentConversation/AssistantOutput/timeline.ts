import type {
  AgentConversationMessage,
  ToolCallSegment,
} from "../../../agent-runtime/contracts";
import type { CanonicalOperation } from "../../../agent-runtime/canonicalOutput";
import { publicAgentProgressNarration } from "../../../agent-runtime/outputPresentation.ts";
import {
  resolveLocalizedToolDisplayName,
  toolCallDisplayRow,
} from "../toolCallLabels.ts";
export { groupConsecutiveWorkSteps } from "../ExecutionLog/grouping.ts";
export type { ExecutionLogTimelineItem } from "../ExecutionLog/grouping.ts";

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
  items: NonNullable<AgentConversationMessage["delegations"]>;
};
export type TimelineContextCompactionPart = {
  type: "contextCompaction";
  state: NonNullable<AgentConversationMessage["contextCompaction"]>;
};
export type TimelineCanonicalOperationPart = {
  type: "operation";
  operation: CanonicalOperation;
  label: string;
  isRetry: boolean;
};

export type AssistantTimelinePart =
  | TimelineCommentaryPart
  | TimelineToolsPart
  | TimelineTextPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart
  | TimelineCanonicalOperationPart;

export type TimelineStepPart = TimelineCommentaryPart | TimelineToolsPart;
export type TimelineOperationPart =
  | TimelineToolsPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart
  | TimelineCanonicalOperationPart;

export interface OperationGroupProgress {
  total: number;
  completed: number;
  current: number;
  active: boolean;
  parallel: boolean;
}

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
  /** 子 Run 可显示已收到的普通文本；根回答始终保持终态原子提交。 */
  allowStreamingText?: boolean;
}

export function getOperationGroupProgress(
  parts: TimelineOperationPart[],
): OperationGroupProgress {
  let total = 0;
  let completed = 0;
  let activeCount = 0;

  for (const part of parts) {
    if (part.type === "operation") {
      total += 1;
      if (part.operation.status === "running") activeCount += 1;
      else completed += 1;
      continue;
    }
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
    parallel: activeCount > 1,
  };
}

export interface ExecutionPanelPresentation {
  visible: boolean;
  active: boolean;
  autoOpen: boolean;
  stepCount: number;
  title: string;
}

export function getActiveOperationLabel(
  parts: TimelineOperationPart[],
): string | undefined {
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (part.type === "operation" && part.operation.status === "running") {
      return part.isRetry ? `重试 ${part.label}` : part.label;
    }
    if (part.type === "tools" && part.isLive) {
      const completed = Math.max(0, part.segment.completedToolCount ?? 0);
      const activeLabel = part.segment.labels.find(
        (_label, labelIndex) =>
          labelIndex >= completed && !part.segment.cachedFlags?.[labelIndex],
      );
      if (activeLabel) return activeLabel;
    }
    if (
      part.type === "contextCompaction"
      && part.state.status === "running"
    ) {
      return "压缩上下文";
    }
  }
  return undefined;
}

function isTimelineOperationPart(
  part: AssistantTimelinePart,
): part is TimelineOperationPart {
  return part.type === "tools"
    || part.type === "delegations"
    || part.type === "contextCompaction"
    || part.type === "operation";
}

export function getExecutionPanelPresentation(
  parts: AssistantTimelinePart[],
  input: {
    isStreaming: boolean;
    durationMs?: number;
    status?: string | null;
    hasError?: boolean;
  },
): ExecutionPanelPresentation {
  const operationParts = parts.filter(isTimelineOperationPart);
  const progress = getOperationGroupProgress(operationParts);
  const active = input.isStreaming;
  const stepCount = progress.total;
  const visible = active
    || parts.length > 0
    || (input.durationMs != null && input.durationMs > 0);
  return {
    visible,
    active,
    autoOpen: active,
    stepCount,
    title: active
      ? "正在进行"
      : input.hasError || input.status === "failed" || input.status === "blocked"
        ? "执行失败"
        : input.status === "canceled"
          ? "已取消"
          : input.status === "paused"
            ? "已暂停"
            : "已完成",
  };
}

export function executionPanelHasTerminalError(
  message: Pick<
    AgentConversationMessage,
    "isError" | "error" | "canonicalOutput" | "taskPlan"
  >,
): boolean {
  const runStatus = message.canonicalOutput?.runStatus;
  const planStatus = message.taskPlan?.status;
  return Boolean(
    message.isError
    || message.error?.trim()
    || runStatus === "failed"
    || runStatus === "blocked"
    || planStatus === "failed"
    || planStatus === "blocked"
  );
}

export function getCanonicalOperationStatusText(
  operation: CanonicalOperation,
  label: string,
  isRetry: boolean,
): string {
  if (isRetry) {
    if (operation.status === "running") return `正在重试 ${label}`;
    if (operation.status === "failed") return `重试失败 ${label}`;
    if (operation.status === "canceled") return `已取消重试 ${label}`;
    return `重试成功 ${label}`;
  }
  if (operation.status === "running") return `正在执行 ${label}`;
  if (operation.status === "failed") return `执行失败 ${label}`;
  if (operation.status === "canceled") return `已取消 ${label}`;
  return `已完成 ${label}`;
}

export function getExecutionPanelLogKey(
  message: Pick<
    AgentConversationMessage,
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
  message: AgentConversationMessage,
  opts: BuildAssistantTimelineOptions,
): AssistantTimelinePart[] {
  const parts: AssistantTimelinePart[] = [];
  const segments = message.toolCallSegments ?? [];
  const blocks = message.commentaryBlocks ?? [];
  const durations = message.commentaryDurationsMs ?? [];
  const {
    messageIndex,
    isStreaming,
    isLastAssistant,
    loading,
    allowStreamingText = false,
  } = opts;
  const emittedBlocks = new Set<number>();

  if (message.canonicalOutput) {
    const canonicalOutput = message.canonicalOutput;
    const canonicalParts: Array<{
      sequence: number;
      part: AssistantTimelinePart;
    }> = [];
    if (message.contextCompaction) {
      const compactionOperation = canonicalOutput.operationOrder.reduce<
        CanonicalOperation | undefined
      >((latest, operationId) => {
        const operation = canonicalOutput.operations[operationId];
        return operation?.kind === "context_compaction"
          && (!latest || operation.firstSequence > latest.firstSequence)
          ? operation
          : latest;
      }, undefined);
      const compactionRuntime = canonicalOutput.latestRuntimeEvent;
      canonicalParts.push({
        sequence: compactionOperation?.firstSequence
          ?? (compactionRuntime?.eventType.startsWith("conversation.compaction.")
            ? compactionRuntime.sequence
            : Number.NEGATIVE_INFINITY),
        part: { type: "contextCompaction", state: message.contextCompaction },
      });
    }
    if (message.delegations?.length) {
      const delegationSequences = message.delegations.flatMap((delegation) => {
        const sequence = canonicalOutput.delegations[delegation.delegationId]
          ?.firstSequence;
        return sequence == null ? [] : [sequence];
      });
      canonicalParts.push({
        sequence: delegationSequences.length > 0
          ? Math.min(...delegationSequences)
          : Number.NEGATIVE_INFINITY,
        part: { type: "delegations", items: message.delegations },
      });
    }
    canonicalOutput.commentaryBlocks
      .filter((block) => !block.aborted)
      .forEach((block) => {
        const narration = publicAgentProgressNarration(block.text);
        if (!narration) return;
        canonicalParts.push({
          sequence: block.firstSequence,
          part: {
            type: "commentary",
            md: narration,
            startedAt: Date.parse(block.startedAt),
            regionKey: `${messageIndex}-canonical-commentary-${block.outputStreamId}`,
          },
        });
    });
    canonicalOutput.planningProgress.forEach((progress) => {
      const narration = publicAgentProgressNarration(progress.text);
      if (!narration) return;
      canonicalParts.push({
        sequence: progress.sequence,
        part: {
          type: "commentary",
          md: narration,
          startedAt: Date.parse(progress.occurredAt),
          regionKey: `${messageIndex}-planning-progress-${progress.eventId}`,
        },
      });
    });
    canonicalOutput.agentProgress.forEach((progress) => {
      const narration = publicAgentProgressNarration(progress.text);
      if (!narration) return;
      canonicalParts.push({
        sequence: progress.sequence,
        part: {
          type: "commentary",
          md: narration,
          startedAt: Date.parse(progress.occurredAt),
          regionKey: `${messageIndex}-agent-progress-stream-${progress.outputStreamId}`,
        },
      });
    });
    canonicalOutput.operationOrder.forEach((operationId) => {
      const operation = canonicalOutput.operations[operationId];
      // Planning and model lifecycle remain canonical state. Their public
      // narration is rendered as commentary, not as an executable work row.
      if (
        !operation
        || operation.kind === "planning"
        || operation.kind === "model"
        || operation.kind === "validation"
        || (operation.kind === "context_compaction" && message.contextCompaction)
        || (operation.kind === "delegation" && message.delegations?.length)
      ) return;
      canonicalParts.push({
        sequence: operation.firstSequence,
        part: {
          type: "operation",
          operation,
          label: canonicalOperationLabel(operation),
          isRetry: typeof operation.display.labelParams.retryOfToolCallId === "string",
        },
      });
    });
    canonicalParts
      .sort((left, right) => left.sequence - right.sequence)
      .forEach(({ part }) => parts.push(part));

    const canonicalMarkdown = canonicalOutput.finalStreamStatus === "open"
      ? message.streamingContent || message.content
      : message.content;
    if (canonicalMarkdown.trim()) {
      parts.push({ type: "text", md: canonicalMarkdown });
    }
    return parts;
  }

  const appendCommentary = (
    blockIndex: number | null | undefined,
    region: string,
  ) => {
    if (typeof blockIndex !== "number" || emittedBlocks.has(blockIndex)) return;
    const md = publicAgentProgressNarration(blocks[blockIndex]);
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
    const timedSegment = segment.itemDurationsMs == null
      && segment.labels.length === 1
      && segment.durationMs != null
      ? { ...segment, itemDurationsMs: [segment.durationMs] }
      : segment;
    parts.push({
      type: "tools",
      segment: timedSegment,
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

  const assistantMarkdown = message.content;
  // Defense in depth: even stale/replayed state must not expose answer text
  // while the root turn is still receiving process events.
  if (
    (message.streamingContent || !isStreaming || allowStreamingText)
    && (message.streamingContent || assistantMarkdown).trim()
  ) {
    parts.push({
      type: "text",
      md: message.streamingContent || assistantMarkdown,
    });
  }

  if (isStreaming && message.commentary?.trim()) {
    const narration = publicAgentProgressNarration(message.commentary);
    if (narration) {
      parts.push({
        type: "commentary",
        md: narration,
        startedAt: message.commentaryStartedAt,
        regionKey: `${messageIndex}-stream-${blocks.length}`,
      });
    }
  }

  return parts;
}

function canonicalOperationLabel(operation: CanonicalOperation): string {
  const params = operation.display.labelParams;
  const toolName = typeof params.toolName === "string"
    ? params.toolName
    : operation.toolName;
  if (operation.kind === "tool" && toolName) {
    const displayNames = localizedDisplayNames(params.displayNames);
    return toolCallDisplayRow(
      toolName,
      {},
      [],
      [],
      resolveLocalizedToolDisplayName(displayNames),
    ).label;
  }
  const labels: Record<string, string> = {
    validation: "校验输出",
    context_compaction: "压缩上下文",
    delegation: "委派子 Agent",
    tool: "执行工具",
  };
  return labels[operation.kind] || "执行操作";
}

function localizedDisplayNames(
  value: unknown,
): Record<string, string> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const entries = Object.entries(value).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string",
  );
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}
