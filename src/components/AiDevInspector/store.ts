import type {
  AiAgentRunSnapshot,
  AiErrorReport,
  AiErrorReportStatus,
  ElectronAPI,
} from "../../types";
import {
  isCanonicalOutputEvent,
  type CanonicalOutputEvent,
} from "../../agent-runtime/canonicalOutput.ts";

type AiStreamRequest = Parameters<ElectronAPI["aiChatStream"]>[0];
export type AiDebugChunk = Parameters<
  Parameters<ElectronAPI["onAiChunk"]>[0]
>[0];

export type AiDebugRunStatus =
  | "starting"
  | "preparing"
  | "planning"
  | "thinking"
  | "tool"
  | "awaiting_approval"
  | "responding"
  | "dispatched"
  | "completed"
  | "aborted"
  | "failed";

export type AiDebugToolStatus = "running" | "completed" | "failed";

export interface AiDebugTool {
  id: string;
  batchIndex: number;
  index: number;
  name: string;
  argumentsText: string;
  argumentsValue: unknown;
  status: AiDebugToolStatus;
  cached: boolean;
  startedAt: number;
  completedAt?: number;
  result?: unknown;
  outcome?: string;
  errorCode?: string;
  errorMessage?: string;
  exceptionType?: string;
  diagnostics?: Record<string, unknown>;
}

export interface AiDebugEvent {
  id: number;
  at: number;
  type: string;
  label: string;
  payload?: unknown;
}

export interface AiDebugModelCall {
  id: string;
  at: number;
  phase: string;
  count: number;
  toolNames: string[];
  toolChoice?: string;
  round?: number;
  logicalRound?: number;
  attempt?: number;
  revision?: number;
  judgeIndex?: number;
  parameters?: Record<string, unknown>;
}

export interface AiDebugMessage {
  role: string;
  content: unknown;
  tool_calls?: unknown[];
  reasoning_content?: string;
  tool_call_id?: string;
}

export interface AiDebugChildRun {
  id: string;
  delegationId: string;
  childRunId?: string;
  agentRole: string;
  agentTitle?: string;
  objective: string;
  unitId?: string;
  attempt?: number;
  taskType: string;
  status: AiDebugRunStatus;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  model?: string;
  output: string;
  commentary: string;
  modelCalls: AiDebugModelCall[];
  tools: AiDebugTool[];
  events: AiDebugEvent[];
  eventCount: number;
  contextBudget?: unknown;
  contextCompaction?: unknown;
  error?: string;
}

export interface AiDebugRun {
  id: string;
  turnId?: string;
  sessionId?: number;
  conversationId?: number;
  source: string;
  taskType: string;
  status: AiDebugRunStatus;
  startedAt: number;
  updatedAt: number;
  finishedAt?: number;
  request: {
    messages: AiDebugMessage[];
    meta: Record<string, unknown>;
  };
  model?: string;
  output: string;
  commentary: string;
  modelCalls: AiDebugModelCall[];
  tools: AiDebugTool[];
  events: AiDebugEvent[];
  eventCount: number;
  contextBudget?: unknown;
  contextCompaction?: unknown;
  agentRunId?: string;
  agentPlan?: unknown;
  delegations: unknown[];
  childRuns: AiDebugChildRun[];
  approvals: unknown[];
  error?: string;
  errorReport?: AiErrorReport;
  abortRequested?: boolean;
  persistedEventCursor?: number;
}

interface AiDebugState {
  runs: AiDebugRun[];
  selectedRunId: string | null;
}

export interface AiDebugTurnGroup {
  key: string;
  runs: AiDebugRun[];
  startedAt: number;
  updatedAt: number;
  sessionId?: number;
  conversationId?: number;
  source: string;
  prompt: string;
}

const MAX_TURNS = 20;
const MAX_EVENTS_PER_RUN = 200;
const SENSITIVE_KEY =
  /^(api[-_]?key|authorization|password|passwd|secret|access[-_]?token|refresh[-_]?token|token)$/i;
const TERMINAL_STATUSES = new Set<AiDebugRunStatus>([
  "dispatched",
  "completed",
  "aborted",
  "failed",
]);
const FINAL_STATUSES = new Set<AiDebugRunStatus>([
  "completed",
  "aborted",
  "failed",
]);
const DEBUG_STORE_ENABLED = import.meta.env?.DEV !== false;

let state: AiDebugState = {
  runs: [],
  selectedRunId: null,
};
let eventSequence = 0;
let notifyScheduled = false;
const listeners = new Set<() => void>();

function latestUserPrompt(run: AiDebugRun): string {
  const message = [...run.request.messages]
    .reverse()
    .find((item) => item.role === "user");
  return typeof message?.content === "string" ? message.content.trim() : "";
}

