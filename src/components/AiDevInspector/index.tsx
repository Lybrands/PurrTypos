import React from "react";
import type {
  AiAgentRunDiagnostics,
  AiAgentRunStabilityTrendReport,
  AiErrorReport,
} from "../../types";
import {
  clearAiDebugRuns,
  getAiDebugSnapshot,
  recordAiDebugErrorReportStatus,
  selectAiDebugRun,
  subscribeAiDebugStore,
  type AiDebugRun,
  type AiDebugChildRun,
  type AiDebugRunStatus,
  type AiDebugModelCall,
  type AiDebugTool,
} from "./store";
import {
  failureFindingLabel,
  failureRemediationLabel,
  formatStabilityTrendRate,
  recoveryCauseLabel,
  recoveryDecisionLabel,
  stabilityGateAlertText,
  stabilityGateVerdictLabel,
  stabilityTrendAlertText,
  stabilityTrendScopeLabel,
  stabilityTrendVerdictLabel,
} from "./stabilityTrend";
import { services } from "../../services";
import "./index.scss";

type InspectorTab = "overview" | "context" | "events" | "reports";
type Position = { x: number; y: number };

const POSITION_KEY = "purrtypos:ai-dev-inspector-position";
const PANEL_WIDTH = 460;
const PANEL_HEIGHT = 680;

const STATUS_LABELS: Record<AiDebugRunStatus, string> = {
  starting: "正在发起",
  preparing: "准备上下文",
  planning: "制定计划",
  thinking: "思考中",
  tool: "调用工具",
  awaiting_approval: "等待审批",
  responding: "生成回答",
  dispatched: "任务已启动",
  completed: "已完成",
  aborted: "已中止",
  failed: "失败",
};

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
  if (!Number.isFinite(tokens) || tokens <= 0) return "—";
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(tokens));
}

