import type {
  AiAgentRunFailureFinding,
  AiAgentRunRecoveryDecision,
  AiAgentRunStabilityRegressionGate,
  AiAgentRunStabilityTrendAlert,
  AiAgentRunStabilityTrendReport,
} from "../../types";

const RECOVERY_CAUSE_LABELS: Record<string, string> = {
  provider_required_tool_choice_unsupported: "供应商不支持强制工具模式",
  provider_stream_interrupted: "模型响应流中断",
  model_output_truncated: "模型输出被截断",
  malformed_tool_call_batch: "工具调用协议包不完整",
  missing_required_tool_call: "模型遗漏必需工具调用",
  missing_required_tool_call_replan: "必需工具步骤需要重规划",
  unstructured_tool_protocol: "模型把工具协议输出成了普通文本",
  empty_model_response: "模型没有生成可见回答",
  deferred_model_response: "模型只声明了后续动作",
  response_constraint_deterministic: "回答未通过结构约束",
  response_constraint_semantic: "回答未通过语义校验",
  future_tool_step: "模型提前调用了后续步骤工具",
  unauthorized_tool: "模型请求了未授权工具",
  tool_input_invalid: "工具 JSON 或 Schema 参数无效",
  tool_execution_failed_replan: "工具执行失败后需要重规划",
};

const RECOVERY_ACTION_LABELS: Record<string, string> = {
  retry_model: "重新生成当前模型轮次",
  fallback_provider_mode: "切换供应商兼容模式",
  replan: "重新规划剩余步骤",
};

const RECOVERY_DENIAL_LABELS: Record<string, string> = {
  attempt_budget_exhausted: "本类恢复次数已经用完，未继续重试。",
  cause_not_retryable: "该故障不满足安全恢复条件，已停止。",
  policy_disabled: "当前 Agent 配置未启用这类自动恢复。",
  request_canceled: "请求已经取消，未安排新的恢复动作。",
  round_budget_exhausted: "剩余模型轮次不足，未安排恢复动作。",
  side_effect_committed: "工具副作用已经提交，为避免重复写入已阻止重放。",
  side_effect_state_unknown: "无法确认工具是否已产生副作用，为避免重复写入已阻止重放。",
  visible_output_already_emitted: "回答正文已经对用户可见，为避免重复输出已阻止重试。",
};

const FINDING_LABELS: Record<string, string> = {
  "tool.protocol_invalid_arguments": "工具参数协议不合法",
  "tool.arguments_too_large": "工具参数超过传输上限",
  "model.tool_call_truncated": "模型工具调用输出被截断",
  "tool.incomplete_terminalization": "工具调用未收口",
  "context.overflow": "上下文预算溢出",
  "context.compaction_failed": "上下文压缩失败",
  "context.compaction_fallback": "上下文压缩发生降级",
  "planner.contract_failure": "规划器输出违反契约",
  "tool.missing_required_call": "缺少当前步骤要求的工具调用",
  "tool.authorization_rejected": "工具调用未通过授权",
  "tool.execution_failed": "工具处理器执行失败",
  "model.interrupted": "模型响应流中断",
  "model.retry_recovered": "模型调用重试后恢复",
  "run.failed_without_specific_cause": "运行失败但诊断证据不足",
};

const REMEDIATION_LABELS: Record<string, string> = {
  inspect_tool_contract: "检查工具 Schema、字段所有权和模型参数生成约定。",
  inspect_tool_payload_strategy: "检查是否应改为 Artifact 分批提交或资源引用。",
  inspect_model_output_budget: "检查模型输出预算、截断结束原因和干净重试记录。",
  inspect_tool_terminalization: "检查工具完成事件、结果回写和取消竞态。",
  inspect_context_budget: "检查模型窗口、预留预算和压缩触发时机。",
  inspect_context_compaction: "检查摘要生成、历史一致性和持久化状态。",
  inspect_compaction_summarizer: "检查摘要模型降级原因和备用摘要质量。",
  inspect_planner_contract: "检查规划响应契约和规划失败的原始错误码。",
  inspect_tool_choice_contract: "检查当前步骤授权工具与模型 tool choice。",
  inspect_tool_authorization: "检查工具作用域、风险策略和审批结果。",
  inspect_tool_handler: "检查工具处理器异常类型、幂等回执和事务结果。",
  inspect_model_transport: "检查供应商连接、超时、结束原因和重试记录。",
  inspect_terminal_error_and_trace_coverage: "检查终态错误以及缺失的运行 Trace。",
};