/** A user turn owns one or more model/Agent Runs. */
export function aiDebugTurnKey(run: AiDebugRun): string {
  if (run.turnId) return `session:${run.sessionId ?? "unknown"}:turn:${run.turnId}`;
  if (run.conversationId != null) return `conversation:${run.conversationId}`;
  return `stream:${run.id}`;
}

export function groupAiDebugRunsByTurn(runs: AiDebugRun[]): AiDebugTurnGroup[] {
  const groups = new Map<string, AiDebugTurnGroup>();
  for (const run of runs) {
    const key = aiDebugTurnKey(run);
    const group = groups.get(key);
    if (group) {
      group.runs.push(run);
      group.startedAt = Math.min(group.startedAt, run.startedAt);
      group.updatedAt = Math.max(group.updatedAt, run.updatedAt);
      group.conversationId ??= run.conversationId;
      if (!group.prompt) group.prompt = latestUserPrompt(run);
      continue;
    }
    groups.set(key, {
      key,
      runs: [run],
      startedAt: run.startedAt,
      updatedAt: run.updatedAt,
      sessionId: run.sessionId,
      conversationId: run.conversationId,
      source: run.source,
      prompt: latestUserPrompt(run),
    });
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      runs: [...group.runs].sort((left, right) => right.startedAt - left.startedAt),
    }))
    .sort((left, right) => right.updatedAt - left.updatedAt);
}

function retainRecentTurns(runs: AiDebugRun[]): AiDebugRun[] {
  const retainedKeys = new Set(
    groupAiDebugRunsByTurn(runs).slice(0, MAX_TURNS).map((group) => group.key),
  );
  return runs.filter((run) => retainedKeys.has(aiDebugTurnKey(run)));
}

function scheduleNotify(): void {
  if (notifyScheduled) return;
  notifyScheduled = true;
  const notify = () => {
    notifyScheduled = false;
    listeners.forEach((listener) => listener());
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(notify);
  } else {
    setTimeout(notify, 0);
  }
}

function setState(next: AiDebugState): void {
  state = next;
  scheduleNotify();
}

function replaceRun(streamId: string, updater: (run: AiDebugRun) => AiDebugRun): void {
  const index = state.runs.findIndex((run) => run.id === streamId);
  if (index < 0) return;
  const nextRuns = [...state.runs];
  nextRuns[index] = updater(nextRuns[index]);
  setState({ ...state, runs: nextRuns });
}

function replaceRunByAgentRunId(
  agentRunId: string,
  updater: (run: AiDebugRun) => AiDebugRun,
): void {
  const index = state.runs.findIndex((run) => run.agentRunId === agentRunId);
  if (index < 0) return;
  const nextRuns = [...state.runs];
  nextRuns[index] = updater(nextRuns[index]);
  setState({ ...state, runs: nextRuns });
}

function sanitizeUrl(value: string): string {
  try {
    const url = new URL(value);
    url.username = url.username ? "••••" : "";
    url.password = url.password ? "••••" : "";
    for (const key of [...url.searchParams.keys()]) {
      if (SENSITIVE_KEY.test(key)) url.searchParams.set(key, "••••");
    }
    return url.toString();
  } catch {
    return value;
  }
}

function sanitizeValue(value: unknown, key = "", depth = 0): unknown {
  if (SENSITIVE_KEY.test(key)) return "••••";
  if (depth > 10) return "[已省略：嵌套层级过深]";
  if (value == null || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return /baseurl|url/i.test(key) ? sanitizeUrl(value) : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeValue(item, "", depth + 1));
  }
  if (typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [itemKey, itemValue] of Object.entries(value)) {
      result[itemKey] = sanitizeValue(itemValue, itemKey, depth + 1);
    }
    return result;
  }
  return String(value);
}

function sourceLabel(streamId: string): string {
  if (streamId.startsWith("chat-")) return "主对话";
  if (streamId.startsWith("screenplay-")) return "剧本 Agent 对话";
  if (streamId.startsWith("inline-edit-")) return "行内改写";
  if (streamId.startsWith("editor-float-")) return "编辑器改写";
  if (streamId.startsWith("ghost-completion-")) return "幽灵补全";
  return "AI 对话";
}

function initialTaskType(streamId: string, request: AiStreamRequest): string {
  if (streamId.startsWith("screenplay-")) return "剧本 Agent 任务";
  if (streamId.startsWith("inline-edit-")) return "行内改写";
  if (streamId.startsWith("editor-float-")) return "编辑器选区改写";
  if (streamId.startsWith("ghost-completion-")) return "幽灵补全";
  return request.chatAgentMode === "agent" ? "写作 Agent 任务" : "普通对话";
}

function updatedTaskType(run: AiDebugRun, chunk: AiDebugChunk): string {
  if (chunk.longTaskDispatched?.taskId) {
    return chunk.longTaskDispatched.kind === "screenplay_draft_generation"
      ? "持久化长任务 · 剧本正文分批创作"
      : `持久化长任务 · ${chunk.longTaskDispatched.kind || "通用任务"}`;
  }
  return run.taskType;
}

