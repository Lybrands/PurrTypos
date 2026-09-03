import React from "react";
import type { AiTaskPlan } from "../../agent-runtime";
import TaskPlanCard from "../AgentConversation/TaskProgress/TaskPlanCard";
import ToolDiagnosticsCard from "./ToolDiagnosticsCard";
import type {
  AiErrorReport,
  AiModelInputDiagnostic,
  AiPlannerModelOutputDiagnostic,
} from "../../types";
import {
  clearAiDebugRuns,
  aiDebugConversationLifecycle,
  aiDebugCurrentRunId,
  aiDebugTurnTokenUsage,
  aiDebugTurnDiagnosticId,
  aiDebugTurnRootRunId,
  getAiDebugSnapshot,
  groupAiDebugRunsByTurn,
  recordAiDebugErrorReportStatus,
  recordAiDebugRunUsageSnapshot,
  isAiDebugRunActive,
  subscribeAiDebugStore,
  type AiDebugRun,
  type AiDebugTurnGroup,
  type AiDebugDelegationActivity,
  type AiDebugRunStatus,
  type AiDebugModelCall,
  type AiDebugTokenUsage,
  type AiDebugTool,
} from "./store";
import { services } from "../../services";
import "./index.scss";

type Position = { x: number; y: number };

const POSITION_KEY = "purrtypos:ai-dev-inspector-position";
const PANEL_WIDTH = 520;
const PANEL_HEIGHT = 760;
const usageSnapshotRequests = new Set<string>();

const STATUS_LABELS: Record<AiDebugRunStatus, string> = {
  starting: "正在发起",
  preparing: "准备上下文",
  planning: "制定计划",
  thinking: "模型推理",
  tool: "调用工具",
  awaiting_approval: "等待审批",
  responding: "生成回答",
  dispatched: "后台执行中",
  completed: "已完成",
  aborted: "已中止",
  failed: "失败",
};

function debugTaskPlan(value: unknown, runId: string): AiTaskPlan | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const plan = value as Record<string, unknown>;
  if (!Array.isArray(plan.steps)) return null;
  return {
    ...(plan as unknown as AiTaskPlan),
    runId,
    title: String(plan.title || 'Core Planner 执行计划'),
  };
}

function loadPosition(): Position {
  const fallback = {
    x: Math.max(16, window.innerWidth - PANEL_WIDTH - 20),
    y: Math.max(16, Math.min(80, window.innerHeight - 80)),
  };
  try {
    const parsed = JSON.parse(localStorage.getItem(POSITION_KEY) || "");
    if (Number.isFinite(parsed?.x) && Number.isFinite(parsed?.y)) {
      return parsed as Position;
    }
  } catch {
    // 位置持久化不可用时使用默认位置。
  }
  return fallback;
}

function clampPosition(position: Position, collapsed: boolean): Position {
  const width = collapsed ? 188 : Math.min(PANEL_WIDTH, window.innerWidth - 24);
  const height = collapsed ? 44 : Math.min(PANEL_HEIGHT, window.innerHeight - 24);
  return {
    x: Math.max(12, Math.min(position.x, window.innerWidth - width - 12)),
    y: Math.max(12, Math.min(position.y, window.innerHeight - height - 12)),
  };
}

