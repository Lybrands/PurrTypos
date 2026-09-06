import type {
  AiAgentRunSnapshot,
  AiErrorReport,
  AiErrorReportStatus,
  ElectronAPI,
} from "../../types";
import {
  canonicalProviderTextDelta,
  isCanonicalOutputEvent,
  type CanonicalOutputEvent,
} from "../../agent-runtime/canonicalOutput.ts";
import { resolveLocalizedToolDisplayName } from "../AgentConversation/toolCallLabels.ts";

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
  displayName?: string;
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

export interface AiDebugTokenUsage {
  inputTokens: number;
  generationTokens: number;
  reasoningTokens: number | null;
  totalTokens: number;
  unreportedAttempts: number;
  unreportedReasoningAttempts: number;
  modelAttempts: number;
  complete: boolean;
}

export interface AiDebugMessage {
  role: string;
  content: unknown;
  tool_calls?: unknown[];
  reasoning_content?: string;
  tool_call_id?: string;
}

export interface AiDebugDelegationActivity {
  id: string;
  delegationId: string;
  agentName: string;
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
  conversationRootRunId?: string;
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
  tokenUsage?: AiDebugTokenUsage;
  contextBudget?: unknown;
  contextCompaction?: unknown;
  agentRunId?: string;
  agentPlan?: unknown;
  delegations: unknown[];
  delegationActivities: AiDebugDelegationActivity[];
  approvals: unknown[];
  error?: string;
  errorReport?: AiErrorReport;
  abortRequested?: boolean;
  providerOutputEvents?: number;
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

export interface AiDebugConversationLifecycle {
  status: AiDebugRunStatus;
  ended: boolean;
  endReason: string;
  authoritativeRunId?: string;
}

/** Root execution identity for one turn, if the journal has established it. */
export function aiDebugTurnRootRunId(
  group: AiDebugTurnGroup | undefined,
): string | undefined {
  if (!group) return undefined;
  const declaredRoots = [...new Set(
    group.runs
      .map((run) => String(run.conversationRootRunId || "").trim())
      .filter(Boolean),
  )];
  if (declaredRoots.length === 1) return declaredRoots[0];
  if (declaredRoots.length > 1) return undefined;
  if (group.runs.length === 1) {
    return String(group.runs[0].agentRunId || "").trim() || undefined;
  }
  return undefined;
}

/** Stable identity for one user turn; never falls back to an arbitrary child Run. */
export function aiDebugTurnDiagnosticId(
  group: AiDebugTurnGroup | undefined,
): string {
  if (!group) return "idle";
  const rootRunId = aiDebugTurnRootRunId(group);
  if (rootRunId) return rootRunId;
  if (group.runs.length === 1) {
    const run = group.runs[0];
    return run.turnId || run.id;
  }
  const turnIds = [...new Set(
    group.runs.map((run) => String(run.turnId || "").trim()).filter(Boolean),
  )];
  return turnIds.length === 1 ? turnIds[0] : group.key;
}

const MAX_TURNS = 20;
const MAX_EVENTS_PER_RUN = 200;
const SENSITIVE_KEY =
  /^(api[-_]?key|authorization|password|passwd|secret|access[-_]?token|refresh[-_]?token|token)$/i;
const FINAL_STATUSES = new Set<AiDebugRunStatus>([
  "completed",
  "aborted",
  "failed",
]);
const DEBUG_STORE_ENABLED = import.meta.env?.DEV !== false;

export function isAiDebugRunActive(run: AiDebugRun | undefined): boolean {
  return Boolean(run && !FINAL_STATUSES.has(run.status));
}

export function aiDebugCurrentRunId(runs: readonly AiDebugRun[]): string | undefined {
  return [...runs]
    .filter(isAiDebugRunActive)
    .sort((left, right) => (
      right.startedAt - left.startedAt
      || right.updatedAt - left.updatedAt
      || right.id.localeCompare(left.id)
    ))[0]?.id;
}

export function aiDebugTurnTokenUsage(
  runs: readonly AiDebugRun[],
): AiDebugTokenUsage | undefined {
  const usages = runs.flatMap((run) => run.tokenUsage ? [run.tokenUsage] : []);
  if (!usages.length) return undefined;
  const total = usages.reduce<AiDebugTokenUsage>((sum, usage) => ({
    inputTokens: sum.inputTokens + usage.inputTokens,
    generationTokens: sum.generationTokens + usage.generationTokens,
    reasoningTokens: (
      sum.reasoningTokens === null || usage.reasoningTokens === null
        ? null
        : sum.reasoningTokens + usage.reasoningTokens
    ),
    totalTokens: sum.totalTokens + usage.totalTokens,
    unreportedAttempts: sum.unreportedAttempts + usage.unreportedAttempts,
    unreportedReasoningAttempts: (
      sum.unreportedReasoningAttempts + usage.unreportedReasoningAttempts
    ),
    modelAttempts: sum.modelAttempts + usage.modelAttempts,
    complete: sum.complete && usage.complete,
  }), {
    inputTokens: 0,
    generationTokens: 0,
    reasoningTokens: 0,
    totalTokens: 0,
    unreportedAttempts: 0,
    unreportedReasoningAttempts: 0,
    modelAttempts: 0,
    complete: true,
  });
  const everyRunSettled = runs.every((run) => {
    if (isAiDebugRunActive(run)) return false;
    const observedModelCalls = run.modelCalls.reduce(
      (count, call) => count + call.count,
      0,
    );
    return observedModelCalls === 0 || run.tokenUsage?.complete === true;
  });
  return { ...total, complete: total.complete && everyRunSettled };
}

let state: AiDebugState = {
  runs: [],
  selectedRunId: null,
};
let eventSequence = 0;
let notifyScheduled = false;
const listeners = new Set<() => void>();
const visibilityListeners = new Set<() => void>();
let inspectorVisible = false;

export function setAiDebugInspectorVisible(visible: boolean): void {
  if (!DEBUG_STORE_ENABLED || inspectorVisible === visible) return;
  inspectorVisible = visible;
  visibilityListeners.forEach(listener => listener());
}

export function getAiDebugInspectorVisible(): boolean {
  return inspectorVisible;
}

export function subscribeAiDebugInspectorVisibility(listener: () => void): () => void {
  visibilityListeners.add(listener);
  return () => visibilityListeners.delete(listener);
}

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
    .map((group) => {
      const runs = [...group.runs].sort((left, right) => right.startedAt - left.startedAt);
      const rootRunId = aiDebugTurnRootRunId({ ...group, runs });
      const rootRun = rootRunId
        ? runs.find((run) => run.agentRunId === rootRunId)
        : undefined;
      return {
        ...group,
        runs,
        sessionId: rootRun?.sessionId ?? group.sessionId,
        conversationId: rootRun?.conversationId ?? group.conversationId,
        source: rootRun?.source || group.source,
        prompt: (rootRun && latestUserPrompt(rootRun)) || group.prompt,
      };
    })
    .sort((left, right) => right.updatedAt - left.updatedAt);
}