function compactEventPayload(chunk: AiDebugChunk): unknown {
  const payload = sanitizeValue(chunk) as Record<string, unknown>;
  const eventPayload = payload.payload;
  if (eventPayload && typeof eventPayload === "object") {
    const next = { ...(eventPayload as Record<string, unknown>) };
    if (typeof next.delta === "string" && next.delta.length > 1_200) {
      next.delta = `${next.delta.slice(0, 1_200)}…`;
    }
    payload.payload = next;
  }
  return payload;
}

function chunkSummary(chunk: AiDebugChunk): { type: string; label: string } {
  if (isCanonicalOutputEvent(chunk)) {
    if (chunk.kind === "provider.content_delta") {
      return chunk.channel === "final"
        ? { type: "response", label: "收到 Provider 最终回答增量" }
        : { type: "commentary", label: "收到 Provider 执行说明增量" };
    }
    if (chunk.kind === "operation.started") {
      return { type: "operation", label: "操作已开始" };
    }
    if (chunk.kind === "operation.finished") {
      return { type: "operation", label: "操作已结束" };
    }
    if (chunk.kind === "delegation.event") {
      return { type: "delegation", label: "子 Agent 事件" };
    }
    if (chunk.kind === "run.lifecycle") {
      return { type: "agent", label: "Agent Run 状态已更新" };
    }
    if (chunk.kind === "runtime.event") {
      const eventType = String(chunk.payload.eventType || "");
      if (eventType === "approval.requested") {
        return { type: "approval", label: "工具等待批准" };
      }
      if (eventType === "approval.resolved") {
        const data = chunk.payload.data as Record<string, unknown> | undefined;
        return {
          type: "approval",
          label: `工具审批：${String(data?.status || "resolved")}`,
        };
      }
    }
    return { type: "event", label: `Agent 事件 · ${chunk.kind}` };
  }
  if (chunk.error) return { type: "error", label: chunk.error };
  if (chunk.done && chunk.errorReport) {
    return { type: "error", label: chunk.errorReport.errorMessage };
  }
  if (chunk.aborted) return { type: "aborted", label: "流已中止" };
  if (chunk.done) return { type: "done", label: "本轮完成" };
  if (chunk.longTaskDispatched) {
    return { type: "long_task", label: "持久化长任务已创建" };
  }
  return { type: "event", label: "收到运行事件" };
}

function nextStatus(run: AiDebugRun, chunk: AiDebugChunk): AiDebugRunStatus {
  if (FINAL_STATUSES.has(run.status)) return run.status;
  if (isCanonicalOutputEvent(chunk)) {
    if (chunk.kind === "run.lifecycle") {
      const status = String(chunk.payload.status || "");
      if (status === "done") return "completed";
      if (status === "failed" || status === "blocked") return "failed";
      if (status === "canceled") return "aborted";
    }
    if (chunk.kind === "provider.content_delta") {
      return chunk.channel === "final" ? "responding" : "planning";
    }
    if (chunk.kind === "operation.started") {
      return chunk.payload.kind === "tool" ? "tool" : "thinking";
    }
    const runtime = canonicalRuntimeData(chunk);
    if (runtime?.eventType === "approval.requested") return "awaiting_approval";
    if (runtime?.eventType === "approval.resolved") return "tool";
    return run.status;
  }
  if (chunk.error) return "failed";
  if (chunk.done && chunk.errorReport && !chunk.aborted) return "failed";
  if (chunk.aborted) return "aborted";
  if (run.status === "dispatched") return run.status;
  if (chunk.longTaskDispatched) return "dispatched";
  if (chunk.done) {
    return run.taskType.startsWith("持久化长任务")
      ? "dispatched"
      : "completed";
  }
  return run.status;
}

function appendEvent(run: AiDebugRun, chunk: AiDebugChunk, now: number): AiDebugEvent[] {
  const summary = chunkSummary(chunk);
  const event: AiDebugEvent = {
    id: ++eventSequence,
    at: now,
    type: summary.type,
    label: summary.label,
    payload: compactEventPayload(chunk),
  };
  return [...run.events, event].slice(-MAX_EVENTS_PER_RUN);
}