function formatJson(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp);
  const base = date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  return `${base}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function formatDuration(ms: number): string {
  if (ms < 1_000) return `${Math.max(0, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1_000)}s`;
}

function formatTokens(value: unknown): string {
  const tokens = Number(value);
  if (!Number.isFinite(tokens) || tokens < 0) return "—";
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(tokens));
}

function tokenUsageText(usage: AiDebugTokenUsage): string {
  if (usage.totalTokens === 0 && usage.unreportedAttempts > 0) return "Token 未上报";
  const incomplete = !usage.complete || usage.unreportedAttempts > 0;
  return `${formatTokens(usage.totalTokens)}${incomplete ? "+" : ""} Token`;
}

function tokenUsageTitle(usage: AiDebugTokenUsage): string {
  const parts = [
    `输入 ${usage.inputTokens}`,
    `输出 ${usage.outputTokens}`,
    `推理 ${usage.reasoningTokens}`,
    `合计 ${usage.totalTokens}`,
  ];
  if (!usage.complete) parts.push("当前仅显示已上报用量");
  if (usage.unreportedAttempts > 0) {
    parts.push(`${usage.unreportedAttempts} 次模型调用未返回用量`);
  }
  return parts.join(" · ");
}

function StatusPill({ status }: { status: AiDebugRunStatus }) {
  return (
    <span className={`ai-dev-inspector__status ai-dev-inspector__status--${status}`}>
      <span className="ai-dev-inspector__status-dot" />
      {STATUS_LABELS[status]}
    </span>
  );
}

function ToolCard({ tool }: { tool: AiDebugTool }) {
  const duration =
    tool.completedAt != null ? tool.completedAt - tool.startedAt : Date.now() - tool.startedAt;
  return (
    <details className={`ai-dev-inspector__tool ai-dev-inspector__tool--${tool.status}`}>
      <summary>
        <span className="ai-dev-inspector__tool-index">{tool.index + 1}</span>
        <strong>{tool.name}</strong>
        {tool.cached && <span className="ai-dev-inspector__tag">缓存</span>}
        <span className="ai-dev-inspector__tool-duration">{formatDuration(duration)}</span>
        <span>{tool.status === "running" ? "运行中" : tool.status === "failed" ? "失败" : "完成"}</span>
      </summary>
      <div className="ai-dev-inspector__tool-detail">
        {tool.errorCode && (
          <>
            <span>错误代码</span>
            <pre>{tool.errorCode}</pre>
          </>
        )}
        {tool.errorMessage && (
          <>
            <span>具体原因</span>
            <pre>{tool.errorMessage}</pre>
          </>
        )}
        {tool.exceptionType && (
          <>
            <span>异常类型</span>
            <pre>{tool.exceptionType}</pre>
          </>
        )}
        {tool.diagnostics && (
          <>
            <span>结构化诊断</span>
            <pre>{formatJson(tool.diagnostics)}</pre>
          </>
        )}
        {tool.argumentsValue !== undefined && (
          <>
            <span>参数</span>
            <pre>{formatJson(tool.argumentsValue)}</pre>
          </>
        )}
        {tool.result !== undefined && (
          <>
            <span>结果</span>
            <pre>{formatJson(tool.result)}</pre>
          </>
        )}
        {tool.argumentsValue === undefined && tool.result === undefined && (
          <div className="ai-dev-inspector__notice">
            参数和返回请展开下方「工具参数与返回」，从持久化日志读取。
          </div>
        )}
      </div>
    </details>
  );
}

interface FailureDiagnosis {
  stage?: string;
  outcome?: string;
  tool?: string;
  toolCallId?: string;
  errorCode?: string;
  exceptionType?: string;
  primaryErrorCode?: string;
  recoveryErrorCode?: string;
  reason?: string;
}

const FAILURE_STAGE_LABELS: Record<string, string> = {
  agent_runtime: "Agent 运行时",
  context_reservation: "上下文预留",
  context_setup: "上下文组装",
  model_round: "模型调用",
  planning: "任务规划",
  stream: "模型流式响应",
  tool_execution: "工具执行",
};

const FAILURE_OUTCOME_LABELS: Record<string, string> = {
  contract_violation: "契约冲突",
  exception: "异常",
  failed: "失败",
  interrupted: "中断",
  invalid: "无效结果",
  overflow: "超出预算",
  rejected: "被拒绝",
};

function diagnosticText(
  diagnostics: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = String(diagnostics?.[key] ?? "").trim();
  return value || undefined;
}

function buildFailureDiagnosis(options: {
  report?: AiErrorReport;
  tools?: AiDebugTool[];
  fallback?: string;
}): FailureDiagnosis | null {
  const diagnostics = options.report?.diagnostics;
  const failedTool = [...(options.tools ?? [])]
    .reverse()
    .find((tool) => tool.status === "failed");
  const errorCode =
    diagnosticText(diagnostics, "failureErrorCode") ||
    failedTool?.errorCode ||
    options.report?.errorCode;
  const reason =
    diagnosticText(diagnostics, "failureReason") ||
    failedTool?.errorMessage ||
    options.fallback ||
    options.report?.errorMessage;
  const diagnosis: FailureDiagnosis = {
    stage: diagnosticText(diagnostics, "failureStage") ||
      (failedTool ? "tool_execution" : undefined),
    outcome: diagnosticText(diagnostics, "failureOutcome") || failedTool?.outcome,
    tool: diagnosticText(diagnostics, "failureTool") || failedTool?.name,
    toolCallId: diagnosticText(diagnostics, "failureToolCallId") || failedTool?.id,
    errorCode,
    exceptionType: diagnosticText(diagnostics, "exceptionType") || failedTool?.exceptionType,
    primaryErrorCode: diagnosticText(diagnostics, "primaryErrorCode"),
    recoveryErrorCode: diagnosticText(diagnostics, "recoveryErrorCode"),
    reason,
  };
  return Object.values(diagnosis).some(Boolean) ? diagnosis : null;
}

function FailureDiagnosisCard({
  report,
  tools,
  fallback,
  compact = false,
}: {
  report?: AiErrorReport;
  tools?: AiDebugTool[];
  fallback?: string;
  compact?: boolean;
}) {
  const diagnosis = buildFailureDiagnosis({ report, tools, fallback });
  if (!diagnosis) return null;
  const fields = [
    diagnosis.stage && {
      label: "失败阶段",
      value: FAILURE_STAGE_LABELS[diagnosis.stage] || diagnosis.stage,
      code: diagnosis.stage,
    },
    diagnosis.tool && {
      label: "失败工具",
      value: diagnosis.tool,
      code: diagnosis.toolCallId,
    },
    diagnosis.errorCode && {
      label: "错误代码",
      value: diagnosis.errorCode,
    },
    diagnosis.outcome && {
      label: "执行结果",
      value: FAILURE_OUTCOME_LABELS[diagnosis.outcome] || diagnosis.outcome,
      code: diagnosis.outcome,
    },
    diagnosis.exceptionType && {
      label: "异常类型",
      value: diagnosis.exceptionType,
    },
    diagnosis.primaryErrorCode && {
      label: "原始错误",
      value: diagnosis.primaryErrorCode,
    },
    diagnosis.recoveryErrorCode && {
      label: "恢复错误",
      value: diagnosis.recoveryErrorCode,
    },
  ].filter(Boolean) as Array<{ label: string; value: string; code?: string }>;
  return (
    <div className={`ai-dev-inspector__diagnosis ${compact ? "is-compact" : ""}`}>
      <strong>具体失败原因</strong>
      {fields.length > 0 && (
        <dl>
          {fields.map((field) => (
            <div key={field.label}>
              <dt>{field.label}</dt>
              <dd>
                <span>{field.value}</span>
                {field.code && field.code !== field.value ? <code>{field.code}</code> : null}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {diagnosis.reason ? <p>{diagnosis.reason}</p> : null}
    </div>
  );
}

const MODEL_PHASE_LABELS: Record<string, string> = {
  planning: "规划",
  replanning: "重规划",
  generation: "生成",
  response_judge: "回答校验",
};

function ModelCallRow({
  call,
  index,
  source,
}: {
  call: AiDebugModelCall;
  index: number;
  source: string;
}) {
  const maxCallOutputTokens = call.parameters?.maxCallOutputTokens;
  const modelCapabilities = call.parameters?.modelOutputCapabilities as Record<string, unknown> | undefined;
  const roundLabel =
    call.logicalRound != null
      ? `逻辑轮次 ${call.logicalRound}${call.attempt != null ? ` / 尝试 ${call.attempt}` : ""}`
      : call.round != null
        ? `轮次 ${call.round}`
        : "";
  return (
    <div className="ai-dev-inspector__model-call">
      <div>
        <span>#{index + 1}</span>
        <strong>{MODEL_PHASE_LABELS[call.phase] || call.phase}</strong>
        {call.count > 1 ? <em>×{call.count}</em> : null}
        <small>{source}</small>
        {roundLabel ? <small>{roundLabel}</small> : null}
      </div>
      <div className="ai-dev-inspector__tool-chips">
        {maxCallOutputTokens ? (
          <span>本次上限 {formatTokens(maxCallOutputTokens)}</span>
        ) : null}
        {modelCapabilities?.maxCallOutputTokens ? (
          <span>模型上限 {formatTokens(modelCapabilities.maxCallOutputTokens)}</span>
        ) : null}
        {call.toolNames.length > 0
          ? call.toolNames.map((name) => <code key={name}>{name}</code>)
          : <span>未传入工具</span>}
      </div>
      {call.parameters ? (
        <details className="ai-dev-inspector__model-parameters">
          <summary>传给模型的参数（已脱敏）</summary>
          <pre>{formatJson(call.parameters)}</pre>
        </details>
      ) : null}
    </div>
  );
}

function ErrorReportCard({ run }: { run: AiDebugRun }) {
  const report = run.errorReport;
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [feedback, setFeedback] = React.useState("");

  React.useEffect(() => {
    setNote(report?.userNote || "");
    setFeedback("");
  }, [report?.id]);

  if (!run.error && !report) return null;
  if (!report) {
    return (
      <div className="ai-dev-inspector__report ai-dev-inspector__report--missing">
        <strong>本地错误记录未创建</strong>
        <span>通常表示后端在记录错误时不可用；原始错误仍保留在本轮事件中。</span>
      </div>
    );
  }

  const submitReport = async () => {
    if (submitting || report.status !== "captured") return;
    setSubmitting(true);
    setFeedback("");
    try {
      const result = await services.ai.submitAiErrorReport({
        reportId: report.id,
        userNote: note.trim() || undefined,
      });
      if (!result.success) {
        setFeedback(result.error || "加入待排查失败");
        return;
      }
      recordAiDebugErrorReportStatus(run.id, "submitted", note.trim());
      setFeedback("已加入本地待排查列表");
    } catch {
      setFeedback("加入待排查失败");
    } finally {
      setSubmitting(false);
    }
  };

  const copyReportId = async () => {
    try {
      await navigator.clipboard.writeText(report.id);
      setFeedback("已复制报告编号");
    } catch {
      setFeedback("复制失败");
    }
  };

  const statusLabel =
    report.status === "captured"
      ? "已自动记录"
      : report.status === "submitted"
        ? "待排查"
        : "已解决";

  return (
    <div className="ai-dev-inspector__report">
      <div className="ai-dev-inspector__report-heading">
        <div>
          <span>错误报告</span>
          <code title={report.id}>{report.id}</code>
        </div>
        <strong data-status={report.status}>{statusLabel}</strong>
      </div>
      <p>{report.errorMessage}</p>
      {report.status === "captured" ? (
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={2000}
          placeholder="可选：补充复现步骤或预期结果"
          aria-label="错误报告补充说明"
        />
      ) : report.userNote ? (
        <p>{report.userNote}</p>
      ) : null}
      <div className="ai-dev-inspector__report-actions">
        <button type="button" onClick={copyReportId}>复制编号</button>
        {report.status === "captured" ? (
          <button type="button" onClick={submitReport} disabled={submitting}>
            {submitting ? "处理中…" : "加入待排查"}
          </button>
        ) : null}
        {feedback ? <span>{feedback}</span> : null}
      </div>
    </div>
  );
}

function PlannerModelOutputCard({
  runId,
  hasPlan,
  status,
}: {
  runId?: string;
  hasPlan: boolean;
  status: AiDebugRunStatus;
}) {
  const [outputs, setOutputs] = React.useState<AiPlannerModelOutputDiagnostic[]>([]);
  const [loaded, setLoaded] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const requestVersion = React.useRef(0);

  const load = React.useCallback(async () => {
    if (!runId) return;
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const response = await services.ai.getAgentRunPlannerDiagnostics({
        runId,
      });
      if (version !== requestVersion.current) return;
      if (response.success) {
        setOutputs(response.data.outputs);
      } else {
        setError(response.error || "读取 Planner 原始输出失败");
      }
    } catch {
      if (version !== requestVersion.current) return;
      setError("读取 Planner 原始输出失败");
    } finally {
      if (version !== requestVersion.current) return;
      setLoaded(true);
      setLoading(false);
    }
  }, [runId]);

  React.useEffect(() => {
    requestVersion.current += 1;
    setOutputs([]);
    setLoaded(false);
    setError("");
    setLoading(false);
    return () => {
      requestVersion.current += 1;
    };
  }, [runId]);

  React.useEffect(() => {
    if (!runId) return;
    void load();
  }, [load, hasPlan, status]);

  if (!runId || (loaded && !loading && !error && outputs.length === 0 && !hasPlan)) {
    return null;
  }

  return (
    <details className="ai-dev-inspector__planner-raw">
      <summary>
        <span>
          <strong>Planner 原始模型返回</strong>
          <small>精确持久化内容，不含 reasoning</small>
        </span>
        <em data-status={error ? "fail" : outputs.length ? "pass" : "captured"}>
          {loading ? "读取中" : error ? "读取失败" : outputs.length ? `${outputs.length} 次调用` : "等待返回"}
        </em>
      </summary>
      <div className="ai-dev-inspector__planner-raw-body">
        {error ? <div className="ai-dev-inspector__error">{error}</div> : null}
        {outputs.map((output) => (
          <section className="ai-dev-inspector__planner-attempt" key={output.invocationId}>
            <div className="ai-dev-inspector__inline-meta">
              <span>{output.model || "未知模型"}</span>
              <span>revision {output.revision} / attempt {output.attempt}</span>
              <span>{output.status}{output.finishReason ? ` · ${output.finishReason}` : ""}</span>
            </div>
            {output.contentDeltaConflict ? (
              <div className="ai-dev-inspector__error">
                同一 Provider source span 出现冲突，只保留首次持久化值。
              </div>
            ) : null}
            <pre className="ai-dev-inspector__raw-json">
              {output.rawContent || "模型尚未返回 content delta…"}
            </pre>
            {output.rawContentTruncated ? (
              <div className="ai-dev-inspector__error">原始输出超过诊断上限，当前内容已截断。</div>
            ) : null}
            <details className="ai-dev-inspector__text-block">
              <summary>调用标识与时序</summary>
              <pre>{formatJson({
                invocationId: output.invocationId,
                outputStreamId: output.outputStreamId,
                contentDeltaCount: output.contentDeltaCount,
                progressRecords: output.progressRecords,
                timing: output.timing,
              })}</pre>
            </details>
          </section>
        ))}
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "读取中…" : "重新读取"}
        </button>
      </div>
    </details>
  );
}

function ModelInputDiagnosticsCard({
  runId,
  status,
}: {
  runId?: string;
  status: AiDebugRunStatus;
}) {
  const [calls, setCalls] = React.useState<AiModelInputDiagnostic[]>([]);
  const [loaded, setLoaded] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [opened, setOpened] = React.useState(false);
  const requestVersion = React.useRef(0);
  const previousStatus = React.useRef(status);

  const load = React.useCallback(async () => {
    if (!runId) return;
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const response = await services.ai.getAgentRunModelInputDiagnostics({ runId });
      if (version !== requestVersion.current) return;
      if (response.success) {
        setCalls(response.data.calls);
      } else {
        setError(response.error || "读取模型实际输入失败");
      }
    } catch {
      if (version !== requestVersion.current) return;
      setError("读取模型实际输入失败");
    } finally {
      if (version !== requestVersion.current) return;
      setLoaded(true);
      setLoading(false);
    }
  }, [runId]);

  React.useEffect(() => {
    requestVersion.current += 1;
    setCalls([]);
    setLoaded(false);
    setLoading(false);
    setError("");
    setOpened(false);
  }, [runId]);

  React.useEffect(() => {
    const changed = previousStatus.current !== status;
    previousStatus.current = status;
    if (
      changed
      && opened
      && loaded
      && ["completed", "failed", "aborted"].includes(status)
    ) void load();
  }, [load, loaded, opened, status]);

  if (!runId) return null;
  const capturedCalls = calls.filter((call) => call.captured).length;

  return (
    <details
      className="ai-dev-inspector__planner-raw ai-dev-inspector__model-input"
      open={opened}
      onToggle={(event) => {
        const nextOpen = event.currentTarget.open;
        setOpened(nextOpen);
        if (nextOpen && !loaded && !loading) void load();
      }}
    >
      <summary>
        <span>
          <strong>Provider 实际输入</strong>
          <small>开发环境最终消息；保留系统上下文，密钥已脱敏，不展示 reasoning</small>
        </span>
        <em data-status={error ? "fail" : capturedCalls ? "pass" : "captured"}>
          {loading ? "读取中" : error ? "读取失败" : loaded ? `${capturedCalls}/${calls.length} 次已记录` : "展开读取"}
        </em>
      </summary>
      <div className="ai-dev-inspector__planner-raw-body">
        {error ? <div className="ai-dev-inspector__error">{error}</div> : null}
        {loaded && !error && calls.length === 0 ? (
          <div className="ai-dev-inspector__notice">当前 Run 尚无模型调用记录。</div>
        ) : null}
        {calls.map((call, index) => (
          <section className="ai-dev-inspector__planner-attempt" key={call.eventRowId}>
            <div className="ai-dev-inspector__inline-meta">
              <span>#{index + 1} · {call.phase}</span>
              <span>{call.provider || "未知 Provider"} / {call.model || "未知模型"}</span>
              {call.round != null ? <span>round {call.round}</span> : null}
              {call.attempt != null ? <span>attempt {call.attempt}</span> : null}
              {call.revision != null ? <span>revision {call.revision}</span> : null}
            </div>
            {call.captured ? (
              <div className="ai-dev-inspector__messages">
                {call.messages.map((message, messageIndex) => {
                  const role = String(message.role || "unknown");
                  return (
                    <article
                      className={`ai-dev-inspector__captured-message ai-dev-inspector__message--${role}`}
                      key={`${call.eventRowId}:${messageIndex}`}
                    >
                      <header><span>{role}</span><small>#{messageIndex + 1}</small></header>
                      <pre>{formatJson(message)}</pre>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="ai-dev-inspector__notice">
                该调用产生于模型输入诊断启用之前，没有补造历史内容。
              </div>
            )}
          </section>
        ))}
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "读取中…" : "重新读取"}
        </button>
      </div>
    </details>
  );
}

function DelegationActivityList({
  items,
  now,
}: {
  items: AiDebugDelegationActivity[];
  now: number;
}) {
  if (!items.length) return null;
  return (
    <>
      <div className="ai-dev-inspector__section-title">
        <span>Agent 委派</span>
        <small>
          {items.filter((item) => item.status === "completed").length}/{items.length} 完成
        </small>
      </div>
      <div className="ai-dev-inspector__delegation-activities">
        {items.map((activity, index) => {
          const modelCalls = activity.modelCalls.reduce(
            (sum, call) => sum + call.count,
            0,
          );
          const active = !["completed", "failed", "aborted"].includes(activity.status);
          return (
            <details
              key={activity.delegationId}
              className="ai-dev-inspector__delegation-activity"
              data-status={activity.status}
              open={active || activity.status === "failed"}
            >
              <summary>
                <span>
                  <strong>{activity.agentTitle || activity.agentName}</strong>
                  <small>#{index + 1}</small>
                </span>
                <span>{STATUS_LABELS[activity.status]}</span>
              </summary>
              <div>
                {activity.objective && <p>{activity.objective}</p>}
                <dl>
                  <div><dt>Delegation</dt><dd title={activity.delegationId}>{activity.delegationId}</dd></div>
                  <div><dt>任务单元</dt><dd>{activity.unitId || "—"}</dd></div>
                  <div><dt>执行尝试</dt><dd>{activity.attempt ?? 1}</dd></div>
                  <div><dt>耗时</dt><dd>{formatDuration((activity.finishedAt ?? now) - activity.startedAt)}</dd></div>
                  <div><dt>模型调用</dt><dd>{modelCalls} 次</dd></div>
                  <div><dt>工具调用</dt><dd>{activity.tools.length} 个</dd></div>
                </dl>
                {activity.error && <div className="ai-dev-inspector__error">{activity.error}</div>}
                {activity.commentary && (
                  <details className="ai-dev-inspector__text-block">
                    <summary>委派说明 <small>{activity.commentary.length} 字符</small></summary>
                    <pre>{activity.commentary}</pre>
                  </details>
                )}
                {activity.output && (
                  <details className="ai-dev-inspector__text-block">
                    <summary>委派输出 <small>{activity.output.length} 字符</small></summary>
                    <pre>{activity.output}</pre>
                  </details>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </>
  );
}

function RunTechnicalDetails({
  run,
  modelCallRows,
  modelToolNames,
}: {
  run: AiDebugRun;
  modelCallRows: Array<{ key: string; call: AiDebugModelCall; source: string }>;
  modelToolNames: string[];
}) {
  return (
    <details className="ai-dev-inspector__technical">
      <summary>
        <span>技术明细</span>
        <small>{run.eventCount} 个事件</small>
      </summary>
      <div className="ai-dev-inspector__technical-body">
        <div className="ai-dev-inspector__identifiers">
          <div><span>Agent Run ID</span><code title={run.agentRunId}>{run.agentRunId || "—"}</code></div>
          <div><span>Stream ID</span><code title={run.id}>{run.id}</code></div>
          <div><span>Turn ID</span><code title={run.turnId}>{run.turnId || "—"}</code></div>
          <div><span>Session ID</span><code>{run.sessionId != null ? `#${run.sessionId}` : "—"}</code></div>
          <div><span>记录 ID</span><code>{run.conversationId != null ? `#${run.conversationId}` : "尚未保存"}</code></div>
        </div>

        {modelToolNames.length > 0 ? (
          <div className="ai-dev-inspector__tool-chips ai-dev-inspector__tool-chips--catalog">
            {modelToolNames.map((name) => <code key={name}>{name}</code>)}
          </div>
        ) : null}

        {modelCallRows.length > 0 ? (
          <details className="ai-dev-inspector__model-calls">
            <summary>模型调用 <small>{modelCallRows.reduce((sum, item) => sum + item.call.count, 0)} 次</small></summary>
            <div>
              {modelCallRows.map((item, index) => (
                <ModelCallRow
                  key={item.key}
                  call={item.call}
                  index={index}
                  source={item.source}
                />
              ))}
            </div>
          </details>
        ) : null}

        <details className="ai-dev-inspector__text-block">
          <summary>请求参数</summary>
          <pre>{formatJson(run.request.meta)}</pre>
        </details>

        {run.request.messages.length > 0 ? (
          <details className="ai-dev-inspector__text-block">
            <summary>入口请求消息 <small>{run.request.messages.length} 条 · 后端处理前</small></summary>
            <div className="ai-dev-inspector__messages">
              {run.request.messages.map((message, index) => (
                <details
                  className={`ai-dev-inspector__message ai-dev-inspector__message--${message.role}`}
                  key={`${message.role}-${index}`}
                >
                  <summary><span>{message.role}</span><small>#{index + 1}</small></summary>
                  <pre>{formatJson(message.content)}</pre>
                </details>
              ))}
            </div>
          </details>
        ) : null}

        {run.contextBudget !== undefined ? (
          <details className="ai-dev-inspector__text-block">
            <summary>上下文与预算</summary>
            <pre>{formatJson(run.contextBudget)}</pre>
          </details>
        ) : null}

        <details className="ai-dev-inspector__text-block">
          <summary>事件日志 <small>{run.events.length}/{run.eventCount}</small></summary>
          <div className="ai-dev-inspector__events">
            {run.events.map((event) => (
              <details className={`ai-dev-inspector__event ai-dev-inspector__event--${event.type}`} key={event.id}>
                <summary><time>{formatTime(event.at)}</time><span>{event.label}</span></summary>
                {event.payload !== undefined ? <pre>{formatJson(event.payload)}</pre> : null}
              </details>
            ))}
            {run.eventCount > run.events.length ? (
              <div className="ai-dev-inspector__notice">内存中只保留最近 {run.events.length} 条事件。</div>
            ) : null}
          </div>
        </details>
      </div>
    </details>
  );
}