function isRunActive(run: AiDebugRun | undefined): boolean {
  return Boolean(
    run &&
      run.status !== "dispatched" &&
      run.status !== "completed" &&
      run.status !== "aborted" &&
      run.status !== "failed",
  );
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
        <span>参数</span>
        <pre>{formatJson(tool.argumentsValue)}</pre>
        {tool.result !== undefined && (
          <>
            <span>结果</span>
            <pre>{formatJson(tool.result)}</pre>
          </>
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

const MODEL_FINISH_REASON_LABELS: Record<string, string> = {
  stop: "正常完成",
  tool_calls: "进入工具调用",
  length: "达到本次输出预算",
  filtered: "供应商内容过滤",
  other: "供应商其他原因",
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
  const outputBudget = call.parameters?.outputBudget as Record<string, unknown> | undefined;
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
        {outputBudget ? (
          <span>本次预算 {formatTokens(outputBudget.effectiveTokens)}</span>
        ) : null}
        {modelCapabilities?.maxOutputTokens ? (
          <span>模型上限 {formatTokens(modelCapabilities.maxOutputTokens)}</span>
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

function StabilityCard({ run }: { run: AiDebugRun }) {
  const [report, setReport] = React.useState<AiAgentRunDiagnostics | null>(null);
  const [trend, setTrend] = React.useState<AiAgentRunStabilityTrendReport | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [maintaining, setMaintaining] = React.useState(false);
  const [maintenanceFeedback, setMaintenanceFeedback] = React.useState("");
  const [error, setError] = React.useState("");

  const load = React.useCallback(async () => {
    if (!run.agentRunId || isRunActive(run)) return;
    setLoading(true);
    setError("");
    try {
      const [diagnosticsResponse, trendResponse] = await Promise.all([
        services.ai.getAgentRunDiagnostics({runId: run.agentRunId}),
        services.ai.getAgentRunStabilityTrend({
          runId: run.agentRunId,
          scope: "auto",
          limit: 20,
        }),
      ]);
      const errors: string[] = [];
      if (diagnosticsResponse.success) {
        setReport(diagnosticsResponse.data);
      } else {
        errors.push(diagnosticsResponse.error || "读取本次稳定性指标失败");
      }
      if (trendResponse.success) {
        setTrend(trendResponse.data);
      } else {
        errors.push(trendResponse.error || "读取近期稳定性趋势失败");
      }
      setError(errors.join("；"));
    } catch {
      setError("读取稳定性指标失败");
    } finally {
      setLoading(false);
    }
  }, [run.agentRunId, run.status]);

  React.useEffect(() => {
    setReport(null);
    setTrend(null);
    setError("");
    setMaintenanceFeedback("");
    if (!run.agentRunId || isRunActive(run)) return;
    void load();
  }, [load, run.agentRunId, run.status]);

  const maintainArtifacts = React.useCallback(async () => {
    setMaintaining(true);
    setMaintenanceFeedback("");
    try {
      const response = await services.ai.maintainAgentArtifacts();
      if (!response.success) {
        setMaintenanceFeedback(response.error || "Artifact 安全维护失败");
        return;
      }
      const released = response.data.report.releasedClaims;
      setMaintenanceFeedback(
        released > 0
          ? `已回收 ${released} 个失效写入租约，未删除 Artifact 内容`
          : "未发现需要回收的失效写入租约",
      );
      await load();
    } catch {
      setMaintenanceFeedback("Artifact 安全维护失败");
    } finally {
      setMaintaining(false);
    }
  }, [load]);

  if (!run.agentRunId || isRunActive(run)) return null;
  const stability = report?.stability;
  const artifacts = report?.artifacts;
  const artifactMaintenance = report?.artifactMaintenance;
  const failureClassification = report?.failureClassification;
  const recovery = report?.recovery;
  const metrics = stability?.metrics;
  const verdictLabel = stability
    ? stability.verdict === "pass"
      ? "稳定"
      : stability.verdict === "warn"
        ? "有降级"
        : "存在失败"
    : loading
      ? "读取中"
      : "暂无";
  const successRate = metrics?.toolSuccessRate;
  const trendStatus = trend?.verdict === "insufficient_data"
    ? "captured"
    : trend?.verdict || "captured";

  return (
    <div className="ai-dev-inspector__report-card">
      <div className="ai-dev-inspector__report-heading">
        <span>持久化稳定性指标</span>
        <strong data-status={stability?.verdict || "captured"}>{verdictLabel}</strong>
      </div>
      {error ? <div className="ai-dev-inspector__error">{error}</div> : null}
      <div className="ai-dev-inspector__metrics">
        <div>
          <span>工具成功率</span>
          <strong>
            {successRate == null ? "—" : `${Math.round(successRate * 100)}%`}
          </strong>
        </div>
        <div>
          <span>协议失败</span>
          <strong>{metrics?.toolProtocolFailures ?? "—"}</strong>
        </div>
        <div>
          <span>未收口工具</span>
          <strong>{metrics?.incompleteToolCalls ?? "—"}</strong>
        </div>
        <div>
          <span>模型中断/重试</span>
          <strong>
            {metrics
              ? `${metrics.interruptedModelAttempts}/${metrics.retryAttempts}`
              : "—"}
          </strong>
        </div>
        <div>
          <span>上下文溢出</span>
          <strong>{metrics?.contextOverflows ?? "—"}</strong>
        </div>
        <div>
          <span>压缩降级/失败</span>
          <strong>
            {metrics
              ? `${metrics.compactionFallbacks}/${metrics.compactionFailures}`
              : "—"}
          </strong>
        </div>
        <div>
          <span>Artifact 批次</span>
          <strong>
            {artifacts
              ? `${artifacts.batchCount} · 未完成 ${artifacts.openArtifacts}`
              : "—"}
          </strong>
        </div>
        <div>
          <span>Work Item</span>
          <strong>
            {artifactMaintenance
              ? `${artifactMaintenance.workItemCount} · 未完成 ${artifactMaintenance.openWorkItems}`
              : "—"}
          </strong>
        </div>
        <div>
          <span>Writer Claim</span>
          <strong>
            {artifactMaintenance
              ? `${artifactMaintenance.activeClaims} 有效 / ${artifactMaintenance.reclaimableClaims} 待回收`
              : "—"}
          </strong>
        </div>
        <div>
          <span>Artifact 一致性</span>
          <strong>
            {artifactMaintenance
              ? artifactMaintenance.consistencyIssues > 0
                ? `${artifactMaintenance.consistencyIssues} 项异常`
                : "正常"
              : "—"}
          </strong>
        </div>
      </div>
      {artifactMaintenance?.requiresAttention ? (
        <div className="ai-dev-inspector__root-cause">
          <div className="ai-dev-inspector__report-heading">
            <div>
              <span>Artifact 生命周期需要处理</span>
              <code>{artifactMaintenance.scopeRunId || "global"}</code>
            </div>
            <strong data-status="warn">需要关注</strong>
          </div>
          <strong>
            可回收租约 {artifactMaintenance.reclaimableClaims} 个，结构异常 {artifactMaintenance.consistencyIssues} 项
          </strong>
          <p>
            安全维护只回收过期、失联或状态失效的 writer claim；结构异常会保留原始记录用于诊断。
          </p>
        </div>
      ) : null}
      {failureClassification?.primaryFinding ? (
        <div className="ai-dev-inspector__root-cause">
          <div className="ai-dev-inspector__report-heading">
            <div>
              <span>持久化根因分类</span>
              <code title={failureClassification.primaryFinding.code}>
                {failureClassification.primaryFinding.code}
              </code>
            </div>
            <strong data-status={failureClassification.primaryFinding.severity}>
              {failureClassification.primaryFinding.confidence === "high"
                ? "高置信度"
                : failureClassification.primaryFinding.confidence === "medium"
                  ? "中置信度"
                  : "证据不足"}
            </strong>
          </div>
          <strong>{failureFindingLabel(failureClassification.primaryFinding)}</strong>
          <p>{failureRemediationLabel(failureClassification.primaryFinding)}</p>
          {failureClassification.findings.length > 1 ? (
            <details>
              <summary>其他关联信号 · {failureClassification.findings.length - 1}</summary>
              <ul>
                {failureClassification.findings.slice(1).map((finding) => (
                  <li key={finding.code}>{failureFindingLabel(finding)}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
      {recovery && recovery.summary.decisionCount > 0 ? (
        <div className="ai-dev-inspector__recovery">
          <div className="ai-dev-inspector__report-heading">
            <div>
              <span>受控恢复决策</span>
              <code>{recovery.summary.decisionCount} 次判定</code>
            </div>
            <strong data-status={
              recovery.summary.safetyProtectedCount > 0
                ? "warn"
                : recovery.summary.deniedCount > 0
                  ? "warn"
                  : "pass"
            }>
              {recovery.summary.safetyProtectedCount > 0
                ? `安全阻止 ${recovery.summary.safetyProtectedCount} 次`
                : recovery.summary.deniedCount > 0
                  ? `限制 ${recovery.summary.deniedCount} 次`
                  : `已执行 ${recovery.summary.allowedCount} 次`}
            </strong>
          </div>
          <div className="ai-dev-inspector__recovery-list">
            {[...recovery.decisions].reverse().slice(0, 4).map((decision, index) => (
              <div
                key={`${decision.round}-${decision.cause}-${decision.action}-${index}`}
                data-status={decision.allowed ? "pass" : "warn"}
              >
                <div>
                  <strong>{recoveryCauseLabel(decision.cause)}</strong>
                  <code title={decision.cause}>第 {decision.round} 轮</code>
                </div>
                <p>{recoveryDecisionLabel(decision)}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {trend ? (
        <div className="ai-dev-inspector__trend">
          <div className="ai-dev-inspector__report-heading">
            <div>
              <span>
                近期趋势 · {stabilityTrendScopeLabel(trend.scope.type)}
              </span>
              <code>
                最近 {trend.sampleSize}/{trend.windowLimit} 次运行
              </code>
            </div>
            <strong data-status={trendStatus}>
              {stabilityTrendVerdictLabel(trend.verdict)}
            </strong>
          </div>
          <div className="ai-dev-inspector__metrics">
            <div>
              <span>运行失败率</span>
              <strong>{formatStabilityTrendRate(trend.metrics.runFailureRate)}</strong>
            </div>
            <div>
              <span>稳定性失败率</span>
              <strong>{formatStabilityTrendRate(trend.metrics.stabilityFailureRate)}</strong>
            </div>
            <div>
              <span>协议异常率</span>
              <strong>{formatStabilityTrendRate(trend.metrics.toolProtocolRunRate)}</strong>
            </div>
            <div>
              <span>工具未收口率</span>
              <strong>{formatStabilityTrendRate(trend.metrics.incompleteToolRunRate)}</strong>
            </div>
            <div>
              <span>上下文溢出率</span>
              <strong>{formatStabilityTrendRate(trend.metrics.contextOverflowRunRate)}</strong>
            </div>
            <div>
              <span>连续异常</span>
              <strong>{trend.metrics.currentFailureStreak} 次</strong>
            </div>
            <div>
              <span>主要工具错误</span>
              <strong title={trend.metrics.topToolErrorCodes[0]?.code || ""}>
                {trend.metrics.topToolErrorCodes[0]
                  ? `${trend.metrics.topToolErrorCodes[0].code} × ${trend.metrics.topToolErrorCodes[0].count}`
                  : "—"}
              </strong>
            </div>
          </div>
          <div className="ai-dev-inspector__trend-gate">
            <div className="ai-dev-inspector__report-heading">
              <div>
                <span>滚动基线门禁</span>
                <code>
                  近期 {trend.regressionGate.candidateSampleSize} 次 / 上一窗口 {trend.regressionGate.baselineSampleSize} 次
                </code>
              </div>
              <strong data-status={
                trend.regressionGate.verdict === "insufficient_data"
                  ? "captured"
                  : trend.regressionGate.verdict
              }>
                {stabilityGateVerdictLabel(trend.regressionGate.verdict)}
              </strong>
            </div>
            {trend.regressionGate.alerts.length > 0 ? (
              <div className="ai-dev-inspector__trend-alerts">
                {trend.regressionGate.alerts.slice(0, 4).map((alert) => (
                  <div key={alert.code} data-status={alert.severity}>
                    {stabilityGateAlertText(alert)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="ai-dev-inspector__trend-note">
                {trend.regressionGate.verdict === "insufficient_data"
                  ? `两个窗口各需要至少 ${trend.regressionGate.minimumWindowSize} 次运行。`
                  : "近期窗口没有超过上一窗口的允许退化范围。"}
              </div>
            )}
          </div>
          {trend.verdict === "insufficient_data" ? (
            <div className="ai-dev-inspector__trend-note">
              至少需要 {trend.minimumSampleSize} 次运行后才启用阈值告警。
            </div>
          ) : trend.alerts.length > 0 ? (
            <div className="ai-dev-inspector__trend-alerts">
              {trend.alerts.map((alert) => (
                <div key={alert.code} data-status={alert.severity}>
                  {stabilityTrendAlertText(alert)}
                </div>
              ))}
            </div>
          ) : (
            <div className="ai-dev-inspector__trend-note">
              近期指标均未达到预警阈值。
            </div>
          )}
        </div>
      ) : null}
      {report ? (
        <details className="ai-dev-inspector__text-block">
          <summary>完整稳定性报告</summary>
          <pre>{formatJson({
            stability: report.stability,
            performance: report.performance,
            artifacts: report.artifacts,
            artifactMaintenance: report.artifactMaintenance,
            failureClassification: report.failureClassification,
            recovery: report.recovery,
            trend,
          })}</pre>
        </details>
      ) : null}
      <div className="ai-dev-inspector__report-actions">
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "读取中…" : "刷新指标"}
        </button>
        <button
          type="button"
          onClick={() => void maintainArtifacts()}
          disabled={maintaining || loading}
          title="只回收失效 writer claim，不删除 Artifact 内容"
        >
          {maintaining ? "维护中…" : "安全维护 Artifact"}
        </button>
        {maintenanceFeedback ? <span>{maintenanceFeedback}</span> : null}
      </div>
    </div>
  );
}

function ChildRunList({
  items,
  now,
}: {
  items: AiDebugChildRun[];
  now: number;
}) {
  if (!items.length) return null;
  return (
    <>
      <div className="ai-dev-inspector__section-title">
        <span>子 Agent / 子 Run</span>
        <small>
          {items.filter((item) => item.status === "completed").length}/{items.length} 完成
        </small>
      </div>
      <div className="ai-dev-inspector__child-runs">
        {items.map((child, index) => {
          const modelCalls = child.modelCalls.reduce(
            (sum, call) => sum + call.count,
            0,
          );
          const active = !["completed", "failed", "aborted"].includes(child.status);
          return (
            <details
              key={child.delegationId}
              className="ai-dev-inspector__child-run"
              data-status={child.status}
              open={active || child.status === "failed"}
            >
              <summary>
                <span>
                  <strong>{child.agentTitle || child.agentRole}</strong>
                  <small>#{index + 1}</small>
                </span>
                <span>{STATUS_LABELS[child.status]}</span>
              </summary>
              <div>
                {child.objective && <p>{child.objective}</p>}
                <dl>
                  <div><dt>Delegation</dt><dd title={child.delegationId}>{child.delegationId}</dd></div>
                  <div><dt>Child Run</dt><dd title={child.childRunId}>{child.childRunId || "等待创建"}</dd></div>
                  <div><dt>任务单元</dt><dd>{child.unitId || "—"}</dd></div>
                  <div><dt>执行尝试</dt><dd>{child.attempt ?? 1}</dd></div>
                  <div><dt>耗时</dt><dd>{formatDuration((child.finishedAt ?? now) - child.startedAt)}</dd></div>
                  <div><dt>模型调用</dt><dd>{modelCalls} 次</dd></div>
                  <div><dt>工具调用</dt><dd>{child.tools.length} 个</dd></div>
                </dl>
                {child.error && <div className="ai-dev-inspector__error">{child.error}</div>}
                {child.thinking && (
                  <details className="ai-dev-inspector__text-block">
                    <summary>子 Agent 思考 <small>{child.thinking.length} 字符</small></summary>
                    <pre>{child.thinking}</pre>
                  </details>
                )}
                {child.output && (
                  <details className="ai-dev-inspector__text-block">
                    <summary>子 Agent 输出 <small>{child.output.length} 字符</small></summary>
                    <pre>{child.output}</pre>
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

function Overview({ run, now }: { run: AiDebugRun; now: number }) {
  const elapsed = (run.finishedAt ?? now) - run.startedAt;
  const rootModelCallCount = run.modelCalls.reduce(
    (sum, call) => sum + call.count,
    0,
  );
  const childModelCallCount = run.childRuns.reduce(
    (sum, child) => sum + child.modelCalls.reduce(
      (childSum, call) => childSum + call.count,
      0,
    ),
    0,
  );
  const modelCallCount = rootModelCallCount + childModelCallCount;
  const modelCallRows = [
    ...run.modelCalls.map((call) => ({
      key: `root:${call.id}`,
      call,
      source: '根 Run',
    })),
    ...run.childRuns.flatMap((child) => child.modelCalls.map((call) => ({
      key: `${child.id}:${call.id}`,
      call,
      source: child.agentTitle || child.agentRole,
    }))),
  ];
  const modelToolNames = [...new Set(
    [
      ...run.modelCalls,
      ...run.childRuns.flatMap((child) => child.modelCalls),
    ].flatMap((call) => call.toolNames),
  )];
  const contextBudget = run.contextBudget as Record<string, unknown> | undefined;
  const resolvedOutputBudget = contextBudget?.outputBudget as Record<string, unknown> | undefined;
  const inputTokens = contextBudget
    ?.actualInputTokens ??
    contextBudget?.estimatedInputTokens;
  const outputTokens = contextBudget?.actualOutputTokens;
  const finishReason = String(contextBudget?.finishReason ?? "");
  return (
    <div className="ai-dev-inspector__section">
      <div className="ai-dev-inspector__metrics">
        <div><span>状态</span><strong>{STATUS_LABELS[run.status]}</strong></div>
        <div><span>任务类型</span><strong title={run.taskType}>{run.taskType}</strong></div>
        <div><span>耗时</span><strong>{formatDuration(elapsed)}</strong></div>
        <div><span>模型</span><strong title={run.model}>{run.model || "待返回"}</strong></div>
        <div><span>Token</span><strong>{String(inputTokens ?? "—")} / {String(outputTokens ?? "—")}</strong></div>
        <div>
          <span>本次输出预算</span>
          <strong>{formatTokens(resolvedOutputBudget?.effectiveTokens ?? contextBudget?.outputReserveTokens)}</strong>
        </div>
        <div>
          <span>模型输出上限</span>
          <strong>{formatTokens(resolvedOutputBudget?.modelMaxOutputTokens)}</strong>
        </div>
        <div>
          <span>结束原因</span>
          <strong>{MODEL_FINISH_REASON_LABELS[finishReason] ?? (finishReason || "—")}</strong>
        </div>
        <div>
          <span>模型调用</span>
          <strong title={`根 Run ${rootModelCallCount} 次 · 子 Run ${childModelCallCount} 次`}>
            {modelCallCount} 次
          </strong>
        </div>
        <div><span>传入工具</span><strong>{modelToolNames.length} 个</strong></div>
      </div>

      <div className="ai-dev-inspector__section-title">
        <span>本轮标识</span>
        <small>保存后回填记录 ID</small>
      </div>
      <div className="ai-dev-inspector__identifiers">
        <div>
          <span>对话记录 ID</span>
          <code>{run.conversationId != null ? `#${run.conversationId}` : "保存后生成"}</code>
        </div>
        <div>
          <span>会话 ID</span>
          <code>{run.sessionId != null ? `#${run.sessionId}` : "—"}</code>
        </div>
        <div>
          <span>Agent Run ID</span>
          <code title={run.agentRunId}>{run.agentRunId || "—"}</code>
        </div>
        <div>
          <span>Stream ID</span>
          <code title={run.id}>{run.id}</code>
        </div>
      </div>

      <ChildRunList items={run.childRuns} now={now} />

      {run.error && <div className="ai-dev-inspector__error">{run.error}</div>}
      <FailureDiagnosisCard
        report={run.errorReport}
        tools={run.tools}
        fallback={run.error}
      />
      <ErrorReportCard run={run} />
      <StabilityCard run={run} />

      <div className="ai-dev-inspector__section-title">
        <span>传入模型的工具函数</span>
        <small>
          {modelToolNames.length} 个 · 根 {rootModelCallCount} 次 / 子 {childModelCallCount} 次
        </small>
      </div>
      {modelToolNames.length > 0 ? (
        <div className="ai-dev-inspector__tool-chips ai-dev-inspector__tool-chips--catalog">
          {modelToolNames.map((name) => <code key={name}>{name}</code>)}
        </div>
      ) : (
        <div className="ai-dev-inspector__empty">
          {modelCallCount > 0 ? "本轮模型调用未传入工具函数" : "等待模型调用…"}
        </div>
      )}
      {modelCallRows.length > 0 ? (
        <details className="ai-dev-inspector__model-calls">
          <summary>
            模型调用明细
            <small>{modelCallCount} 次</small>
          </summary>
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

      <div className="ai-dev-inspector__section-title">
        <span>工具调用</span>
        <small>{run.tools.length} 个</small>
      </div>
      {run.tools.length > 0 ? (
        <div className="ai-dev-inspector__tool-list">
          {run.tools.map((tool) => <ToolCard key={`${tool.batchIndex}-${tool.id}`} tool={tool} />)}
        </div>
      ) : (
        <div className="ai-dev-inspector__empty">本轮尚未调用工具</div>
      )}

      {run.thinking && (
        <details className="ai-dev-inspector__text-block">
          <summary>思考过程 <small>{run.thinking.length} 字符</small></summary>
          <pre>{run.thinking}</pre>
        </details>
      )}
      <details className="ai-dev-inspector__text-block" open>
        <summary>模型输出 <small>{run.output.length} 字符</small></summary>
        <pre>{run.output || "等待模型输出…"}</pre>
      </details>

      {run.agentPlan !== undefined && (
        <details className="ai-dev-inspector__text-block">
          <summary>Core Planner 执行计划</summary>
          <pre>{formatJson(run.agentPlan)}</pre>
        </details>
      )}
      {run.contextBudget !== undefined && (
        <details className="ai-dev-inspector__text-block">
          <summary>上下文预算</summary>
          <pre>{formatJson(run.contextBudget)}</pre>
        </details>
      )}
    </div>
  );
}

function ContextView({ run }: { run: AiDebugRun }) {
  return (
    <div className="ai-dev-inspector__section">
      <details className="ai-dev-inspector__text-block">
        <summary>请求参数</summary>
        <pre>{formatJson(run.request.meta)}</pre>
      </details>
      <div className="ai-dev-inspector__section-title">
        <span>消息</span>
      </div>
      <div className="ai-dev-inspector__messages">
        {run.request.messages.map((message, index) => (
          <details
            className={`ai-dev-inspector__message ai-dev-inspector__message--${message.role}`}
            key={`${message.role}-${index}`}
            open={index >= run.request.messages.length - 2}
          >
            <summary>
              <span>{message.role}</span>
              <small>#{index + 1}</small>
            </summary>
            <pre>{formatJson(message.content)}</pre>
          </details>
        ))}
      </div>
    </div>
  );
}

function EventsView({ run }: { run: AiDebugRun }) {
  return (
    <div className="ai-dev-inspector__events">
      {run.events.map((event) => (
        <details className={`ai-dev-inspector__event ai-dev-inspector__event--${event.type}`} key={event.id}>
          <summary>
            <time>{formatTime(event.at)}</time>
            <span>{event.label}</span>
          </summary>
          {event.payload !== undefined && <pre>{formatJson(event.payload)}</pre>}
        </details>
      ))}
      {run.eventCount > run.events.length && (
        <div className="ai-dev-inspector__notice">
          为控制内存，仅保留最近 {run.events.length} 条事件。
        </div>
      )}
    </div>
  );
}

function ReportsView({
  onOpenRun,
  availableRunIds,
}: {
  onOpenRun: (streamId: string) => void;
  availableRunIds: ReadonlySet<string>;
}) {
  const [reports, setReports] = React.useState<AiErrorReport[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  const loadReports = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await services.ai.listAiErrorReports({ limit: 50 });
      if (!result.success) {
        setError(result.error || "读取错误报告失败");
        return;
      }
      setReports(result.data || []);
    } catch {
      setError("读取错误报告失败");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadReports();
  }, [loadReports]);

  return (
    <div className="ai-dev-inspector__reports">
      <div className="ai-dev-inspector__reports-toolbar">
        <span>本地报告不会包含提示词或章节正文</span>
        <button type="button" onClick={loadReports} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>
      {error ? <div className="ai-dev-inspector__error">{error}</div> : null}
      {!loading && reports.length === 0 ? (
        <div className="ai-dev-inspector__empty">暂时没有错误报告</div>
      ) : null}
      {reports.map((report) => (
        <article className="ai-dev-inspector__report-item" key={report.id}>
          <header>
            <code title={report.id}>{report.id}</code>
            <span data-status={report.status}>
              {report.status === "captured"
                ? "已记录"
                : report.status === "submitted"
                  ? "待排查"
                  : "已解决"}
            </span>
          </header>
          <p>{report.errorMessage}</p>
          <FailureDiagnosisCard report={report} compact />
          <dl>
            <div><dt>来源</dt><dd>{report.source}</dd></div>
            <div>
              <dt>任务类型</dt>
              <dd>{String(report.diagnostics?.taskType || "—")}</dd>
            </div>
            <div><dt>时间</dt><dd>{new Date(report.createTime).toLocaleString("zh-CN")}</dd></div>
            <div><dt>Agent Run</dt><dd>{report.agentRunId || "—"}</dd></div>
          </dl>
          {report.userNote ? <blockquote>{report.userNote}</blockquote> : null}
          <button
            type="button"
            onClick={() => onOpenRun(report.streamId)}
            disabled={!availableRunIds.has(report.streamId)}
            title={
              availableRunIds.has(report.streamId)
                ? "在开发面板中定位本轮"
                : "本轮已不在开发面板的内存记录中"
            }
          >
            {availableRunIds.has(report.streamId) ? "定位本轮" : "仅保留持久记录"}
          </button>
        </article>
      ))}
    </div>
  );
}

export default function AiDevInspector() {
  const snapshot = React.useSyncExternalStore(
    subscribeAiDebugStore,
    getAiDebugSnapshot,
    getAiDebugSnapshot,
  );
  const selectedRun =
    snapshot.runs.find((run) => run.id === snapshot.selectedRunId) ?? snapshot.runs[0];
  const availableRunIds = React.useMemo(
    () => new Set(snapshot.runs.map((run) => run.id)),
    [snapshot.runs],
  );
  const [tab, setTab] = React.useState<InspectorTab>("overview");
  const [collapsed, setCollapsed] = React.useState(false);
  const [hidden, setHidden] = React.useState(true);
  const [position, setPosition] = React.useState<Position>(
    () => clampPosition(loadPosition(), false),
  );
  const [now, setNow] = React.useState(Date.now);
  const lastOpenedRunRef = React.useRef<string | null>(null);
  const dragRef = React.useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    origin: Position;
  } | null>(null);

  React.useEffect(() => {
    if (!selectedRun || lastOpenedRunRef.current === selectedRun.id) return;
    lastOpenedRunRef.current = selectedRun.id;
    setHidden(false);
    setCollapsed(false);
    setTab("overview");
  }, [selectedRun?.id]);

  React.useEffect(() => {
    if (!isRunActive(selectedRun)) return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [selectedRun?.id, selectedRun?.status]);

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
    if ((event.target as HTMLElement).closest("button, select")) return;
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
  const status = selectedRun?.status ?? "starting";

  return (
    <aside
      className={`ai-dev-inspector ${collapsed ? "ai-dev-inspector--collapsed" : ""}`}
      style={{ left: position.x, top: position.y }}
      aria-label="AI 对话开发状态"
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
            <strong>{collapsed ? "AI 调试" : "AI 对话诊断"}</strong>
            <small>
              {collapsed && selectedRun
                ? STATUS_LABELS[selectedRun.status]
                : selectedRun?.source ?? "开发模式"}
            </small>
          </div>
        </div>
        <div className="ai-dev-inspector__header-actions">
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? "展开调试面板" : "折叠调试面板"}
            title={collapsed ? "展开" : "折叠"}
          >
            {collapsed ? "▣" : "—"}
          </button>
          {!collapsed && (
            <button type="button" onClick={() => setHidden(true)} aria-label="关闭调试面板" title="下次 AI 对话时自动打开">
              ×
            </button>
          )}
        </div>
      </header>

      {!collapsed && (
        <>
          <div className="ai-dev-inspector__runbar">
            <select
              value={selectedRun?.id ?? ""}
              onChange={(event) => selectAiDebugRun(event.target.value)}
              disabled={snapshot.runs.length === 0}
              aria-label="选择 AI 运行"
            >
              {snapshot.runs.length === 0 ? (
                <option value="">等待第一次 AI 对话…</option>
              ) : snapshot.runs.map((run) => (
                <option value={run.id} key={run.id}>
                  {formatTime(run.startedAt)} · {run.conversationId != null ? `#${run.conversationId} · ` : ""}{run.source} · {STATUS_LABELS[run.status]}
                </option>
              ))}
            </select>
            {selectedRun && <StatusPill status={selectedRun.status} />}
            <button type="button" onClick={clearAiDebugRuns} disabled={snapshot.runs.length === 0}>
              清空
            </button>
          </div>

          <nav className="ai-dev-inspector__tabs" aria-label="调试信息分类">
            {([
              ["overview", "过程", undefined],
              ["context", "上下文", undefined],
              ["events", "事件", selectedRun?.eventCount],
              ["reports", "错误", undefined],
            ] as const).map(([key, label, count]) => (
              <button
                type="button"
                className={tab === key ? "is-active" : ""}
                onClick={() => setTab(key)}
                key={key}
              >
                <span>{label}</span>
                {count != null ? <small>{count}</small> : null}
              </button>
            ))}
          </nav>

          <div className="ai-dev-inspector__body">
            {tab === "reports" ? (
              <ReportsView
                availableRunIds={availableRunIds}
                onOpenRun={(streamId) => {
                  selectAiDebugRun(streamId);
                  setTab("overview");
                }}
              />
            ) : !selectedRun ? (
              <div className="ai-dev-inspector__waiting">
                <span className="ai-dev-inspector__waiting-icon">⌁</span>
                <strong>等待 AI 对话</strong>
                <p>任一 AI 流开始后，这里会自动显示本轮上下文、工具和实时事件。</p>
              </div>
            ) : tab === "overview" ? (
              <Overview run={selectedRun} now={now} />
            ) : tab === "context" ? (
              <ContextView run={selectedRun} />
            ) : (
              <EventsView run={selectedRun} />
            )}
          </div>

          <footer className="ai-dev-inspector__footer">
            <span>开发模式</span>
            <code>{selectedRun?.id ?? "idle"}</code>
          </footer>
        </>
      )}
    </aside>
  );
}