function upsertTools(
  run: AiDebugRun,
  chunk: AiDebugChunk,
  now: number,
): AiDebugTool[] {
  let tools = run.tools;
  if (isCanonicalOutputEvent(chunk)) {
    const operationId = String(chunk.payload.operationId || "");
    if (
      chunk.kind === "operation.started"
      && chunk.payload.kind === "tool"
      && operationId
    ) {
      const display = chunk.payload.display as Record<string, unknown> | undefined;
      const params = display?.labelParams as Record<string, unknown> | undefined;
      tools = [...tools, {
        id: operationId,
        batchIndex: tools.length,
        index: 0,
        name: String(params?.toolName || "工具操作"),
        argumentsText: "{}",
        argumentsValue: {},
        status: "running",
        cached: false,
        startedAt: Date.parse(String(chunk.payload.startedAt || chunk.occurredAt)),
      }];
    } else if (chunk.kind === "operation.finished" && operationId) {
      const status = String(chunk.payload.status || "");
      tools = tools.map((tool) => tool.id === operationId ? {
        ...tool,
        status: status === "succeeded" ? "completed" : "failed",
        completedAt: Date.parse(
          String(chunk.payload.finishedAt || chunk.occurredAt),
        ),
        outcome: status,
        errorCode: String(chunk.payload.errorCode || "") || undefined,
      } : tool);
    } else if (chunk.kind === "tool.event" && operationId) {
      tools = tools.map((tool) => tool.id === operationId ? {
        ...tool,
        name: String(chunk.payload.toolName || tool.name),
      } : tool);
    }
  } else if (chunk.error) {
    tools = tools.map((tool) =>
      tool.status === "running"
        ? { ...tool, status: "failed" as const, completedAt: now }
        : tool,
    );
  }
  return tools;
}

function appendModelCall(
  run: AiDebugRun,
  chunk: AiDebugChunk,
  now: number,
): AiDebugModelCall[] {
  if (
    !isCanonicalOutputEvent(chunk)
    || chunk.kind !== "operation.started"
    || chunk.payload.kind !== "model"
  ) return run.modelCalls;
  const operationId = String(chunk.payload.operationId || "");
  if (!operationId || run.modelCalls.some((call) => call.id === operationId)) {
    return run.modelCalls;
  }
  const display = chunk.payload.display as Record<string, unknown> | undefined;
  const parameters = display?.labelParams as Record<string, unknown> | undefined;
  return [...run.modelCalls, {
    id: operationId,
    at: now,
    phase: "model",
    count: 1,
    toolNames: [],
    parameters: parameters
      ? sanitizeValue(parameters) as Record<string, unknown>
      : undefined,
  }];
}

function getAgentRunId(chunk: AiDebugChunk): string | undefined {
  return isCanonicalOutputEvent(chunk) ? chunk.runId : (
    chunk.longTaskDispatched?.runId
  );
}

function delegationStatus(
  status: unknown,
  current: AiDebugRunStatus,
): AiDebugRunStatus {
  if (status === "done") return "completed";
  if (status === "failed") return "failed";
  if (status === "canceled") return "aborted";
  if (status === "running" || status === "claimed") return "thinking";
  if (status === "queued") return "preparing";
  return current;
}

function delegationMetadata(value: unknown): {
  delegationId: string;
  childRunId?: string;
  agentRole: string;
  agentTitle?: string;
  objective: string;
  unitId?: string;
  attempt?: number;
  status?: unknown;
  error?: string;
} | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const input = raw.input && typeof raw.input === "object"
    ? raw.input as Record<string, unknown>
    : {};
  const delegationId = String(raw.delegationId || "").trim();
  const agentRole = String(raw.agentRole || "").trim();
  if (!delegationId || !agentRole) return null;
  const parsedAttempt = Number(raw.attempt ?? input.attempt);
  return {
    delegationId,
    childRunId: String(raw.childRunId || "").trim() || undefined,
    agentRole,
    agentTitle: String(raw.agentTitle || "").trim() || undefined,
    objective: String(raw.objective || "").trim(),
    unitId: String(raw.unitId || input.unitId || "").trim() || undefined,
    attempt: Number.isFinite(parsedAttempt) && parsedAttempt > 0
      ? parsedAttempt
      : undefined,
    status: raw.status,
    error: String(raw.error || "").trim() || undefined,
  };
}

