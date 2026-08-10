import assert from "node:assert/strict";
import test from "node:test";
import type { AiAgentRunSnapshot } from "../../types.ts";
import {
  clearAiDebugRuns,
  getAiDebugSnapshot,
  groupAiDebugRunsByTurn,
  hydrateAiDebugRunSnapshot,
  recordAiDebugChunk,
  recordAiDebugRunContinuation,
  recordScreenplayAiDebugChunk,
  startAiDebugRun,
} from "./store.ts";

test('diagnostics group multiple Runs under their conversation turn', () => {
  clearAiDebugRuns();
  const request = {
    apiKey: 'key',
    sessionId: 7,
    messages: [{ role: 'user' as const, content: '继续创作三集' }],
    options: { model: 'model' },
    enableAgentTools: true,
  };
  startAiDebugRun('turn-1-planner', request, { turnId: 'turn-1' });
  startAiDebugRun('turn-1-writer', request, { turnId: 'turn-1' });
  startAiDebugRun('turn-2-planner', {
    ...request,
    messages: [{ role: 'user' as const, content: '检查第五集' }],
  }, { turnId: 'turn-2' });

  const turns = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs);
  assert.equal(turns.length, 2);
  assert.deepEqual(turns.map((turn) => turn.prompt), ['检查第五集', '继续创作三集']);
  assert.deepEqual(
    turns.find((turn) => turn.prompt === '继续创作三集')?.runs.map((run) => run.id),
    ['turn-1-writer', 'turn-1-planner'],
  );
});

function persistedSnapshot(
  events: AiAgentRunSnapshot['events'],
  options: {
    runId?: string;
    status?: AiAgentRunSnapshot['run']['status'];
    nextCursor?: number;
    hasMore?: boolean;
  } = {},
): AiAgentRunSnapshot {
  const status = options.status ?? 'running';
  const runId = options.runId ?? 'run-recovered';
  return {
    version: 1,
    run: {
      runId,
      sessionId: 7,
      conversationId: null,
      status,
      mode: 'agent',
      lineage: {
        parentRunId: null,
        rootRunId: runId,
        delegationId: null,
        agentRole: null,
        agentTitle: null,
        depth: 0,
      },
      finalResponse: '',
      createdAt: '2026-08-05 17:00:00',
      updatedAt: '2026-08-05 17:00:10',
      execution: {
        attempt: 1,
        cancellationRequested: false,
      },
      provenance: {
        modelProvider: 'openai',
        modelName: 'mimo-v2.5-pro',
      },
    },
    todos: [],
    events,
    delegations: {
      items: [],
      aggregate: {
        state: 'ready',
        counts: {},
        requiredFailures: [],
        results: [],
      },
    },
    nextCursor: options.nextCursor ?? events.at(-1)?.cursor ?? 0,
    hasMore: options.hasMore ?? false,
  };
}

test('persisted recovery restores Planner calls without duplicating cursors', () => {
  clearAiDebugRuns();
  const initial = persistedSnapshot([
    {
      version: 1,
      cursor: 1,
      type: 'run.started',
      runId: 'run-recovered',
      payload: {},
      chunk: { agentRunStarted: { runId: 'run-recovered', status: 'running' } },
    },
    {
      version: 1,
      cursor: 2,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: {
        modelInvocation: {
          phase: 'planning',
          count: 1,
          toolNames: [],
          parameters: { messageCount: 2 },
        },
      },
    },
    {
      version: 1,
      cursor: 3,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: {
        modelInvocation: {
          phase: 'planning',
          count: 1,
          toolNames: [],
          parameters: { messageCount: 4 },
        },
      },
    },
  ]);

  hydrateAiDebugRunSnapshot({
    snapshot: initial,
    prompt: '继续创作三集',
    source: '剧本 Agent',
  });
  hydrateAiDebugRunSnapshot({
    snapshot: initial,
    prompt: '继续创作三集',
    source: '剧本 Agent',
  });

  let run = getAiDebugSnapshot().runs[0];
  assert.equal(run.id, 'screenplay-run-recovered');
  assert.equal(run.agentRunId, 'run-recovered');
  assert.equal(run.modelCalls.length, 2);
  assert.deepEqual(
    run.modelCalls.map((call) => call.parameters?.messageCount),
    [2, 4],
  );

  hydrateAiDebugRunSnapshot({
    snapshot: persistedSnapshot([{
      version: 1,
      cursor: 4,
      type: 'long_task.dispatched',
      runId: 'run-recovered',
      payload: {},
      chunk: {
        longTaskDispatched: {
          runId: 'run-recovered',
          taskId: 'task-1',
          kind: 'screenplay_draft_generation',
          status: 'running',
          totalUnits: 7,
          completedUnits: 0,
        },
      },
    }], { nextCursor: 4 }),
    prompt: '继续创作三集',
    source: '剧本 Agent',
  });

  run = getAiDebugSnapshot().runs[0];
  assert.equal(run.status, 'dispatched');
  assert.equal(run.taskType, '持久化长任务 · 剧本正文分批创作');
  assert.equal(run.modelCalls.length, 2);
});