const CHECK_LABELS: Record<string, string> = {
  failedRuns: "运行失败",
  stabilityFailedRuns: "稳定性失败",
  toolProtocolFailureRuns: "工具协议异常",
  incompleteToolRuns: "工具未收口",
  contextOverflowRuns: "上下文溢出",
  compactionFailureRuns: "上下文压缩失败",
  runFailureRate: "运行失败率",
  stabilityFailureRate: "稳定性失败率",
  toolProtocolRunRate: "工具协议异常率",
  incompleteToolRunRate: "工具未收口率",
  contextOverflowRunRate: "上下文溢出率",
  compactionFailureRunRate: "压缩失败率",
  retryRunRate: "模型重试率",
  failureStreak: "连续异常次数",
};

const SCOPE_LABELS: Record<string, string> = {
  session: "当前会话",
  book: "当前书籍",
  screenplay_project: "当前剧本项目",
  global: "全部 Agent",
};

export function stabilityTrendVerdictLabel(
  verdict: AiAgentRunStabilityTrendReport["verdict"],
): string {
  if (verdict === "pass") return "趋势稳定";
  if (verdict === "warn") return "趋势预警";
  if (verdict === "fail") return "趋势异常";
  return "样本不足";
}

export function stabilityTrendScopeLabel(scope: string): string {
  return SCOPE_LABELS[scope] || "近期运行";
}

export function formatStabilityTrendRate(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export function stabilityTrendAlertText(
  alert: AiAgentRunStabilityTrendAlert,
): string {
  const label = CHECK_LABELS[alert.code] || alert.code;
  const isStreak = alert.code === "failureStreak";
  const value = isStreak
    ? `${alert.value ?? 0} 次`
    : formatStabilityTrendRate(alert.value);
  const threshold = isStreak
    ? `${alert.threshold} 次`
    : formatStabilityTrendRate(alert.threshold);
  return `${label} ${value}，达到${alert.severity === "fail" ? "异常" : "预警"}阈值 ${threshold}`;
}

export function failureFindingLabel(finding: AiAgentRunFailureFinding): string {
  return FINDING_LABELS[finding.code] || finding.code;
}

export function failureRemediationLabel(
  finding: AiAgentRunFailureFinding,
): string {
  return REMEDIATION_LABELS[finding.remediation] || "检查该分类关联的持久化证据。";
}

export function recoveryCauseLabel(cause: string): string {
  return RECOVERY_CAUSE_LABELS[cause] || cause;
}

export function recoveryDecisionLabel(
  decision: AiAgentRunRecoveryDecision,
): string {
  if (!decision.allowed) {
    return RECOVERY_DENIAL_LABELS[decision.reasonCode]
      || `恢复被拒绝：${decision.reasonCode}`;
  }
  const action = RECOVERY_ACTION_LABELS[decision.action] || decision.action;
  return `${action}，使用第 ${decision.attempt}/${decision.maxAttempts} 次额度。`;
}

export function stabilityGateVerdictLabel(
  verdict: AiAgentRunStabilityRegressionGate["verdict"],
): string {
  if (verdict === "pass") return "未发生回归";
  if (verdict === "warn") return "轻微退化";
  if (verdict === "fail") return "检测到回归";
  return "基线不足";
}

export function stabilityGateAlertText(
  alert: AiAgentRunStabilityRegressionGate["alerts"][number],
): string {
  if (alert.code === "candidateHealth") {
    return "近期窗口本身已经达到稳定性告警阈值。";
  }
  if (alert.code.startsWith("rateRegression:")) {
    const metric = alert.code.slice("rateRegression:".length);
    const delta = typeof alert.detail.delta === "number" ? alert.detail.delta : null;
    return `${CHECK_LABELS[metric] || metric}较上一窗口上升 ${formatStabilityTrendRate(delta)}`;
  }
  if (alert.code === "failureStreakRegression") {
    const delta = typeof alert.detail.delta === "number" ? alert.detail.delta : 0;
    return `连续异常较上一窗口增加 ${delta} 次。`;
  }
  if (alert.code === "newToolErrorCodes") {
    const codes = Array.isArray(alert.detail.codes) ? alert.detail.codes.join("、") : "未知";
    return `近期出现新的工具错误码：${codes}`;
  }
  if (alert.code === "newCriticalSignals") {
    const metrics = Array.isArray(alert.detail.metrics)
      ? alert.detail.metrics.map((metric) => CHECK_LABELS[String(metric)] || String(metric))
      : [];
    return `近期首次出现严重信号：${metrics.join("、") || "未知"}`;
  }
  return "近期稳定性指标较上一窗口发生退化。";
}