function updateChildRuns(
  run: AiDebugRun,
  chunk: AiDebugChunk,
  now: number,
): AiDebugChildRun[] {
  if (
    !isCanonicalOutputEvent(chunk)
    || chunk.kind !== "delegation.event"
  ) return run.childRuns;
  const metadata = delegationMetadata(chunk.payload);
  if (!metadata) return run.childRuns;
  const childEvent = isCanonicalOutputEvent(chunk.payload.event)
    ? chunk.payload.event
    : null;
  const index = run.childRuns.findIndex(
    (item) => item.delegationId === metadata.delegationId,
  );
  const existing = index >= 0 ? run.childRuns[index] : undefined;
  const base: AiDebugChildRun = existing ?? {
    id: `${run.id}-child-${metadata.delegationId}`,
    delegationId: metadata.delegationId,
    childRunId: metadata.childRunId,
    agentRole: metadata.agentRole,
    agentTitle: metadata.agentTitle,
    objective: metadata.objective,
    unitId: metadata.unitId,
    attempt: metadata.attempt,
    taskType: "子 Agent",
    status: "preparing",
    startedAt: now,
    updatedAt: now,
    output: "",
    commentary: "",
    modelCalls: [],
    tools: [],
    events: [],
    eventCount: 0,
  };
  let next = {
    ...base,
    childRunId: metadata.childRunId || base.childRunId,
    agentRole: metadata.agentRole || base.agentRole,
    agentTitle: metadata.agentTitle || base.agentTitle,
    objective: metadata.objective || base.objective,
    unitId: metadata.unitId || base.unitId,
    attempt: metadata.attempt || base.attempt,
    updatedAt: now,
  };
  if (childEvent) {
    const status = nextStatus(next as unknown as AiDebugRun, childEvent);
    const terminal = TERMINAL_STATUSES.has(status);
    const delta = childEvent.kind === "provider.content_delta"
      ? String(childEvent.payload.delta || "")
      : "";
    next = {
      ...next,
      status,
      finishedAt: terminal ? next.finishedAt ?? now : next.finishedAt,
      childRunId: childEvent.runId || next.childRunId,
      output: next.output + (childEvent.channel === "final" ? delta : ""),
      commentary: next.commentary + (
        childEvent.channel === "commentary" ? delta : ""
      ),
      modelCalls: appendModelCall(
        next as unknown as AiDebugRun,
        childEvent,
        now,
      ),
      tools: upsertTools(next as unknown as AiDebugRun, childEvent, now),
      events: appendEvent(next as unknown as AiDebugRun, childEvent, now),
      eventCount: next.eventCount + 1,
    };
  } else {
    const status = delegationStatus(metadata.status, next.status);
    next = {
      ...next,
      status,
      finishedAt: TERMINAL_STATUSES.has(status)
        ? next.finishedAt ?? now
        : next.finishedAt,
      error: metadata.error || next.error,
    };
  }
  if (index < 0) return [...run.childRuns, next];
  return run.childRuns.map((item, itemIndex) => itemIndex === index ? next : item);
}

function upsertDebugDelegation(items: unknown[], value: unknown): unknown[] {
  const metadata = delegationMetadata(value);
  if (!metadata) return items;
  const index = items.findIndex((item) =>
    delegationMetadata(item)?.delegationId === metadata.delegationId,
  );
  const sanitized = sanitizeValue(value);
  if (index < 0) return [...items, sanitized];
  return items.map((item, itemIndex) => itemIndex === index ? sanitized : item);
}

function canonicalRuntimeData(event: CanonicalOutputEvent | null): {
  eventType: string;
  data: Record<string, unknown>;
} | null {
  if (event?.kind !== "runtime.event") return null;
  const eventType = String(event.payload.eventType || "");
  const data = event.payload.data;
  if (!eventType || !data || typeof data !== "object" || Array.isArray(data)) {
    return null;
  }
  return { eventType, data: data as Record<string, unknown> };
}

function canonicalModel(event: CanonicalOutputEvent | null): string | undefined {
  if (event?.kind !== "operation.started" || event.payload.kind !== "model") {
    return undefined;
  }
  const display = event.payload.display;
  if (!display || typeof display !== "object" || Array.isArray(display)) {
    return undefined;
  }
  const params = (display as Record<string, unknown>).labelParams;
  if (!params || typeof params !== "object" || Array.isArray(params)) {
    return undefined;
  }
  return String((params as Record<string, unknown>).model || "") || undefined;
}

export function startAiDebugRun(
  streamId: string,
  request: AiStreamRequest,
  context: { turnId?: string; conversationId?: number } = {},
): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  const sanitized = sanitizeValue(request) as Record<string, unknown>;
  delete sanitized.apiKey;
  delete sanitized.messages;
  delete sanitized.streamId;
  const messages = (sanitizeValue(request.messages) ?? []) as AiDebugMessage[];
  const run: AiDebugRun = {
    id: streamId,
    turnId: context.turnId,
    sessionId: request.sessionId,
    conversationId: context.conversationId,
    source: sourceLabel(streamId),
    taskType: initialTaskType(streamId, request),
    status: "starting",
    startedAt: now,
    updatedAt: now,
    request: { messages, meta: sanitized },
    model: request.options?.model,
    output: "",
    commentary: "",
    modelCalls: [],
    tools: [],
    events: [{
      id: ++eventSequence,
      at: now,
      type: "request",
      label: `开始 ${sourceLabel(streamId)}`,
      payload: { messages, ...sanitized },
    }],
    eventCount: 1,
    delegations: [],
    childRuns: [],
    approvals: [],
  };
  const withoutSameId = state.runs.filter((item) => item.id !== streamId);
  setState({
    runs: retainRecentTurns([run, ...withoutSameId]),
    selectedRunId: streamId,
  });
}