function RunTimelineItem({
  run,
  index,
  total,
  rootRunId,
  isCurrent,
  now,
}: {
  run: AiDebugRun;
  index: number;
  total: number;
  rootRunId?: string;
  isCurrent: boolean;
  now: number;
}) {
  const [open, setOpen] = React.useState(isCurrent);
  React.useEffect(() => setOpen(isCurrent), [isCurrent, run.id]);
  const isRoot = Boolean(rootRunId && run.agentRunId === rootRunId);
  const elapsed = (run.finishedAt ?? now) - run.startedAt;
  const modelCallRows = [
    ...run.modelCalls.map((call) => ({
      key: `run:${call.id}`,
      call,
      source: "当前 Agent",
    })),
    ...run.delegationActivities.flatMap((activity) => activity.modelCalls.map((call) => ({
      key: `${activity.id}:${call.id}`,
      call,
      source: activity.agentTitle || activity.agentName,
    }))),
  ];
  const modelCallCount = modelCallRows.reduce((sum, item) => sum + item.call.count, 0);
  const modelToolNames = [...new Set(modelCallRows.flatMap((item) => item.call.toolNames))];
  const usage = run.tokenUsage;

  return (
    <section className="ai-dev-inspector__timeline-item" data-status={run.status}>
      <div className="ai-dev-inspector__timeline-rail" aria-hidden="true">
        <span>{index + 1}</span>
        {index < total - 1 ? <i /> : null}
      </div>
      <details
        className="ai-dev-inspector__run-card"
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          <div>
            <span>{isRoot ? "主 AGENT" : `执行段 ${index + 1}`}</span>
            <strong>{isRoot ? "主流程" : run.source || run.taskType}</strong>
            <small title={usage ? tokenUsageTitle(usage) : undefined}>
              {isRoot ? `${run.source} · ` : ""}{formatTime(run.startedAt)}
              {usage ? ` · ${tokenUsageText(usage)}` : ""}
            </small>
          </div>
          <span className="ai-dev-inspector__run-card-status">
            <StatusPill status={run.status} />
            <i aria-hidden="true">⌄</i>
          </span>
        </summary>

        <div className="ai-dev-inspector__run-card-body">

          <div className="ai-dev-inspector__run-facts">
            <span><b>{formatDuration(elapsed)}</b>耗时</span>
            <span><b>{run.model || "—"}</b>模型</span>
            <span><b>{modelCallCount}</b>模型调用</span>
            <span><b>{run.tools.length}</b>工具调用</span>
            {usage ? <>
              <span title={tokenUsageTitle(usage)}>
                <b>{tokenUsageText(usage)}</b>本段消耗
              </span>
              <span><b>{formatTokens(usage.inputTokens)} / {formatTokens(usage.outputTokens)}</b>输入 / 输出</span>
              {usage.reasoningTokens > 0 ? (
                <span><b>{formatTokens(usage.reasoningTokens)}</b>其中推理 Token</span>
              ) : null}
              {usage.unreportedAttempts > 0 ? (
                <span><b>{usage.unreportedAttempts}</b>用量未上报的调用</span>
              ) : null}
            </> : null}
          </div>

          {run.error ? <div className="ai-dev-inspector__error">{run.error}</div> : null}
          <FailureDiagnosisCard report={run.errorReport} tools={run.tools} fallback={run.error} />
          <ErrorReportCard run={run} />

          {run.tools.length > 0 ? (
            <div className="ai-dev-inspector__run-section">
              <div className="ai-dev-inspector__section-title">
                <span>工具执行</span><small>{run.tools.length} 个</small>
              </div>
              <div className="ai-dev-inspector__tool-list">
                {run.tools.map((tool) => <ToolCard key={`${tool.batchIndex}-${tool.id}`} tool={tool} />)}
              </div>
            </div>
          ) : null}

          {run.agentRunId ? <ToolDiagnosticsCard
            key={run.agentRunId}
            runId={run.agentRunId}
            revision={`${run.status}:${run.tools.map((tool) => `${tool.id}:${tool.status}`).join('|')}`}
          /> : null}

          <DelegationActivityList items={run.delegationActivities} now={now} />

          {run.commentary ? (
            <details className="ai-dev-inspector__text-block">
              <summary>公开过程输出 <small>{run.commentary.length} 字符</small></summary>
              <pre>{run.commentary}</pre>
            </details>
          ) : null}
          {run.output ? (
            <details className="ai-dev-inspector__text-block">
              <summary>最终输出 <small>{run.output.length} 字符</small></summary>
              <pre>{run.output}</pre>
            </details>
          ) : null}

          <ModelInputDiagnosticsCard
            runId={run.agentRunId}
            status={run.status}
          />

          <RunTechnicalDetails
            run={run}
            modelCallRows={modelCallRows}
            modelToolNames={modelToolNames}
          />
        </div>
      </details>
    </section>
  );
}

