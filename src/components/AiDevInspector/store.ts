import type {
  AiErrorReport,
  AiErrorReportStatus,
  ElectronAPI,
} from "../../types";

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
  thinking: string;
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
  thinking: string;
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
}

interface AiDebugState {
  runs: AiDebugRun[];
  selectedRunId: string | null;
}

const MAX_RUNS = 20;
const MAX_EVENTS_PER_RUN = 200;
const SENSITIVE_KEY =
  /^(api[-_]?key|authorization|password|passwd|secret|access[-_]?token|refresh[-_]?token|token)$/i;
const TERMINAL_STATUSES = new Set<AiDebugRunStatus>([
  "dispatched",
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

function parseArguments(value: string): unknown {
  if (!value.trim()) return {};
  try {
    return sanitizeValue(JSON.parse(value));
  } catch {
    return value;
  }
}

function parseToolResult(value: unknown): unknown {
  if (typeof value !== "string") return sanitizeValue(value);
  try {
    return sanitizeValue(JSON.parse(value));
  } catch {
    return value;
  }
}

function toolResultFailure(value: unknown): {
  errorCode?: string;
  errorMessage?: string;
  diagnostics?: Record<string, unknown>;
} | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const result = value as Record<string, unknown>;
  const errorCode = String(result.errorCode ?? result.error_code ?? "").trim();
  const explicitError = String(result.error ?? "").trim();
  const failed = result.success === false || Boolean(errorCode) || Boolean(explicitError);
  if (!failed) return null;
  const errorMessage = explicitError || String(result.message ?? "").trim();
  const diagnostics =
    result.diagnostics &&
    typeof result.diagnostics === "object" &&
    !Array.isArray(result.diagnostics)
      ? result.diagnostics as Record<string, unknown>
      : undefined;
  return {
    errorCode: errorCode || undefined,
    errorMessage: errorMessage || undefined,
    diagnostics,
  };
}

function sourceLabel(streamId: string, request: AiStreamRequest): string {
  if (request.agentProfile === "screenplay" || streamId.startsWith("screenplay-")) {
    return "剧本 Agent";
  }
  if (streamId.startsWith("chat-")) return "主对话";
  if (streamId.startsWith("inline-edit-")) return "行内改写";
  if (streamId.startsWith("editor-float-")) return "编辑器改写";
  if (streamId.startsWith("ghost-completion-")) return "幽灵补全";
  return "AI 对话";
}

const SCREENPLAY_STAGE_LABELS: Record<string, string> = {
  orientation: "原作分析",
  brief: "创作简报",
  structure: "剧本结构",
  scenes: "场景表",
  draft: "场景正文",
  review: "剧本审阅",
  completed: "已完成项目",
};

function initialTaskType(streamId: string, request: AiStreamRequest): string {
  if (request.agentProfile === "screenplay") {
    if (request.screenplayTaskIntent === "stage_deliverable") {
      const stage = SCREENPLAY_STAGE_LABELS[String(request.activeStage || "")]
        || String(request.activeStage || "当前阶段");
      return `剧本阶段交付 · ${stage}`;
    }
    return "剧本自由对话";
  }
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
  if (chunk.taskAdmission?.mode === "durable") {
    return "持久化长任务 · 正在派发";
  }
  return run.taskType;
}

function compactEventPayload(chunk: AiDebugChunk): unknown {
  const payload = sanitizeValue(chunk) as Record<string, unknown>;
  for (const key of ["delta", "thinkingDelta", "partialContent", "partialThinking"]) {
    const value = payload[key];
    if (typeof value === "string" && value.length > 1_200) {
      payload[key] = `${value.slice(0, 1_200)}…`;
    }
  }
  const results = payload.toolResults;
  if (Array.isArray(results)) {
    payload.toolResults = results.map((item) => {
      if (!item || typeof item !== "object") return item;
      const next = { ...(item as Record<string, unknown>) };
      if (typeof next.content === "string" && next.content.length > 4_000) {
        next.content = `${next.content.slice(0, 4_000)}…`;
      }
      return next;
    });
  }
  return payload;
}

function chunkSummary(chunk: AiDebugChunk): { type: string; label: string } {
  if (chunk.error) return { type: "error", label: chunk.error };
  if (chunk.done && chunk.errorReport) {
    return { type: "error", label: chunk.errorReport.errorMessage };
  }
  if (chunk.aborted) return { type: "aborted", label: "流已中止" };
  if (chunk.done) return { type: "done", label: "本轮完成" };
  if (chunk.toolApprovalRequired) {
    return { type: "approval", label: "工具等待批准" };
  }
  if (chunk.toolApprovalResolved) {
    return { type: "approval", label: `工具审批：${chunk.toolApprovalResolved.status}` };
  }
  if (chunk.toolCallsInProgress) {
    const names = (chunk.toolCalls ?? []).map((item) => item.function.name).filter(Boolean);
    return { type: "tool_start", label: `调用工具：${names.join("、") || "未知工具"}` };
  }
  if (chunk.toolResults?.length) {
    return { type: "tool_result", label: `收到 ${chunk.toolResults.length} 个工具结果` };
  }
  if (typeof chunk.toolIndexCompleted === "number") {
    return { type: "tool_complete", label: `第 ${chunk.toolIndexCompleted + 1} 个工具完成` };
  }
  if (chunk.agentRunStarted) return { type: "agent", label: "Agent Run 已开始" };
  if (chunk.agentRunTodosUpdated) return { type: "plan", label: "任务计划已生成" };
  if (chunk.agentRunTodoUpdated) return { type: "plan", label: "任务步骤已更新" };
  if (
    chunk.agentRunCompleted ||
    chunk.agentRunFailed ||
    chunk.agentRunBlocked ||
    chunk.agentRunCanceled
  ) {
    return { type: "agent", label: "Agent Run 状态已更新" };
  }
  if (chunk.agentDelegationCreated || chunk.agentDelegationUpdated) {
    return { type: "delegation", label: "子 Agent 状态已更新" };
  }
  if (chunk.contextCompaction) return { type: "context", label: "上下文压缩状态已更新" };
  if (chunk.contextBudget) return { type: "context", label: "上下文预算已更新" };
  if (chunk.longTaskDispatched) {
    return { type: "long_task", label: "持久化长任务已创建" };
  }
  if (chunk.taskAdmission) {
    return { type: "task_admission", label: `任务准入：${chunk.taskAdmission.mode}` };
  }
  if (chunk.modelInvocation) {
    const count = Math.max(1, Number(chunk.modelInvocation.count) || 1);
    const names = chunk.modelInvocation.toolNames?.filter(Boolean) ?? [];
    return {
      type: "model_call",
      label: `大模型调用 ×${count}${names.length ? ` · ${names.length} 个工具` : ""}`,
    };
  }
  if (chunk.thinkingDelta) return { type: "thinking", label: "收到思考增量" };
  if (chunk.delta) return { type: "response", label: "收到正文增量" };
  return { type: "event", label: "收到运行事件" };
}

function nextStatus(run: AiDebugRun, chunk: AiDebugChunk): AiDebugRunStatus {
  if (chunk.error) return "failed";
  if (chunk.done && chunk.errorReport && !chunk.aborted) return "failed";
  if (chunk.aborted) return "aborted";
  if (TERMINAL_STATUSES.has(run.status)) return run.status;
  if (chunk.done) {
    return run.taskType.startsWith("持久化长任务")
      ? "dispatched"
      : "completed";
  }
  if (chunk.toolApprovalRequired) return "awaiting_approval";
  if (chunk.toolCallsInProgress || chunk.toolResults?.length) return "tool";
  if (chunk.thinkingDelta) return "thinking";
  if (chunk.delta) return "responding";
  if (chunk.agentRunTodosUpdated || chunk.agentRunTodoUpdated || chunk.agentRunStarted) {
    return "planning";
  }
  if (chunk.contextBudget || chunk.contextCompaction) return "preparing";
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
  if (chunk.toolCallsInProgress && chunk.toolCalls?.length) {
    const batchIndex =
      tools.length === 0 ? 0 : Math.max(...tools.map((tool) => tool.batchIndex)) + 1;
    const nextTools = chunk.toolCalls.map((call, index): AiDebugTool => ({
      id: call.id || `${run.id}-${batchIndex}-${index}`,
      batchIndex,
      index,
      name: call.function.name || "未知工具",
      argumentsText: call.function.arguments || "{}",
      argumentsValue: parseArguments(call.function.arguments || "{}"),
      status: "running",
      cached: false,
      startedAt: now,
    }));
    tools = [...tools, ...nextTools];
  }

  if (chunk.toolResults?.length) {
    tools = tools.map((tool) => {
      const result = chunk.toolResults?.find(
        (item) =>
          item.tool_call_id === tool.id ||
          (!item.tool_call_id && item.name === tool.name && tool.status === "running"),
      );
      if (!result) return tool;
      const parsedResult = parseToolResult(result.content);
      const failure = toolResultFailure(parsedResult);
      return {
        ...tool,
        result: parsedResult,
        status: failure ? "failed" as const : tool.status,
        completedAt: failure ? tool.completedAt ?? now : tool.completedAt,
        errorCode: failure?.errorCode ?? tool.errorCode,
        errorMessage: failure?.errorMessage ?? tool.errorMessage,
        diagnostics: failure?.diagnostics ?? tool.diagnostics,
      };
    });
  }

  if (typeof chunk.toolIndexCompleted === "number") {
    const batchIndex =
      tools.length === 0 ? -1 : Math.max(...tools.map((tool) => tool.batchIndex));
    const failed = Boolean(
      chunk.toolErrorCode ||
      ["failed", "rejected", "canceled"].includes(chunk.toolOutcome || ""),
    );
    tools = tools.map((tool) => {
      const matches = chunk.toolCallId
        ? tool.id === chunk.toolCallId
        : tool.batchIndex === batchIndex && tool.index === chunk.toolIndexCompleted;
      if (!matches) return tool;
      return {
        ...tool,
        status: failed ? "failed" as const : "completed" as const,
        cached: chunk.toolFromCache === true,
        completedAt: now,
        outcome: chunk.toolOutcome ?? tool.outcome,
        errorCode: chunk.toolErrorCode ?? tool.errorCode,
        exceptionType: chunk.toolExceptionType ?? tool.exceptionType,
      };
    });
  }

  if (chunk.error) {
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
  const invocation = chunk.modelInvocation;
  if (!invocation) return run.modelCalls;
  const toolNames = [...new Set(
    (invocation.toolNames ?? [])
      .map((name) => String(name || "").trim())
      .filter(Boolean),
  )];
  return [...run.modelCalls, {
    id: `${run.id}-model-${run.modelCalls.length + 1}`,
    at: now,
    phase: String(invocation.phase || "generation"),
    count: Math.max(1, Number(invocation.count) || 1),
    toolNames,
    toolChoice: invocation.toolChoice,
    round: invocation.round,
    logicalRound: invocation.logicalRound,
    attempt: invocation.attempt,
    revision: invocation.revision,
    judgeIndex: invocation.judgeIndex,
    parameters: invocation.parameters
      ? sanitizeValue(invocation.parameters) as Record<string, unknown>
      : undefined,
  }];
}

function getAgentRunId(chunk: AiDebugChunk): string | undefined {
  return (
    chunk.agentRunStarted?.runId ||
    chunk.agentRunTodosUpdated?.runId ||
    chunk.agentRunTodoUpdated?.runId ||
    chunk.agentRunCompleted?.runId ||
    chunk.agentRunFailed?.runId ||
    chunk.agentRunBlocked?.runId ||
    chunk.agentRunCanceled?.runId ||
    chunk.agentDelegationCreated?.runId ||
    chunk.agentDelegationUpdated?.runId ||
    chunk.longTaskDispatched?.runId ||
    chunk.taskAdmission?.runId
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

function childChunkStatus(
  child: AiDebugChildRun,
  chunk: AiDebugChunk,
): AiDebugRunStatus {
  if (chunk.agentRunCompleted) return "completed";
  if (chunk.agentRunFailed || chunk.agentRunBlocked || chunk.error) return "failed";
  if (chunk.agentRunCanceled || chunk.aborted) return "aborted";
  return nextStatus(child as unknown as AiDebugRun, chunk);
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
  const envelope = chunk.agentSubRunEvent;
  const lifecycle = delegationMetadata(
    chunk.agentDelegationCreated ?? chunk.agentDelegationUpdated,
  );
  const metadata = envelope
    ? delegationMetadata({
        ...envelope,
        status: undefined,
      })
    : lifecycle;
  if (!metadata) return run.childRuns;
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
    thinking: "",
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
  if (envelope?.chunk) {
    const childChunk = envelope.chunk as AiDebugChunk;
    const status = childChunkStatus(next, childChunk);
    const terminal = TERMINAL_STATUSES.has(status);
    next = {
      ...next,
      status,
      finishedAt: terminal ? next.finishedAt ?? now : next.finishedAt,
      childRunId: envelope.childRunId || getAgentRunId(childChunk) || next.childRunId,
      model: childChunk.model || next.model,
      output: next.output + (childChunk.delta || ""),
      thinking: next.thinking + (childChunk.thinkingDelta || ""),
      modelCalls: appendModelCall(next as unknown as AiDebugRun, childChunk, now),
      tools: upsertTools(next as unknown as AiDebugRun, childChunk, now),
      events: appendEvent(next as unknown as AiDebugRun, childChunk, now),
      eventCount: next.eventCount + 1,
      contextBudget: childChunk.contextBudget
        ? {
            ...(next.contextBudget as Record<string, unknown> | undefined),
            ...childChunk.contextBudget,
          }
        : next.contextBudget,
      contextCompaction: childChunk.contextCompaction ?? next.contextCompaction,
      error: childChunk.error || childChunk.errorReport?.errorMessage || next.error,
    };
  } else if (lifecycle) {
    const status = delegationStatus(lifecycle.status, next.status);
    next = {
      ...next,
      status,
      finishedAt: TERMINAL_STATUSES.has(status)
        ? next.finishedAt ?? now
        : next.finishedAt,
      error: lifecycle.error || next.error,
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

export function startAiDebugRun(streamId: string, request: AiStreamRequest): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  const sanitized = sanitizeValue(request) as Record<string, unknown>;
  delete sanitized.apiKey;
  delete sanitized.messages;
  delete sanitized.streamId;
  const messages = (sanitizeValue(request.messages) ?? []) as AiDebugMessage[];
  const run: AiDebugRun = {
    id: streamId,
    sessionId: request.sessionId,
    source: sourceLabel(streamId, request),
    taskType: initialTaskType(streamId, request),
    status: "starting",
    startedAt: now,
    updatedAt: now,
    request: { messages, meta: sanitized },
    model: request.options?.model,
    output: "",
    thinking: "",
    modelCalls: [],
    tools: [],
    events: [{
      id: ++eventSequence,
      at: now,
      type: "request",
      label: `开始 ${sourceLabel(streamId, request)}`,
      payload: { messages, ...sanitized },
    }],
    eventCount: 1,
    delegations: [],
    childRuns: [],
    approvals: [],
  };
  const withoutSameId = state.runs.filter((item) => item.id !== streamId);
  setState({
    runs: [run, ...withoutSameId].slice(0, MAX_RUNS),
    selectedRunId: streamId,
  });
}

export function recordAiDebugChunk(streamId: string, chunk: AiDebugChunk): void {
  if (!DEBUG_STORE_ENABLED) return;
  const now = Date.now();
  replaceRun(streamId, (run) => {
    const status = nextStatus(run, chunk);
    const terminal = TERMINAL_STATUSES.has(status);
    const runId = getAgentRunId(chunk);
    const delegation =
      chunk.agentDelegationCreated ?? chunk.agentDelegationUpdated;
    const approval =
      chunk.toolApprovalRequired ?? chunk.toolApprovalResolved;
    return {
      ...run,
      status,
      taskType: updatedTaskType(run, chunk),
      updatedAt: now,
      finishedAt: terminal ? run.finishedAt ?? now : run.finishedAt,
      model: chunk.model || run.model,
      output: run.output + (chunk.delta || ""),
      thinking: run.thinking + (chunk.thinkingDelta || ""),
      modelCalls: appendModelCall(run, chunk, now),
      tools: upsertTools(run, chunk, now),
      events: appendEvent(run, chunk, now),
      eventCount: run.eventCount + 1,
      contextBudget: chunk.contextBudget
        ? { ...(run.contextBudget as Record<string, unknown> | undefined), ...chunk.contextBudget }
        : run.contextBudget,
      contextCompaction: chunk.contextCompaction ?? run.contextCompaction,
      agentRunId: runId || run.agentRunId,
      agentPlan: chunk.agentRunTodosUpdated ?? chunk.agentRunTodoUpdated ?? run.agentPlan,
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
    const status = chunk.aborted
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
    const delegation =
      chunk.agentDelegationCreated ?? chunk.agentDelegationUpdated;
    const approval =
      chunk.toolApprovalRequired ?? chunk.toolApprovalResolved;
    return {
      ...run,
      status,
      updatedAt: now,
      finishedAt: terminal ? now : undefined,
      model: chunk.model || run.model,
      output: run.output + (chunk.delta || ""),
      thinking: run.thinking + (chunk.thinkingDelta || ""),
      modelCalls: appendModelCall(run, chunk, now),
      tools: upsertTools(run, chunk, now),
      events: appendEvent(run, chunk, now),
      eventCount: run.eventCount + 1,
      contextBudget: chunk.contextBudget
        ? { ...(run.contextBudget as Record<string, unknown> | undefined), ...chunk.contextBudget }
        : run.contextBudget,
      contextCompaction: chunk.contextCompaction ?? run.contextCompaction,
      // Never replace the orchestration root with the current child Run id.
      agentRunId: rootAgentRunId,
      agentPlan: chunk.agentRunTodosUpdated ?? chunk.agentRunTodoUpdated ?? run.agentPlan,
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