function isFinalRun(run: AiDebugRun): boolean {
  return FINAL_STATUSES.has(run.status);
}

/** Project one user turn from its authoritative root Run, not a child model Run. */
export function aiDebugConversationLifecycle(
  group: AiDebugTurnGroup,
): AiDebugConversationLifecycle {
  const declaredRootRunId = group.runs.find(
    (run) => run.conversationRootRunId,
  )?.conversationRootRunId;
  const explicitRoot = group.runs.find((run) => (
    Boolean(run.conversationRootRunId)
    && run.agentRunId === run.conversationRootRunId
  ));
  if (declaredRootRunId && !explicitRoot) {
    const activeRun = group.runs.find((run) => !isFinalRun(run));
    return {
      status: activeRun?.status ?? "preparing",
      ended: false,
      endReason: "尚未结束",
      authoritativeRunId: declaredRootRunId,
    };
  }
  const authoritative = explicitRoot
    ?? group.runs.find((run) => !isFinalRun(run))
    ?? group.runs.find((run) => run.status === "failed")
    ?? group.runs.find((run) => run.status === "aborted")
    ?? group.runs[0];
  if (!authoritative) {
    return { status: "starting", ended: false, endReason: "尚未结束" };
  }
  if (!isFinalRun(authoritative)) {
    return {
      status: authoritative.status,
      ended: false,
      endReason: "尚未结束",
      authoritativeRunId: authoritative.agentRunId || authoritative.id,
    };
  }
  return {
    status: authoritative.status,
    ended: true,
    endReason: authoritative.status === "completed"
      ? "正常完成"
      : authoritative.error || (authoritative.status === "aborted" ? "已中止" : "执行失败"),
    authoritativeRunId: authoritative.agentRunId || authoritative.id,
  };
}