function ConversationTimeline({
  turn,
  now,
}: {
  turn: AiDebugTurnGroup;
  now: number;
}) {
  const lifecycle = aiDebugConversationLifecycle(turn);
  const rootRunId = aiDebugTurnRootRunId(turn);
  const rootRun = rootRunId
    ? turn.runs.find((run) => run.agentRunId === rootRunId)
    : undefined;
  const taskPlan = debugTaskPlan(rootRun?.agentPlan, rootRunId || rootRun?.id || turn.key);
  const runs = [...turn.runs].sort((left, right) => (
    left.startedAt - right.startedAt || left.id.localeCompare(right.id)
  ));
  const currentRunId = aiDebugCurrentRunId(runs);
  const turnUsage = aiDebugTurnTokenUsage(runs);
  const usageRunIds = runs.flatMap((run) => (
    !isAiDebugRunActive(run)
    && run.tokenUsage?.complete !== true
    && run.agentRunId
      ? [run.agentRunId]
      : []
  ));
  const usageRefreshKey = usageRunIds.join("|");
  React.useEffect(() => {
    for (const runId of usageRunIds) {
      if (usageSnapshotRequests.has(runId)) continue;
      usageSnapshotRequests.add(runId);
      void services.ai.getAgentRunSnapshot({ runId, limit: 1 })
        .then((result) => {
          if (result.success && result.data) {
            recordAiDebugRunUsageSnapshot(result.data);
          }
        })
        .catch(() => undefined)
        .finally(() => usageSnapshotRequests.delete(runId));
    }
  }, [usageRefreshKey]);
  const finishedAt = runs.every((run) => !isAiDebugRunActive(run))
    ? Math.max(...runs.map((run) => run.finishedAt ?? run.updatedAt))
    : now;
  const modelCallCount = runs.reduce((sum, run) => (
    sum
    + run.modelCalls.reduce((count, call) => count + call.count, 0)
    + run.delegationActivities.reduce((count, activity) => (
      count + activity.modelCalls.reduce((calls, call) => calls + call.count, 0)
    ), 0)
  ), 0);
  const toolCallCount = runs.reduce((sum, run) => sum + run.tools.length, 0);

  return (
    <div className="ai-dev-inspector__conversation">
      <section className="ai-dev-inspector__turn-summary">
        <header>
          <div>
            <span>当前任务</span>
            <strong>{turn.source}</strong>
          </div>
          <StatusPill status={lifecycle.status} />
        </header>
        {turn.prompt ? <p title={turn.prompt}>{turn.prompt}</p> : null}
        <div className="ai-dev-inspector__turn-facts">
          <span>{formatTime(turn.startedAt)} 开始</span>
          <span>{formatDuration(finishedAt - turn.startedAt)}</span>
          <span>{runs.length} 个执行段</span>
          <span>{modelCallCount} 次模型调用</span>
          <span>{toolCallCount} 次工具调用</span>
          {turnUsage ? (
            <span title={tokenUsageTitle(turnUsage)}>{tokenUsageText(turnUsage)}</span>
          ) : null}
        </div>
      </section>

      {taskPlan ? (
        <section className="ai-dev-inspector__plan">
          <div className="ai-dev-inspector__section-title">
            <span>模型任务计划</span>
            <small>{taskPlan.title} · {taskPlan.steps.length} 步</small>
          </div>
          <TaskPlanCard plan={taskPlan} planKey={`debug:${taskPlan.runId}`} />
        </section>
      ) : null}

      <PlannerModelOutputCard
        runId={rootRunId}
        hasPlan={Boolean(taskPlan)}
        status={rootRun?.status ?? lifecycle.status}
      />

      <div className="ai-dev-inspector__timeline-heading">
        <strong>执行过程</strong>
        <span>按发生顺序排列</span>
      </div>
      <div className="ai-dev-inspector__timeline">
        {runs.map((run, index) => (
          <RunTimelineItem
            key={run.id}
            run={run}
            index={index}
            total={runs.length}
            rootRunId={rootRunId}
            isCurrent={run.id === currentRunId}
            now={now}
          />
        ))}
      </div>
    </div>
  );
}


