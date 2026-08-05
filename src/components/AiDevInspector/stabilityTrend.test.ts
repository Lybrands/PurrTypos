import assert from "node:assert/strict";
import test from "node:test";

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
} from "./stabilityTrend.ts";

test("stability trend labels keep internal alert codes out of the UI", () => {
  assert.equal(stabilityTrendVerdictLabel("insufficient_data"), "样本不足");
  assert.equal(stabilityTrendScopeLabel("screenplay_project"), "当前剧本项目");
  assert.equal(formatStabilityTrendRate(0.126), "13%");
  assert.equal(
    stabilityTrendAlertText({
      code: "incompleteToolRunRate",
      severity: "fail",
      value: 0.2,
      threshold: 0.1,
    }),
    "工具未收口率 20%，达到异常阈值 10%",
  );
});

test("controlled recovery decisions explain retries and safety denials in Chinese", () => {
  assert.equal(
    recoveryCauseLabel("tool_input_invalid"),
    "工具 JSON 或 Schema 参数无效",
  );
  assert.equal(
    recoveryDecisionLabel({
      round: 2,
      cause: "tool_input_invalid",
      action: "retry_model",
      allowed: true,
      reasonCode: "allowed",
      attempt: 1,
      maxAttempts: 1,
      remainingModelRounds: 3,
      effectState: "not_started",
      mayRepeatSideEffect: true,
    }),
    "重新生成当前模型轮次，使用第 1/1 次额度。",
  );
  assert.equal(
    recoveryDecisionLabel({
      round: 2,
      cause: "tool_input_invalid",
      action: "retry_model",
      allowed: false,
      reasonCode: "side_effect_state_unknown",
      attempt: 0,
      maxAttempts: 1,
      remainingModelRounds: 3,
      effectState: "unknown",
      mayRepeatSideEffect: true,
    }),
    "无法确认工具是否已产生副作用，为避免重复写入已阻止重放。",
  );
});

test("failure streak alerts use counts instead of percentages", () => {
  assert.equal(
    stabilityTrendAlertText({
      code: "failureStreak",
      severity: "warn",
      value: 2,
      threshold: 2,
    }),
    "连续异常次数 2 次，达到预警阈值 2 次",
  );
});

test("failure classifications and remediation keys become Chinese guidance", () => {
  const finding = {
    code: "tool.incomplete_terminalization",
    category: "tool_lifecycle",
    severity: "fail" as const,
    confidence: "high" as const,
    evidence: {incompleteCalls: 1},
    remediation: "inspect_tool_terminalization",
  };
  assert.equal(failureFindingLabel(finding), "工具调用未收口");
  assert.equal(
    failureRemediationLabel(finding),
    "检查工具完成事件、结果回写和取消竞态。",
  );
});

test("rolling gate alerts describe regressions without exposing internal check names", () => {
  assert.equal(stabilityGateVerdictLabel("fail"), "检测到回归");
  assert.equal(
    stabilityGateAlertText({
      code: "rateRegression:contextOverflowRunRate",
      severity: "fail",
      detail: {delta: 0.2},
    }),
    "上下文溢出率较上一窗口上升 20%",
  );
  assert.equal(
    stabilityGateAlertText({
      code: "newCriticalSignals",
      severity: "fail",
      detail: {metrics: ["incompleteToolRuns", "contextOverflowRuns"]},
    }),
    "近期首次出现严重信号：工具未收口、上下文溢出",
  );
});