test('persisted recovery replaces a detached partial live diagnostic copy', () => {
  clearAiDebugRuns();
  startAiDebugRun('live-before-detach', {
    apiKey: 'key',
    messages: [{ role: 'user', content: '继续创作三集' }],
    options: { model: 'model' },
    enableAgentTools: true,
  });
  recordAiDebugChunk('live-before-detach', {
    agentRunStarted: { runId: 'run-recovered', status: 'running' },
  });
  recordAiDebugChunk('live-before-detach', {
    modelInvocation: {
      phase: 'generation',
      count: 1,
      toolNames: [],
    },
  });

  hydrateAiDebugRunSnapshot({
    snapshot: persistedSnapshot([{
      version: 1,
      cursor: 1,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: {
        modelInvocation: {
          phase: 'planning',
          count: 1,
          toolNames: [],
        },
      },
    }]),
    prompt: '继续创作三集',
    source: '剧本 Agent',
  });

  const runs = getAiDebugSnapshot().runs;
  assert.equal(runs.length, 1);
  assert.equal(runs[0].id, 'screenplay-run-recovered');
  assert.deepEqual(runs[0].modelCalls.map((call) => call.phase), ['planning']);
});

test('a terminal Snapshot cannot be reopened by late screenplay chunk replay', () => {
  clearAiDebugRuns();
  const input = {
    runId: 'run-terminal-race',
    turnId: 'turn-terminal-race',
    sessionId: 9,
    prompt: '继续创作下一集',
    model: 'model',
  };
  recordScreenplayAiDebugChunk({
    ...input,
    chunk: { reasoningDelta: '实时片段' },
  });

  hydrateAiDebugRunSnapshot({
    snapshot: persistedSnapshot([], {
      runId: input.runId,
      status: 'done',
    }),
    turnId: input.turnId,
    prompt: input.prompt,
    source: '剧本 Agent 对话',
  });
  const completed = getAiDebugSnapshot().runs[0];
  const finishedAt = completed.finishedAt;
  assert.equal(completed.id, 'screenplay-run-terminal-race');
  assert.equal(completed.status, 'completed');
  assert.equal(completed.reasoning, '实时片段');
  assert.ok(finishedAt);

  recordScreenplayAiDebugChunk({
    ...input,
    chunk: { modelContentDelta: '晚到的历史片段' },
  });
  const runs = getAiDebugSnapshot().runs;
  assert.equal(runs.length, 1);
  assert.equal(runs[0].status, 'completed');
  assert.equal(runs[0].finishedAt, finishedAt);
  assert.equal(runs[0].modelContent, '晚到的历史片段');
});

test("debug store preserves a rejected tool's concrete failure", () => {
    clearAiDebugRuns();
    startAiDebugRun("screenplay-test", {
      apiKey: "key",
      messages: [{ role: "user", content: "分析限定范围" }],
      options: { model: "model" },
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

test("debug store identifies a durable screenplay chunk", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-long-task", {
    apiKey: "key",
    messages: [{ role: "user", content: "创作剩余全部场景" }],
    options: { model: "model" },
    chatAgentMode: "agent",
    enableAgentTools: true,
  });
  assert.equal(
    getAiDebugSnapshot().runs[0].taskType,
    "剧本 Agent 任务",
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

test("screenplay persisted SSE creates a live diagnostic Run", () => {
  clearAiDebugRuns();
  recordScreenplayAiDebugChunk({
    runId: "run-live-screenplay",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: {
      agentRunStarted: {
        runId: "run-live-screenplay",
        status: "running",
      },
    },
  });
  recordScreenplayAiDebugChunk({
    runId: "run-live-screenplay",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: { reasoningDelta: "先检查场景连续性" },
  });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.source, "剧本 Agent 对话");
  assert.equal(run.taskType, "剧本 Agent 任务");
  assert.equal(run.agentRunId, "run-live-screenplay");
  assert.equal(run.turnId, "turn-live-screenplay");
  assert.equal(run.reasoning, "先检查场景连续性");

  recordScreenplayAiDebugChunk({
    runId: "run-live-screenplay-writer",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: { commentaryDelta: "开始创作" },
  });
  const turns = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs);
  assert.equal(turns.length, 1);
  assert.deepEqual(
    turns[0].runs.map((item) => item.agentRunId),
    ["run-live-screenplay-writer", "run-live-screenplay"],
  );
});

test("durable child activity is accounted under the orchestration root", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-workflow", {
    apiKey: "key",
    messages: [{ role: "user", content: "连续创作" }],
    options: { model: "model" },
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
        reasoningDelta: "检查连续性",
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
  assert.equal(run.childRuns[0].reasoning, "检查连续性");
});
