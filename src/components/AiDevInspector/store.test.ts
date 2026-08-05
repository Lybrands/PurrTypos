import assert from "node:assert/strict";
import test from "node:test";
import {
  clearAiDebugRuns,
  getAiDebugSnapshot,
  recordAiDebugChunk,
  recordAiDebugRunContinuation,
  startAiDebugRun,
} from "./store.ts";

test("debug store preserves a rejected tool's concrete failure", () => {
    clearAiDebugRuns();
    startAiDebugRun("screenplay-test", {
      apiKey: "key",
      messages: [{ role: "user", content: "分析限定范围" }],
      options: { model: "model" },
      agentProfile: "screenplay",
      screenplayProjectId: "project-1",
      enableAgentTools: true,
    });
    recordAiDebugChunk("screenplay-test", {
      toolCallsInProgress: true,
      toolCalls: [{
        id: "call-scope",
        type: "function",
        function: { name: "getSourceCharacters", arguments: "{}" },
      }],
    });
    recordAiDebugChunk("screenplay-test", {
      toolIndexCompleted: 0,
      toolCallId: "call-scope",
      toolName: "getSourceCharacters",
      toolOutcome: "rejected",
      toolErrorCode: "tool_scope_violation",
    });
    recordAiDebugChunk("screenplay-test", {
      toolResults: [{
        tool_call_id: "call-scope",
        name: "getSourceCharacters",
        content: JSON.stringify({
          success: false,
          errorCode: "tool_scope_violation",
          error: "Character dossiers are outside the restricted adaptation range.",
          diagnostics: {
            stage: "schema_validation",
            path: "$.sceneText",
            actualChars: 24001,
            maxChars: 24000,
          },
        }),
      }],
    });

    const tool = getAiDebugSnapshot().runs[0].tools[0];
    assert.equal(tool.status, "failed");
    assert.equal(tool.outcome, "rejected");
    assert.equal(tool.errorCode, "tool_scope_violation");
    assert.match(tool.errorMessage || "", /restricted adaptation range/);
    assert.deepEqual(tool.diagnostics, {
      stage: "schema_validation",
      path: "$.sceneText",
      actualChars: 24001,
      maxChars: 24000,
    });
});

test("debug store does not treat a successful tool message as an error", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-success", {
    apiKey: "key",
    messages: [{ role: "user", content: "提交正式提案" }],
    options: { model: "model" },
    agentProfile: "screenplay",
    screenplayProjectId: "project-1",
    enableAgentTools: true,
  });
  recordAiDebugChunk("screenplay-success", {
    toolCallsInProgress: true,
    toolCalls: [{
      id: "call-proposal",
      type: "function",
      function: { name: "proposeSourceAnalysis", arguments: "{}" },
    }],
  });
  recordAiDebugChunk("screenplay-success", {
    toolIndexCompleted: 0,
    toolCallId: "call-proposal",
    toolName: "proposeSourceAnalysis",
    toolOutcome: "completed",
  });
  recordAiDebugChunk("screenplay-success", {
    toolResults: [{
      tool_call_id: "call-proposal",
      name: "proposeSourceAnalysis",
      content: JSON.stringify({
        success: true,
        status: "awaiting_user_review",
        message: "Proposal delivered for user review.",
      }),
    }],
  });

  const tool = getAiDebugSnapshot().runs[0].tools[0];
  assert.equal(tool.status, "completed");
  assert.equal(tool.errorCode, undefined);
  assert.equal(tool.errorMessage, undefined);
});

