import type {
  AgentConversationMessage,
  ToolCallSegment,
} from "../../../agent-runtime/contracts";
import type { CanonicalOperation } from "../../../agent-runtime/canonicalOutput";
import type { Outline, EntityId } from "../../../types";
import { publicAgentProgressNarration } from "../../../agent-runtime/outputPresentation.ts";
import { markdownToPlainText } from "../../../utils/markdown.ts";
import { parseAgentMessageTime } from "../messageMetadata.ts";
import { collapseSubAgentDelegations } from "../DelegationStatus/presentation.ts";
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

export type TimelineStagePart = {
  type: "stage";
  md: string;
  streamId: string;
  status: "open" | "completed" | "aborted";
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

export type TimelineCanonicalOperationGroupPart = {
  type: "operationGroup";
  groupKey: string;
  label: string;
  operations: CanonicalOperation[];
};

export type AssistantTimelinePart =
  | TimelineStagePart
  | TimelineCommentaryPart
  | TimelineToolsPart
  | TimelineTextPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart
  | TimelineCanonicalOperationPart
  | TimelineCanonicalOperationGroupPart;

export type TimelineStepPart =
  | TimelineCommentaryPart
  | TimelineStagePart
  | TimelineToolsPart;
export type TimelineOperationPart =
  | TimelineToolsPart
  | TimelineDelegationsPart
  | TimelineContextCompactionPart
  | TimelineCanonicalOperationPart
  | TimelineCanonicalOperationGroupPart;

export function getPresentationGroupStatus(
  part: TimelineCanonicalOperationGroupPart,
): CanonicalOperation["status"] {
  if (part.operations.some((operation) => operation.status === "running")) {
    return "running";
  }
  if (part.operations.some((operation) => operation.status === "failed")) {
    return "failed";
  }
  if (part.operations.some((operation) => operation.status === "canceled")) {
    return "canceled";
  }
  return "succeeded";
}

export interface OperationGroupProgress {
  total: number;
  completed: number;
  current: number;
  active: boolean;
  parallel: boolean;
}

export interface ToolLabelContext {
  /** 本地章节目录：把工具参数中的章节 ID 解析成《标题》 */
  writingChapters?: { id: EntityId; title: string }[];
  /** 本地大纲目录：把工具参数中的大纲 ID 解析成《标题》 */
  availableOutlines?: Outline[];
  /** 本地人物目录：把工具参数中的 characterIds 解析成人物名 */
  characters?: { id: number; name: string }[];
}

export interface BuildAssistantTimelineOptions {
  messageIndex: number;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  loading?: boolean;
  /** 子 Run 可显示已收到的普通文本；根回答始终保持终态原子提交。 */
  allowStreamingText?: boolean;
  /** 解析工具参数（章节/大纲标题等）所需的页面内目录数据 */
  toolLabelContext?: ToolLabelContext;
}

function timelineTimestamp(value: string | null | undefined): number {
  return parseAgentMessageTime(value ?? undefined)?.getTime() ?? Number.NaN;
}

export function isVisibleExecutionLogPart(
  part: AssistantTimelinePart,
): boolean {
  if (part.type !== "tools") return part.type !== "text";
  return part.segment.labels.some(
    (_label, labelIndex) => !part.segment.cachedFlags?.[labelIndex],
  );
}

const STAGE_SECTION_LABEL = String.raw`(?:阶段(?:性)?进展(?:小结|总结)?|已完成(?:内容)?|进行中\s*(?:\/|／)\s*待处理|不确定性(?:与注意事项)?|注意事项|下一步)`;
const STAGE_SECTION_HEADING = new RegExp(`^${STAGE_SECTION_LABEL}[:：]?$`);
const STAGE_SECTION_PREFIX = new RegExp(`^${STAGE_SECTION_LABEL}[:：]\\s*`);
const STAGE_MARKDOWN_HEADING = new RegExp(
  String.raw`^\s{0,3}#{1,6}\s+${STAGE_SECTION_LABEL}(?:[:：].*)?\s*$`,
);

export function stageTextToParagraph(value: string): string {
  const withoutReportHeadings = value
    .split(/\r?\n/)
    .filter((line) => !STAGE_MARKDOWN_HEADING.test(line))
    .join("\n");
  return markdownToPlainText(withoutReportHeadings)
    .split(/\n+/)
    .map((line) => line.trim().replace(/^•\s*/, ""))
    .map((line) => line.replace(STAGE_SECTION_PREFIX, "").trim())
    .filter((line) => line && !STAGE_SECTION_HEADING.test(line))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
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
    if (part.type === "operationGroup") {
      total += 1;
      if (getPresentationGroupStatus(part) === "running") activeCount += 1;
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
    if (
      part.type === "operationGroup"
      && getPresentationGroupStatus(part) === "running"
    ) {
      return part.label;
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
    || part.type === "operation"
    || part.type === "operationGroup";
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
    autoOpen: false,
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

export function getAssistantExecutionStatus(
  message: Pick<AgentConversationMessage, "canonicalOutput" | "taskPlan">,
): string | null | undefined {
  // A durable pause intentionally cancels the current Root while keeping the
  // LongTask resumable. Preserve the Root event record, but present the
  // product workflow state instead of calling the paused turn "canceled".
  if (message.taskPlan?.status === "paused") return "paused";
  return message.canonicalOutput?.runStatus ?? message.taskPlan?.status;
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
    const canonicalMarkdown = canonicalOutput.finalStreamStatus === "open"
      ? message.streamingContent || message.content
      : message.content;
    const terminalAnswer = canonicalOutput.runTerminal
      ? canonicalMarkdown.trim()
      : "";
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
    collapseSubAgentDelegations(message.delegations ?? []).forEach((delegation, delegationIndex) => {
      canonicalParts.push({
        sequence: canonicalOutput.delegations[delegation.delegationId]
          ?.firstSequence
          ?? derivedDelegationSequence(
            canonicalOutput,
            delegation.startedAt,
            delegationIndex,
          ),
        part: { type: "delegations", items: [delegation] },
      });
    });
    canonicalOutput.commentaryBlocks
      .filter((block) => block.stage || !block.aborted)
      .forEach((block) => {
        const narration = block.text.trim();
        if (!narration) return;
        // A durable checkpoint is persisted both as the terminal answer and
        // as its completed stage stream. The answer owns that text once the
        // Root is terminal; otherwise a replay renders it twice (or more).
        if (terminalAnswer && narration === terminalAnswer) return;
        canonicalParts.push({
          sequence: block.firstSequence,
          part: block.stage ? {
            type: "stage",
            md: narration,
            streamId: block.outputStreamId,
            status: block.aborted ? "aborted" : block.committed ? "completed" : "open",
          } : {
            type: "commentary",
            md: narration,
            startedAt: timelineTimestamp(block.startedAt),
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
          startedAt: timelineTimestamp(progress.occurredAt),
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
          startedAt: timelineTimestamp(progress.occurredAt),
          regionKey: `${messageIndex}-agent-progress-stream-${progress.outputStreamId}`,
        },
      });
    });
    (canonicalOutput.stageOutputs ?? []).forEach((stage) => {
      const narration = publicAgentProgressNarration(stage.text);
      if (!narration) return;
      canonicalParts.push({
        sequence: stage.sequence,
        part: {
          type: "commentary",
          md: narration,
          startedAt: timelineTimestamp(stage.occurredAt),
          regionKey: `${messageIndex}-analysis-stage-${stage.stageId}`,
        },
      });
    });
    const presentationGroups = new Map<
      string,
      { sequence: number; part: TimelineCanonicalOperationGroupPart }
    >();
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
      const presentationGroup = operationPresentationGroup(operation);
      if (presentationGroup) {
        const groupKey = `${operation.runId}:${presentationGroup.key}`;
        const existing = presentationGroups.get(groupKey);
        if (existing) {
          existing.part.operations.push(operation);
        } else {
          presentationGroups.set(groupKey, {
            sequence: operation.firstSequence,
            part: {
              type: "operationGroup",
              groupKey,
              label: presentationGroup.label,
              operations: [operation],
            },
          });
        }
        return;
      }
      canonicalParts.push({
        sequence: operation.firstSequence,
        part: {
          type: "operation",
          operation,
          label: canonicalOperationLabel(operation, opts.toolLabelContext),
          isRetry: typeof operation.display.labelParams.retryOfToolCallId === "string",
        },
      });
    });
    presentationGroups.forEach(({ sequence, part }) => {
      canonicalParts.push({ sequence, part });
    });
    canonicalParts
      .sort((left, right) => left.sequence - right.sequence)
      .forEach(({ part }) => parts.push(part));

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

  collapseSubAgentDelegations(message.delegations ?? []).forEach((delegation) => {
    parts.push({ type: "delegations", items: [delegation] });
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

function derivedDelegationSequence(
  output: NonNullable<AgentConversationMessage["canonicalOutput"]>,
  startedAt: string | null | undefined,
  index: number,
): number {
  const timestamp = timelineTimestamp(startedAt);
  if (!Number.isFinite(timestamp)) return output.lastSequence + 1 + index / 1000;
  const anchors = [
    ...output.commentaryBlocks.map((item) => ({
      timestamp: timelineTimestamp(item.startedAt), sequence: item.firstSequence,
    })),
    ...output.planningProgress.map((item) => ({
      timestamp: timelineTimestamp(item.occurredAt), sequence: item.sequence,
    })),
    ...output.agentProgress.map((item) => ({
      timestamp: timelineTimestamp(item.occurredAt), sequence: item.sequence,
    })),
    ...(output.stageOutputs ?? []).map((item) => ({
      timestamp: timelineTimestamp(item.occurredAt), sequence: item.sequence,
    })),
    ...output.operationOrder.map((operationId) => output.operations[operationId])
      .filter(Boolean)
      .map((item) => ({
        timestamp: timelineTimestamp(item.startedAt), sequence: item.firstSequence,
      })),
  ].filter((item) => Number.isFinite(item.timestamp))
    .sort((left, right) => left.timestamp - right.timestamp || left.sequence - right.sequence);
  const before = anchors.filter((item) => item.timestamp <= timestamp).at(-1);
  const after = anchors.find((item) => item.timestamp > timestamp);
  if (before && after) return (before.sequence + after.sequence) / 2 + index / 1000;
  if (before) return before.sequence + 0.5 + index / 1000;
  if (after) return after.sequence - 0.5 + index / 1000;
  return output.lastSequence + 1 + index / 1000;
}

function operationPresentationGroup(
  operation: CanonicalOperation,
): { key: string; label: string } | undefined {
  const value = operation.display.labelParams.presentationGroup;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const group = value as Record<string, unknown>;
  const key = typeof group.key === "string" ? group.key.trim() : "";
  const label = typeof group.label === "string" ? group.label.trim() : "";
  return key && label ? { key, label } : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function canonicalOperationLabel(
  operation: CanonicalOperation,
  context?: ToolLabelContext,
): string {
  const params = operation.display.labelParams;
  const toolName = typeof params.toolName === "string"
    ? params.toolName
    : operation.toolName;
  const isToolOperation = operation.kind === "tool"
    || operation.display.labelKey === "agent.operation.tool";
  if (isToolOperation) {
    const displayNames = localizedDisplayNames(params.displayNames);
    const displayName = resolveLocalizedToolDisplayName(displayNames);
    const args = isRecord(params.toolArguments) ? params.toolArguments : {};
    if (toolName && Object.keys(args).length > 0) {
      // 参数化文案优先：只有后端投影了模型参数时才可能出现
      // 「检索写作记忆“xxx”」这类带具体信息的行。
      return toolCallDisplayRow(
        toolName,
        args,
        context?.writingChapters ?? [],
        context?.availableOutlines ?? [],
        displayName,
        context?.characters,
      ).label;
    }
    if (displayName) return displayName;
    if (toolName) {
      return toolCallDisplayRow(
        toolName,
        {},
        context?.writingChapters ?? [],
        context?.availableOutlines ?? [],
        undefined,
        context?.characters,
      ).label;
    }
  }
  const labels: Record<string, string> = {
    model: "生成模型响应",
    planning: "制定执行计划",
    validation: "校验输出",
    context_compaction: "压缩上下文",
    delegation: "委派子 Agent",
    tool: "执行工具",
  };
  const labelKeys: Record<string, string> = {
    "agent.operation.model": labels.model,
    "agent.operation.planning": labels.planning,
    "agent.operation.tool": labels.tool,
    "agent.operation.validation": labels.validation,
    "agent.operation.context_compaction": labels.context_compaction,
  };
  return labels[operation.kind]
    || (operation.display.labelKey
      ? labelKeys[operation.display.labelKey]
      : undefined)
    || "执行操作";
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