function retainRecentTurns(runs: AiDebugRun[]): AiDebugRun[] {
  const retainedKeys = new Set(
    groupAiDebugRunsByTurn(runs).slice(0, MAX_TURNS).map((group) => group.key),
  );
  return runs.filter((run) => retainedKeys.has(aiDebugTurnKey(run)));
}

function scheduleNotify(): void {
  if (notifyScheduled || listeners.size === 0) return;
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
    if (canonicalProviderTextDelta(chunk)) {
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
      return { type: "delegation", label: "Agent 委派事件" };
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
  if (successfulTerminal(chunk)) return { type: "done", label: "本轮完成" };
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
  const resultStatus = String(chunk.runResult?.status || "");
  if (resultStatus === "done") return "completed";
  if (resultStatus === "failed" || resultStatus === "blocked") return "failed";
  if (resultStatus === "canceled") return "aborted";
  if (isCanonicalOutputEvent(chunk)) {
    if (chunk.kind === "run.lifecycle") {
      const status = String(chunk.payload.status || "");
      if (status === "done") return "completed";
      if (status === "failed" || status === "blocked") return "failed";
      if (status === "canceled") return "aborted";
    }
    if (canonicalProviderTextDelta(chunk)) {
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

function successfulTerminal(chunk: AiDebugChunk): boolean {
  if (String(chunk.runResult?.status || "") === "done") return true;
  return isCanonicalOutputEvent(chunk)
    && chunk.kind === "run.lifecycle"
    && String(chunk.payload.status || "") === "done";
}

function runTerminalError(chunk: AiDebugChunk): string | undefined {
  if (successfulTerminal(chunk)) return undefined;
  const runResultStatus = String(chunk.runResult?.status || "");
  if (["failed", "blocked", "canceled"].includes(runResultStatus)) {
    const errorCode = String(chunk.runResult?.errorCode || "").trim();
    if (errorCode) return errorCode;
  }
  if (isCanonicalOutputEvent(chunk) && chunk.kind === "run.lifecycle") {
    const status = String(chunk.payload.status || "");
    if (["failed", "blocked", "canceled"].includes(status)) {
      const errorCode = String(
        chunk.payload.errorCode || chunk.payload.reasonCode || chunk.payload.error || "",
      ).trim();
      if (errorCode) return errorCode;
    }
  }
  return chunk.error || chunk.errorReport?.errorMessage || undefined;
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
      const rawDisplayNames = params?.displayNames;
      const displayNames = rawDisplayNames
        && typeof rawDisplayNames === "object"
        && !Array.isArray(rawDisplayNames)
        ? Object.fromEntries(
          Object.entries(rawDisplayNames as Record<string, unknown>)
            .filter((entry): entry is [string, string] => typeof entry[1] === "string"),
        )
        : undefined;
      tools = [...tools, {
        id: operationId,
        batchIndex: tools.length,
        index: 0,
        name: String(params?.toolName || "工具操作"),
        displayName: resolveLocalizedToolDisplayName(displayNames),
        argumentsText: "",
        argumentsValue: undefined,
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
  agentName: string;
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
  const agentName = String(raw.agentName || "").trim();
  if (!delegationId || !agentName) return null;
  const parsedAttempt = Number(raw.attempt ?? input.attempt);
  return {
    delegationId,
    agentName,
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

function updateDelegationActivities(
  run: AiDebugRun,
  chunk: AiDebugChunk,
  now: number,
): AiDebugDelegationActivity[] {
  if (
    !isCanonicalOutputEvent(chunk)
    || chunk.kind !== "delegation.event"
  ) return run.delegationActivities;
  const metadata = delegationMetadata(chunk.payload);
  if (!metadata) return run.delegationActivities;
  const index = run.delegationActivities.findIndex(
    (item) => item.delegationId === metadata.delegationId,
  );
  const existing = index >= 0 ? run.delegationActivities[index] : undefined;
  const base: AiDebugDelegationActivity = existing ?? {
    id: `${run.id}-delegation-${metadata.delegationId}`,
    delegationId: metadata.delegationId,
    agentName: metadata.agentName,
    agentTitle: metadata.agentTitle,
    objective: metadata.objective,
    unitId: metadata.unitId,
    attempt: metadata.attempt,
    taskType: "委派 Agent",
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
    agentName: metadata.agentName || base.agentName,
    agentTitle: metadata.agentTitle || base.agentTitle,
    objective: metadata.objective || base.objective,
    unitId: metadata.unitId || base.unitId,
    attempt: metadata.attempt || base.attempt,
    updatedAt: now,
  };
  const status = delegationStatus(metadata.status, next.status);
  next = {
    ...next,
    status,
    finishedAt: FINAL_STATUSES.has(status)
      ? next.finishedAt ?? now
      : next.finishedAt,
    error: metadata.error || next.error,
  };
  if (index < 0) return [...run.delegationActivities, next];
  return run.delegationActivities.map((item, itemIndex) => itemIndex === index ? next : item);
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

function nonNegativeUsageInteger(value: unknown): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

function nullableUsageInteger(value: unknown): number | null {
  return value === null || value === undefined
    ? null
    : nonNegativeUsageInteger(value);
}

function persistedTokenUsage(
  activity: AiAgentRunSnapshot['run']['activity'],
  complete: boolean,
): AiDebugTokenUsage | undefined {
  const usage = activity?.usage;
  if (!usage) return undefined;
  const inputTokens = nonNegativeUsageInteger(usage.inputTokens);
  const generationTokens = nonNegativeUsageInteger(usage.generationTokens);
  return {
    inputTokens,
    generationTokens,
    reasoningTokens: nullableUsageInteger(usage.reasoningTokens),
    totalTokens: nonNegativeUsageInteger(usage.totalTokens) || inputTokens + generationTokens,
    unreportedAttempts: nonNegativeUsageInteger(usage.unreportedAttempts),
    unreportedReasoningAttempts: nonNegativeUsageInteger(
      usage.unreportedReasoningAttempts,
    ),
    modelAttempts: nonNegativeUsageInteger(activity?.modelAttemptCount),
    complete,
  };
}

function runtimeTokenUsage(
  current: AiDebugTokenUsage | undefined,
  runtimeData: ReturnType<typeof canonicalRuntimeData>,
  modelAttempts: number,
): AiDebugTokenUsage | undefined {
  if (current?.complete || runtimeData?.eventType !== 'context.usage_recorded') {
    return current;
  }
  const inputTokens = nonNegativeUsageInteger(runtimeData.data.actualInputTokens);
  const generationTokens = nonNegativeUsageInteger(
    runtimeData.data.actualGenerationTokens,
  );
  return {
    inputTokens,
    generationTokens,
    reasoningTokens: nullableUsageInteger(runtimeData.data.reasoningTokens),
    totalTokens: nonNegativeUsageInteger(runtimeData.data.actualTotalTokens)
      || inputTokens + generationTokens,
    unreportedAttempts: 0,
    unreportedReasoningAttempts: (
      runtimeData.data.reasoningTokens === null
      || runtimeData.data.reasoningTokens === undefined
        ? 1
        : 0
    ),
    modelAttempts,
    complete: false,
  };
}

function mergeDebugAgentPlan(
  current: unknown,
  runtimeData: ReturnType<typeof canonicalRuntimeData>,
): unknown {
  if (!runtimeData) return current;
  if (runtimeData.eventType === 'run.todos_updated') return runtimeData.data;
  if (runtimeData.eventType !== 'run.todo_updated') return current;
  if (!current || typeof current !== 'object' || Array.isArray(current)) return current;
  const plan = current as Record<string, unknown>;
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const step = runtimeData.data.step;
  if (!step || typeof step !== 'object' || Array.isArray(step)) return current;
  const update = step as Record<string, unknown>;
  const stepId = String(runtimeData.data.step_id || update.id || '');
  return {
    ...plan,
    steps: steps.map((item) => (
      item && typeof item === 'object' && !Array.isArray(item)
        && String((item as Record<string, unknown>).id || '') === stepId
        ? { ...(item as Record<string, unknown>), ...update }
        : item
    )),
  };
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
  context: {
    turnId?: string;
    conversationId?: number;
    conversationRootRunId?: string;
    source?: string;
    openInspector?: boolean;
  } = {},
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
    conversationRootRunId: context.conversationRootRunId,
    sessionId: request.sessionId,
    conversationId: context.conversationId,
    source: context.source || sourceLabel(streamId),
    taskType: context.source ? `${context.source}任务` : initialTaskType(streamId, request),
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
      label: `开始 ${context.source || sourceLabel(streamId)}`,
      payload: { messages, ...sanitized },
    }],
    eventCount: 1,
    delegations: [],
    delegationActivities: [],
    approvals: [],
  };
  const withoutSameId = state.runs.filter((item) => item.id !== streamId);
  setState({
    runs: retainRecentTurns([run, ...withoutSameId]),
    selectedRunId: streamId,
  });
  if (context.openInspector !== false) setAiDebugInspectorVisible(true);
}

function persistedTimestamp(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
    ? `${value.replace(' ', 'T')}Z` : value;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

/** Apply durable counters and timestamps, independently of browser replay speed. */
export function recordAiDebugRunUsageSnapshot(
  snapshot: AiAgentRunSnapshot,
): void {
  if (!DEBUG_STORE_ENABLED) return;
  const runId = String(snapshot.run.runId || '').trim();
  if (!runId) return;
  const usage = persistedTokenUsage(
    snapshot.run.activity,
    snapshot.run.status !== 'running',
  );
  const startedAt = persistedTimestamp(snapshot.run.createdAt);
  const updatedAt = persistedTimestamp(snapshot.run.updatedAt);
  const terminal = ['done', 'failed', 'canceled'].includes(snapshot.run.status);
  replaceRunByAgentRunId(runId, (run) => ({
    ...run, tokenUsage: usage ?? run.tokenUsage,
    model: run.model || snapshot.run.provenance.modelName || undefined,
    startedAt: startedAt ?? run.startedAt,
    updatedAt: updatedAt ?? run.updatedAt,
    finishedAt: terminal ? updatedAt ?? run.finishedAt : run.finishedAt,
  }));
}

export function recordAiDebugChunk(streamId: string, chunk: AiDebugChunk): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  replaceRun(streamId, (run) => {
    const status = nextStatus(run, chunk);
    const terminal = FINAL_STATUSES.has(status);
    const runId = getAgentRunId(chunk);
    const canonicalEvent = isCanonicalOutputEvent(chunk) ? chunk : null;
    const delta = canonicalEvent ? canonicalProviderTextDelta(canonicalEvent) : "";
    const runtimeData = canonicalRuntimeData(canonicalEvent);
    const succeeded = successfulTerminal(chunk);
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
      tokenUsage: runtimeTokenUsage(
        run.tokenUsage,
        runtimeData,
        run.modelCalls.reduce((count, call) => count + call.count, 0),
      ),
      contextBudget: runtimeData?.eventType.startsWith("context.")
        ? { ...(run.contextBudget as Record<string, unknown> | undefined), ...runtimeData.data }
        : run.contextBudget,
      contextCompaction: runtimeData?.eventType.startsWith("conversation.compaction.")
        ? runtimeData.data
        : run.contextCompaction,
      agentRunId: runId || run.agentRunId,
      agentPlan: mergeDebugAgentPlan(run.agentPlan, runtimeData),
      delegations: delegation
        ? upsertDebugDelegation(run.delegations, delegation)
        : run.delegations,
      delegationActivities: updateDelegationActivities(run, chunk, now),
      approvals: approval ? [...run.approvals, sanitizeValue(approval)] : run.approvals,
      error: succeeded ? undefined : runTerminalError(chunk) || run.error,
      errorReport: succeeded ? undefined : chunk.errorReport ?? run.errorReport,
    };
  });
}

/** Observe the conversation's existing SSE reader, without a diagnostic poller. */
export function recordAgentConversationDebugChunk(data: {
  runId: string;
  turnId?: string;
  conversationRootRunId?: string;
  sessionId?: number;
  source?: string;
  prompt: string;
  model?: string;
  chunk: AiDebugChunk;
}): void {
  if (!DEBUG_STORE_ENABLED) return;
  const existing = state.runs.find((run) => run.agentRunId === data.runId);
  const streamId = existing?.id ?? `agent-${data.runId}`;
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
      conversationRootRunId: data.conversationRootRunId,
      source: data.source || 'Agent 对话',
      openInspector: false,
    });
  } else if (
    data.conversationRootRunId
    && existing.conversationRootRunId !== data.conversationRootRunId
  ) {
    replaceRun(streamId, (run) => ({
      ...run,
      conversationRootRunId: data.conversationRootRunId,
    }));
  }
  recordAiDebugChunk(streamId, data.chunk);
  if (data.source && existing && existing.source !== data.source) {
    replaceRun(streamId, run => ({ ...run, source: data.source! }));
  }
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

export function clearAiDebugRuns(): void {
  setState({ runs: [], selectedRunId: null });
}