test("debug store identifies durable screenplay task type", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-long-task", {
    apiKey: "key",
    messages: [{ role: "user", content: "创作剩余全部场景" }],
    options: { model: "model" },
    agentProfile: "screenplay",
    screenplayProjectId: "project-1",
    activeStage: "draft",
    screenplayTaskIntent: "stage_deliverable",
    enableAgentTools: true,
  });
  assert.equal(
    getAiDebugSnapshot().runs[0].taskType,
    "剧本阶段交付 · 场景正文",
  );

  recordAiDebugChunk("screenplay-long-task", {
    longTaskDispatched: {
      runId: "run-1",
      taskId: "task-1",
      kind: "screenplay_draft_generation",
      status: "pending",
      totalUnits: 5,
      completedUnits: 0,
    },
    delta: "已创建长篇正文任务。",
  });

  assert.equal(
    getAiDebugSnapshot().runs[0].taskType,
    "持久化长任务 · 剧本正文分批创作",
  );

  recordAiDebugChunk("screenplay-long-task", { done: true });
  assert.equal(getAiDebugSnapshot().runs[0].status, "dispatched");
});

test("durable child activity is accounted under the orchestration root", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-workflow", {
    apiKey: "key",
    messages: [{ role: "user", content: "连续创作" }],
    options: { model: "model" },
    agentProfile: "screenplay",
    screenplayProjectId: "project-1",
    enableAgentTools: true,
  });
  recordAiDebugChunk("screenplay-workflow", {
    longTaskDispatched: {
      runId: "run-root",
      taskId: "task-1",
      kind: "screenplay_draft_generation",
      status: "running",
      totalUnits: 3,
      completedUnits: 0,
    },
  });
  recordAiDebugChunk("screenplay-workflow", { done: true });

  recordAiDebugRunContinuation("run-root", {
    agentRunStarted: { runId: "run-child", status: "running" },
  });
  recordAiDebugRunContinuation("run-root", {
    modelInvocation: {
      phase: "generation",
      count: 1,
      round: 1,
      toolNames: [],
    },
  });
  recordAiDebugRunContinuation("run-root", { done: true });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.agentRunId, "run-root");
  assert.equal(run.modelCalls.length, 1);
  assert.equal(run.status, "completed");
  assert.ok((run.finishedAt || 0) >= run.startedAt);
});

test("delegated child Runs keep independent diagnostics under the root", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-multi-agent", {
    apiKey: "key",
    messages: [{ role: "user", content: "并行创作" }],
    options: { model: "root-model" },
    agentProfile: "screenplay",
    screenplayProjectId: "project-1",
    enableAgentTools: true,
  });
  recordAiDebugChunk("screenplay-multi-agent", {
    agentRunStarted: { runId: "run-root", status: "running" },
  });
  recordAiDebugChunk("screenplay-multi-agent", {
    agentDelegationCreated: {
      runId: "run-root",
      delegationId: "delegation-writer-a",
      parentRunId: "run-root",
      rootRunId: "run-root",
      childRunId: "run-child-a",
      agentRole: "screenplay_writer",
      agentTitle: "剧本 Writer · ep05",
      objective: "创作第五集",
      unitId: "ep05",
      attempt: 2,
      status: "running",
      required: true,
      priority: 0,
    },
  });
  recordAiDebugChunk("screenplay-multi-agent", {
    agentSubRunEvent: {
      runId: "run-root",
      parentRunId: "run-root",
      rootRunId: "run-root",
      delegationId: "delegation-writer-a",
      childRunId: "run-child-a",
      agentRole: "screenplay_writer",
      agentTitle: "剧本 Writer · ep05",
      objective: "创作第五集",
      unitId: "ep05",
      attempt: 2,
      chunk: {
        modelInvocation: {
          phase: "generation",
          count: 1,
          round: 1,
          toolNames: [],
        },
        thinkingDelta: "检查连续性",
      },
    },
  });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.agentRunId, "run-root");
  assert.equal(run.modelCalls.length, 0);
  assert.equal(run.childRuns.length, 1);
  assert.equal(run.childRuns[0].childRunId, "run-child-a");
  assert.equal(run.childRuns[0].unitId, "ep05");
  assert.equal(run.childRuns[0].attempt, 2);
  assert.equal(run.childRuns[0].modelCalls.length, 1);
  assert.equal(run.childRuns[0].thinking, "检查连续性");
});