function persistedRunStatus(
  status: AiAgentRunSnapshot['run']['status'],
): AiDebugRunStatus {
  if (status === 'done') return 'completed';
  if (status === 'canceled') return 'aborted';
  if (status === 'failed' || status === 'blocked') return 'failed';
  return 'preparing';
}

function persistedTimestamp(value: string | null | undefined, fallback: number): number {
  if (!value) return fallback;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
    ? value
    : `${value.replace(' ', 'T')}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : fallback;
}

function persistedDebugRunId(runId: string): string {
  return `screenplay-${runId}`;
}

/** Rebuild a debug entry from the canonical persisted Run event stream. */
export function hydrateAiDebugRunSnapshot(data: {
  snapshot: AiAgentRunSnapshot;
  prompt: string;
  source?: string;
  turnId?: string;
}): void {
  if (!DEBUG_STORE_ENABLED) return;
  const { snapshot } = data;
  const runId = String(snapshot.run.runId || '').trim();
  if (!runId) return;
  const debugRunId = persistedDebugRunId(runId);
  const existing = state.runs.find(
    (run) => run.agentRunId === runId || run.id === debugRunId,
  );

  if (!existing || existing.persistedEventCursor == null) {
    const now = Date.now();
    const startedAt = persistedTimestamp(snapshot.run.createdAt, now);
    const source = String(data.source || '').trim() || 'Agent 历史恢复';
    const runRole = String(
      snapshot.run.lineage.agentTitle || snapshot.run.lineage.agentRole || '',
    ).trim();
    const run: AiDebugRun = {
      id: debugRunId,
      turnId: data.turnId,
      sessionId: snapshot.run.sessionId ?? undefined,
      conversationId: snapshot.run.conversationId ?? undefined,
      source,
      taskType: runRole
        ? `持久化 Agent Run · ${runRole}`
        : snapshot.run.lineage.depth > 0
          ? '持久化子 Run'
          : '持久化主 Run',
      status: persistedRunStatus(snapshot.run.status),
      startedAt,
      updatedAt: persistedTimestamp(snapshot.run.updatedAt, now),
      finishedAt: snapshot.run.status === 'running'
        ? undefined
        : persistedTimestamp(snapshot.run.updatedAt, now),
      request: {
        messages: data.prompt.trim()
          ? [{ role: 'user', content: data.prompt }]
          : [],
        meta: { recovered: true },
      },
      model: snapshot.run.provenance.modelName ?? undefined,
      // Preserve Provider-authored public text already observed before detach.
      output: existing?.output ?? '',
      commentary: existing?.commentary ?? '',
      modelCalls: [],
      tools: [],
      events: [{
        id: ++eventSequence,
        at: startedAt,
        type: 'recovered',
        label: '从持久化事件恢复 Agent Run',
        payload: { runId },
      }],
      eventCount: 1,
      agentRunId: runId,
      agentPlan: snapshot.todos.length
        ? { status: snapshot.run.status, steps: snapshot.todos }
        : undefined,
      delegations: snapshot.delegations.items.map((item) => sanitizeValue(item)),
      childRuns: [],
      approvals: [],
      persistedEventCursor: 0,
    };
    // Snapshot monitoring starts only after the live stream is detached. At
    // that boundary the persisted event stream is authoritative, so replace
    // any partial live debug copy instead of merging and duplicating events.
    const withoutSameRun = state.runs.filter(
      (item) => item.id !== debugRunId && item.agentRunId !== runId,
    );
    setState({
      runs: retainRecentTurns([run, ...withoutSameRun]),
      selectedRunId: debugRunId,
    });
  }

  const currentCursor = state.runs.find(
    (run) => run.agentRunId === runId || run.id === debugRunId,
  )?.persistedEventCursor ?? 0;
  let nextCursor = currentCursor;
  for (const event of [...snapshot.events].sort((left, right) => (
    left.cursor - right.cursor
  ))) {
    if (event.cursor <= currentCursor) continue;
    if (event.chunk) {
      recordAiDebugRunContinuation(runId, event.chunk as AiDebugChunk);
    }
    nextCursor = Math.max(nextCursor, event.cursor);
  }
  replaceRunByAgentRunId(runId, (run) => {
    const terminal = snapshot.run.status !== 'running' && !snapshot.hasMore;
    return {
      ...run,
      turnId: data.turnId ?? run.turnId,
      sessionId: snapshot.run.sessionId ?? run.sessionId,
      conversationId: snapshot.run.conversationId ?? run.conversationId,
      model: snapshot.run.provenance.modelName ?? run.model,
      status: terminal ? persistedRunStatus(snapshot.run.status) : run.status,
      updatedAt: persistedTimestamp(snapshot.run.updatedAt, run.updatedAt),
      finishedAt: terminal
        ? persistedTimestamp(snapshot.run.updatedAt, run.updatedAt)
        : run.finishedAt,
      persistedEventCursor: Math.max(nextCursor, snapshot.nextCursor),
    };
  });
}

export function recordAiDebugChunk(streamId: string, chunk: AiDebugChunk): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  replaceRun(streamId, (run) => {
    const status = nextStatus(run, chunk);
    const terminal = TERMINAL_STATUSES.has(status);
    const runId = getAgentRunId(chunk);
    const canonicalEvent = isCanonicalOutputEvent(chunk) ? chunk : null;
    const delta = canonicalEvent?.kind === "provider.content_delta"
      ? String(canonicalEvent.payload.delta || "")
      : "";
    const runtimeData = canonicalRuntimeData(canonicalEvent);
    const delegation = canonicalEvent?.kind === "delegation.event"
      ? canonicalEvent.payload
      : null;
    const approval = runtimeData?.eventType.startsWith("approval.")
      ? { eventType: runtimeData.eventType, ...runtimeData.data }
      : null;
    return {
      ...run,
      status,
      taskType: updatedTaskType(run, chunk),
      updatedAt: now,
      finishedAt: terminal ? run.finishedAt ?? now : run.finishedAt,
      model: canonicalModel(canonicalEvent) || chunk.model || run.model,
      output: run.output + (canonicalEvent?.channel === "final" ? delta : ""),
      commentary: run.commentary + (
        canonicalEvent?.channel === "commentary" ? delta : ""
      ),
      modelCalls: appendModelCall(run, chunk, now),
      tools: upsertTools(run, chunk, now),
      events: appendEvent(run, chunk, now),
      eventCount: run.eventCount + 1,
      contextBudget: runtimeData?.eventType.startsWith("context.")
        ? { ...(run.contextBudget as Record<string, unknown> | undefined), ...runtimeData.data }
        : run.contextBudget,
      contextCompaction: runtimeData?.eventType.startsWith("conversation.compaction.")
        ? runtimeData.data
        : run.contextCompaction,
      agentRunId: runId || run.agentRunId,
      agentPlan: runtimeData?.eventType.startsWith("run.todo")
        ? runtimeData.data
        : run.agentPlan,
      delegations: delegation
        ? upsertDebugDelegation(run.delegations, delegation)
        : run.delegations,
      childRuns: updateChildRuns(run, chunk, now),
      approvals: approval ? [...run.approvals, sanitizeValue(approval)] : run.approvals,
      error: chunk.error || chunk.errorReport?.errorMessage || run.error,
      errorReport: chunk.errorReport ?? run.errorReport,
    };
  });
}

/** Feed the screenplay persisted SSE into the same live diagnostic store. */
export function recordScreenplayAiDebugChunk(data: {
  runId: string;
  turnId: string;
  sessionId: number;
  prompt: string;
  model?: string;
  chunk: AiDebugChunk;
}): void {
  if (!DEBUG_STORE_ENABLED) return;
  const existing = state.runs.find((run) => run.agentRunId === data.runId);
  const streamId = existing?.id ?? persistedDebugRunId(data.runId);
  if (!existing) {
    startAiDebugRun(streamId, {
      streamId,
      apiKey: "",
      sessionId: data.sessionId,
      messages: data.prompt.trim()
        ? [{ role: "user", content: data.prompt }]
        : [],
      options: { model: data.model },
      chatAgentMode: "agent",
    }, {
      turnId: data.turnId,
    });
  }
  recordAiDebugChunk(streamId, data.chunk);
}

/**
 * Attach durable child-Run activity to the debug entry that dispatched it.
 * The root Run remains the report identity while model/tool evidence comes
 * from the workflow's real execution Runs.
 */
export function recordAiDebugRunContinuation(
  rootAgentRunId: string,
  chunk: AiDebugChunk,
): void {
  if (!DEBUG_STORE_ENABLED || !rootAgentRunId) return;
  const now = Date.now();
  replaceRunByAgentRunId(rootAgentRunId, (run) => {
    const status = FINAL_STATUSES.has(run.status)
      ? run.status
      : chunk.aborted
        ? "aborted"
        : chunk.error
          ? "failed"
          : chunk.done
            ? "completed"
            : nextStatus(
                run.status === "dispatched" ? { ...run, status: "preparing" } : run,
                chunk,
              );
    const terminal = TERMINAL_STATUSES.has(status);
    const canonicalEvent = isCanonicalOutputEvent(chunk) ? chunk : null;
    const delta = canonicalEvent?.kind === "provider.content_delta"
      ? String(canonicalEvent.payload.delta || "")
      : "";
    const runtimeData = canonicalRuntimeData(canonicalEvent);
    const delegation = canonicalEvent?.kind === "delegation.event"
      ? canonicalEvent.payload
      : null;
    const approval = runtimeData?.eventType.startsWith("approval.")
      ? { eventType: runtimeData.eventType, ...runtimeData.data }
      : null;
    return {
      ...run,
      status,
      taskType: updatedTaskType(run, chunk),
      updatedAt: now,
      finishedAt: terminal ? run.finishedAt ?? now : undefined,
      model: canonicalModel(canonicalEvent) || chunk.model || run.model,
      output: run.output + (canonicalEvent?.channel === "final" ? delta : ""),
      commentary: run.commentary + (
        canonicalEvent?.channel === "commentary" ? delta : ""
      ),
      modelCalls: appendModelCall(run, chunk, now),
      tools: upsertTools(run, chunk, now),
      events: appendEvent(run, chunk, now),
      eventCount: run.eventCount + 1,
      contextBudget: runtimeData?.eventType.startsWith("context.")
        ? { ...(run.contextBudget as Record<string, unknown> | undefined), ...runtimeData.data }
        : run.contextBudget,
      contextCompaction: runtimeData?.eventType.startsWith("conversation.compaction.")
        ? runtimeData.data
        : run.contextCompaction,
      // Never replace the orchestration root with the current child Run id.
      agentRunId: rootAgentRunId,
      agentPlan: runtimeData?.eventType.startsWith("run.todo")
        ? runtimeData.data
        : run.agentPlan,
      delegations: delegation
        ? upsertDebugDelegation(run.delegations, delegation)
        : run.delegations,
      childRuns: updateChildRuns(run, chunk, now),
      approvals: approval ? [...run.approvals, sanitizeValue(approval)] : run.approvals,
      error: chunk.error || chunk.errorReport?.errorMessage || run.error,
      errorReport: chunk.errorReport ?? run.errorReport,
    };
  });
}

export function recordAiDebugErrorReportStatus(
  streamId: string,
  status: AiErrorReportStatus,
  userNote?: string,
): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  replaceRun(streamId, (run) => {
    if (!run.errorReport) return run;
    return {
      ...run,
      updatedAt: now,
      errorReport: {
        ...run.errorReport,
        status,
        userNote: userNote || run.errorReport.userNote,
        updateTime: new Date(now).toISOString(),
      },
      events: [...run.events, {
        id: ++eventSequence,
        at: now,
        type: "error_report",
        label: status === "submitted" ? "错误报告已加入待排查" : "错误报告状态已更新",
        payload: {
          reportId: run.errorReport.id,
          status,
        },
      }].slice(-MAX_EVENTS_PER_RUN),
      eventCount: run.eventCount + 1,
    };
  });
}

export function markAiDebugAbortRequested(streamId: string | null): void {
  if (!DEBUG_STORE_ENABLED || !streamId) return;
  const now = Date.now();
  replaceRun(streamId, (run) => ({
    ...run,
    abortRequested: true,
    updatedAt: now,
    events: [...run.events, {
      id: ++eventSequence,
      at: now,
      type: "abort_requested",
      label: "用户请求中止",
    }].slice(-MAX_EVENTS_PER_RUN),
    eventCount: run.eventCount + 1,
  }));
}

export function recordAiDebugConversationSaved(data: {
  conversationId: number;
  sessionId: number;
  agentRunId?: string;
  prompt: string;
  response: string;
}): void {
  if (!DEBUG_STORE_ENABLED) return;
  const prompt = data.prompt.trim();
  const response = data.response.trim();
  const index = state.runs.findIndex((run) => {
    if (run.conversationId != null || run.sessionId !== data.sessionId) return false;
    if (data.agentRunId && run.agentRunId === data.agentRunId) return true;
    const latestUserMessage = [...run.request.messages]
      .reverse()
      .find((message) => message.role === "user");
    const requestPrompt =
      typeof latestUserMessage?.content === "string"
        ? latestUserMessage.content.trim()
        : "";
    return (
      requestPrompt === prompt &&
      (!response || !run.output.trim() || run.output.trim() === response)
    );
  });
  if (index < 0) return;
  const now = Date.now();
  const nextRuns = [...state.runs];
  const run = nextRuns[index];
  nextRuns[index] = {
    ...run,
    conversationId: data.conversationId,
    updatedAt: now,
    events: [...run.events, {
      id: ++eventSequence,
      at: now,
      type: "persisted",
      label: `对话已保存 · #${data.conversationId}`,
      payload: {
        conversationId: data.conversationId,
        sessionId: data.sessionId,
      },
    }].slice(-MAX_EVENTS_PER_RUN),
    eventCount: run.eventCount + 1,
  };
  setState({ ...state, runs: nextRuns });
}

export function subscribeAiDebugStore(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAiDebugSnapshot(): AiDebugState {
  return state;
}

export function selectAiDebugRun(runId: string): void {
  if (!state.runs.some((run) => run.id === runId)) return;
  setState({ ...state, selectedRunId: runId });
}

export function clearAiDebugRuns(): void {
  setState({ runs: [], selectedRunId: null });
}