export default function AiDevInspector() {
  const snapshot = React.useSyncExternalStore(
    subscribeAiDebugStore,
    getAiDebugSnapshot,
    getAiDebugSnapshot,
  );
  const turns = React.useMemo(
    () => groupAiDebugRunsByTurn(snapshot.runs),
    [snapshot.runs],
  );
  const currentTurn = turns.find((turn) => (
    turn.runs.some((run) => run.id === snapshot.selectedRunId)
  )) ?? turns[0];
  const lifecycle = currentTurn
    ? aiDebugConversationLifecycle(currentTurn)
    : undefined;
  const [collapsed, setCollapsed] = React.useState(false);
  const [hidden, setHidden] = React.useState(true);
  const [position, setPosition] = React.useState<Position>(
    () => clampPosition(loadPosition(), false),
  );
  const [now, setNow] = React.useState(Date.now);
  const lastOpenedTurnRef = React.useRef<string | null>(null);
  const dragRef = React.useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    origin: Position;
  } | null>(null);

  React.useEffect(() => {
    if (!currentTurn || lastOpenedTurnRef.current === currentTurn.key) return;
    lastOpenedTurnRef.current = currentTurn.key;
    setHidden(false);
    setCollapsed(false);
  }, [currentTurn?.key]);

  React.useEffect(() => {
    if (!currentTurn?.runs.some(isAiDebugRunActive)) return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [currentTurn?.key, currentTurn?.runs]);

  React.useEffect(() => {
    const handleResize = () => setPosition((current) => clampPosition(current, collapsed));
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [collapsed]);

  const persistPosition = React.useCallback((next: Position) => {
    try {
      localStorage.setItem(POSITION_KEY, JSON.stringify(next));
    } catch {
    }
  }, []);

  const handlePointerDown = (event: React.PointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest("button")) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: position,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setPosition(clampPosition({
      x: drag.origin.x + event.clientX - drag.startX,
      y: drag.origin.y + event.clientY - drag.startY,
    }, collapsed));
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    setPosition((current) => {
      const next = clampPosition(current, collapsed);
      persistPosition(next);
      return next;
    });
  };

  if (hidden) return null;
  const status = lifecycle?.status ?? "starting";

  return (
    <aside
      className={`ai-dev-inspector ${collapsed ? "ai-dev-inspector--collapsed" : ""}`}
      style={{ left: position.x, top: position.y }}
      aria-label="当前 AI 任务诊断"
    >
      <header
        className="ai-dev-inspector__header"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <div className="ai-dev-inspector__title">
          <span className={`ai-dev-inspector__live-dot ai-dev-inspector__live-dot--${status}`} />
          <div>
            <strong>{collapsed ? "AI 诊断" : "当前任务诊断"}</strong>
            <small>{currentTurn?.source ?? "等待任务"} · {STATUS_LABELS[status]}</small>
          </div>
        </div>
        <div className="ai-dev-inspector__header-actions">
          {!collapsed && currentTurn ? (
            <button type="button" onClick={clearAiDebugRuns} aria-label="清空诊断记录" title="清空诊断记录">
              清空
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? "展开调试面板" : "折叠调试面板"}
            title={collapsed ? "展开" : "折叠"}
          >
            {collapsed ? "▣" : "—"}
          </button>
          {!collapsed ? (
            <button type="button" onClick={() => setHidden(true)} aria-label="关闭调试面板" title="下次任务开始时自动打开">
              ×
            </button>
          ) : null}
        </div>
      </header>

      {!collapsed ? (
        <>
          <div className="ai-dev-inspector__body">
            {currentTurn ? (
              <ConversationTimeline turn={currentTurn} now={now} />
            ) : (
              <div className="ai-dev-inspector__waiting">
                <span className="ai-dev-inspector__waiting-icon">⌁</span>
                <strong>等待 AI 任务</strong>
                <p>任务开始后，计划、各执行段、工具和错误会按发生顺序显示在这里。</p>
              </div>
            )}
          </div>

          <footer className="ai-dev-inspector__footer">
            <span>本轮 Root Run ID</span>
            <code title={aiDebugTurnDiagnosticId(currentTurn)}>
              {aiDebugTurnDiagnosticId(currentTurn)}
            </code>
          </footer>
        </>
      ) : null}
    </aside>
  );
}
