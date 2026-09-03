import assert from "node:assert/strict";
import test from "node:test";
import type { AiAgentRunSnapshot } from "../../types.ts";
import { mergeToolDiagnostics } from './toolDiagnostics.ts';

test('tool diagnostics merge paged IO without conflating calls or Runs', () => {
  const args = { text: '{"chapter":1}', characters: 13, truncated: false };
  const result = { text: '', characters: 0, truncated: false };
  const calls = mergeToolDiagnostics([
    { runId: 'a', toolCallId: 'same', eventRowId: 1, arguments: args },
    { runId: 'b', toolCallId: 'same', eventRowId: 2, arguments: args },
  ], [
    { runId: 'a', toolCallId: 'same', eventRowId: 3, result, status: 'failed' },
  ]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].arguments, args);
  assert.equal(calls[0].result, result);
  assert.equal(calls[0].status, 'failed');
  assert.equal(calls[0].eventRowId, 1);
  assert.equal(calls[1].result, undefined);
  const replayed = mergeToolDiagnostics(calls, [
    { runId: 'a', toolCallId: 'same', eventRowId: 1, arguments: args },
  ]);
  assert.deepEqual(replayed, calls);
});
import type { CanonicalOutputEvent } from "../../agent-runtime/canonicalOutput.ts";
import {
  aiDebugConversationLifecycle,
  aiDebugCurrentRunId,
  aiDebugTurnTokenUsage,
  aiDebugTurnDiagnosticId,
  aiDebugTurnRootRunId,
  clearAiDebugRuns,
  getAiDebugSnapshot,
  groupAiDebugRunsByTurn,
  hydrateAiDebugRunSnapshot,
  recordAiDebugChunk,
  recordAiDebugRunEvent,
  recordAiDebugRunUsageSnapshot,
  recordAgentConversationDebugChunk,
  startAiDebugRun,
  type AiDebugRun,
} from "./store.ts";

function canonicalEvent(
  sequence: number,
  overrides: Partial<CanonicalOutputEvent> = {},
): CanonicalOutputEvent {
  return {
    eventId: `event-${sequence}`,
    outputStreamId: null,
    runId: "run-root",
    turnId: "turn-1",
    invocationId: null,
    sequence,
    source: "runtime",
    kind: "runtime.event",
    channel: "lifecycle",
    visibility: "public",
    payload: {},
    occurredAt: `2026-08-12T08:00:0${sequence}Z`,
    emittedAt: `2026-08-12T08:00:0${sequence}Z`,
    ...overrides,
  };
}

function modelOperation(
  sequence: number,
  runId: string,
  operationId: string,
  labelParams: Record<string, unknown> = {},
): CanonicalOutputEvent {
  return canonicalEvent(sequence, {
    runId,
    kind: "operation.started",
    channel: "operation",
    invocationId: `${operationId}-invocation`,
    payload: {
      operationId,
      kind: "model",
      startedAt: `2026-08-12T08:00:0${sequence}Z`,
      display: { labelParams },
    },
  });
}

function providerDelta(
  sequence: number,
  runId: string,
  channel: "commentary" | "final",
  delta: string,
): CanonicalOutputEvent {
  return canonicalEvent(sequence, {
    runId,
    source: "provider",
    kind: "provider.content_delta",
    channel,
    outputStreamId: `${runId}-${channel}`,
    invocationId: `${runId}-${channel}-invocation`,
    payload: { delta },
  });
}

function providerDeltaBatch(
  sequence: number,
  runId: string,
  channel: "commentary" | "final",
  ...deltas: string[]
): CanonicalOutputEvent {
  return canonicalEvent(sequence, {
    runId,
    source: "provider",
    kind: "provider.delta_batch",
    channel,
    outputStreamId: `${runId}-${channel}`,
    invocationId: `${runId}-${channel}-invocation`,
    payload: {
      schemaVersion: "purra.provider-delta-batch/v1",
      entries: deltas.map((delta, index) => ({
        sourceChunkIndex: index + 1,
        sourcePartIndex: 0,
        kind: "provider.content_delta",
        payload: { delta },
      })),
    },
  });
}

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

test('a grouped turn takes its label and prompt from the Root Run', () => {
  clearAiDebugRuns();
  startAiDebugRun('root-stream', {
    apiKey: 'key',
    sessionId: 7,
    messages: [{ role: 'user', content: '分析整部小说' }],
    options: { model: 'model' },
  }, {
    turnId: 'turn-root-label',
    conversationRootRunId: 'run-root-label',
    source: '小说来源分析',
  });
  recordAiDebugChunk('root-stream', canonicalEvent(1, {
    runId: 'run-root-label',
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }));
  startAiDebugRun('child-stream', {
    apiKey: 'key',
    sessionId: 7,
    messages: [{ role: 'user', content: '提取人物关系' }],
    options: { model: 'model' },
  }, {
    turnId: 'turn-root-label',
    conversationRootRunId: 'run-root-label',
    source: '事实分析 Agent',
  });

  const turn = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs)[0];
  assert.equal(turn.source, '小说来源分析');
  assert.equal(turn.prompt, '分析整部小说');
});

test('the latest active Run is the current expanded execution segment', () => {
  const run = (
    id: string,
    status: AiDebugRun["status"],
    startedAt: number,
  ) => ({ id, status, startedAt, updatedAt: startedAt }) as AiDebugRun;
  assert.equal(aiDebugCurrentRunId([
    run("root", "thinking", 1),
    run("past", "completed", 2),
    run("current", "tool", 3),
  ]), "current");
  assert.equal(aiDebugCurrentRunId([
    run("done", "completed", 1),
    run("failed", "failed", 2),
  ]), undefined);
});

test('conversation lifecycle follows the root Run instead of a child model finish', () => {
  clearAiDebugRuns();
  const input = {
    turnId: 'turn-root-authority',
    conversationRootRunId: 'run-root-authority',
    sessionId: 7,
    prompt: '继续创作三集',
    model: 'model',
  };
  recordAgentConversationDebugChunk({
    ...input,
    runId: 'run-root-authority',
    chunk: canonicalEvent(1, {
      runId: 'run-root-authority',
      kind: 'run.lifecycle',
      payload: { status: 'running' },
    }),
  });
  recordAgentConversationDebugChunk({
    ...input,
    runId: 'run-child-model',
    chunk: canonicalEvent(2, {
      runId: 'run-child-model',
      kind: 'runtime.event',
      payload: {
        eventType: 'context.usage_recorded',
        data: { finishReason: 'tool_calls' },
      },
    }),
  });
  recordAgentConversationDebugChunk({
    ...input,
    runId: 'run-child-model',
    chunk: canonicalEvent(3, {
      runId: 'run-child-model',
      kind: 'run.lifecycle',
      payload: { status: 'failed', errorCode: 'child_model_failed' },
    }),
  });

  let turn = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs)[0];
  let lifecycle = aiDebugConversationLifecycle(turn);
  assert.equal(lifecycle.ended, false);
  assert.equal(lifecycle.endReason, '尚未结束');
  assert.equal(lifecycle.authoritativeRunId, 'run-root-authority');

  recordAgentConversationDebugChunk({
    ...input,
    runId: 'run-root-authority',
    chunk: canonicalEvent(4, {
      runId: 'run-root-authority',
      kind: 'run.lifecycle',
      payload: { status: 'done', finalResponse: '任务完成' },
    }),
  });
  turn = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs)[0];
  lifecycle = aiDebugConversationLifecycle(turn);
  assert.equal(lifecycle.status, 'completed');
  assert.equal(lifecycle.ended, true);
  assert.equal(lifecycle.endReason, '正常完成');
});

test('diagnostic footer identity stays on the Root Run across child selection', () => {
  const root = {
    id: 'agent-run-root',
    agentRunId: 'run-root',
    conversationRootRunId: 'run-root',
    turnId: 'turn-root',
  } as AiDebugRun;
  const child = {
    id: 'agent-run-child',
    agentRunId: 'run-child',
    conversationRootRunId: 'run-root',
    turnId: 'turn-root',
  } as AiDebugRun;
  const base = {
    key: 'session:7:turn:turn-root',
    startedAt: 1,
    updatedAt: 2,
    source: '小说来源分析',
    prompt: '分析小说',
  };

  assert.equal(aiDebugTurnDiagnosticId({ ...base, runs: [root, child] }), 'run-root');
  assert.equal(aiDebugTurnDiagnosticId({ ...base, runs: [child, root] }), 'run-root');
  assert.equal(aiDebugTurnDiagnosticId({ ...base, runs: [child] }), 'run-root');
  assert.equal(aiDebugTurnRootRunId({ ...base, runs: [root, child] }), 'run-root');
  assert.equal(aiDebugTurnRootRunId({ ...base, runs: [child] }), 'run-root');
});

test('multi-Run legacy turns fall back to their shared Turn ID', () => {
  const run = (id: string) => ({ id, turnId: 'legacy-turn' }) as AiDebugRun;
  const turn = {
    key: 'session:7:turn:legacy-turn',
    runs: [run('agent-child-one'), run('agent-child-two')],
    startedAt: 1,
    updatedAt: 2,
    source: 'Agent 历史恢复',
    prompt: '',
  };
  assert.equal(aiDebugTurnDiagnosticId(turn), 'legacy-turn');
  assert.equal(aiDebugTurnRootRunId(turn), undefined);
});

test('conversation lifecycle preserves the root terminal error code', () => {
  clearAiDebugRuns();
  recordAgentConversationDebugChunk({
    runId: 'run-root-failed',
    turnId: 'turn-root-failed',
    conversationRootRunId: 'run-root-failed',
    sessionId: 7,
    prompt: '继续创作三集',
    model: 'model',
    chunk: canonicalEvent(1, {
      runId: 'run-root-failed',
      kind: 'run.lifecycle',
      payload: {
        status: 'failed',
        errorCode: 'model_invocation_deadline_exceeded',
      },
    }),
  });

  const run = getAiDebugSnapshot().runs[0];
  const lifecycle = aiDebugConversationLifecycle(
    groupAiDebugRunsByTurn([run])[0],
  );
  assert.equal(run.status, 'failed');
  assert.equal(run.error, 'model_invocation_deadline_exceeded');
  assert.equal(lifecycle.ended, true);
  assert.equal(lifecycle.endReason, 'model_invocation_deadline_exceeded');
});

test('conversation stays open until its declared root Run is observed', () => {
  clearAiDebugRuns();
  recordAgentConversationDebugChunk({
    runId: 'run-child-first',
    turnId: 'turn-child-first',
    conversationRootRunId: 'run-root-later',
    sessionId: 7,
    prompt: '继续创作三集',
    model: 'model',
    chunk: canonicalEvent(1, {
      runId: 'run-child-first',
      kind: 'run.lifecycle',
      payload: { status: 'failed', errorCode: 'child_failed' },
    }),
  });

  const lifecycle = aiDebugConversationLifecycle(
    groupAiDebugRunsByTurn(getAiDebugSnapshot().runs)[0],
  );
  assert.equal(lifecycle.ended, false);
  assert.equal(lifecycle.endReason, '尚未结束');
  assert.equal(lifecycle.authoritativeRunId, 'run-root-later');
});

test('transport runResult controls the diagnostic terminal status', () => {
  clearAiDebugRuns();
  startAiDebugRun('transport-terminal', {
    apiKey: 'key',
    messages: [{ role: 'user', content: '继续创作三集' }],
    options: { model: 'model' },
  });
  recordAiDebugChunk('transport-terminal', {
    done: true,
    runResult: {
      runId: 'run-transport-terminal',
      status: 'failed',
      errorCode: 'provider_unavailable',
    },
  });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.status, 'failed');
  assert.equal(run.error, 'provider_unavailable');
});

test('diagnostics treat Provider delta batches as visible completed output', () => {
  clearAiDebugRuns();
  startAiDebugRun('batched-output', {
    apiKey: 'key',
    messages: [{ role: 'user', content: '继续' }],
    options: { model: 'model' },
  });
  recordAiDebugChunk(
    'batched-output',
    providerDeltaBatch(1, 'run-batched-output', 'final', '批量', '回答'),
  );
  recordAiDebugChunk('batched-output', {
    done: true,
    runResult: {
      runId: 'run-batched-output',
      status: 'done',
      errorCode: null,
    },
    errorReport: {
      id: 'false-empty-report',
      streamId: 'batched-output',
      source: 'workspace_chat',
      status: 'captured',
      errorCode: 'empty_model_response',
      errorMessage: '模型未返回可见内容。',
      diagnostics: {},
      createTime: '2026-08-27T07:34:58Z',
      updateTime: '2026-08-27T07:34:58Z',
    },
  });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.status, 'completed');
  assert.equal(run.output, '批量回答');
  assert.equal(run.error, undefined);
  assert.equal(run.errorReport, undefined);
  assert.equal(run.events.at(-1)?.type, 'done');
  assert.equal(run.events.some((event) => event.type === 'response'), true);
});

function persistedSnapshot(
  events: AiAgentRunSnapshot['events'],
  options: {
    runId?: string;
    status?: AiAgentRunSnapshot['run']['status'];
    nextCursor?: number;
    hasMore?: boolean;
    todos?: AiAgentRunSnapshot['todos'];
    providerOutputEvents?: number;
    modelAttemptCount?: number;
    usage?: Partial<NonNullable<NonNullable<AiAgentRunSnapshot['run']['activity']>['usage']>>;
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
      finalResponse: '',
      createdAt: '2026-08-05 17:00:00',
      updatedAt: '2026-08-05 17:00:10',
      execution: {
        attempt: 1,
        cancellationRequested: false,
      },
      activity: {
        modelAttemptCount: options.modelAttemptCount ?? 0,
        usage: {
          inputTokens: options.usage?.inputTokens ?? 0,
          outputTokens: options.usage?.outputTokens ?? 0,
          reasoningTokens: options.usage?.reasoningTokens ?? 0,
          totalTokens: options.usage?.totalTokens ?? 0,
          unreportedAttempts: options.usage?.unreportedAttempts ?? 0,
        },
        providerOutputEvents: options.providerOutputEvents ?? 0,
        providerOutputBytes: 0,
      },
      provenance: {
        modelProvider: 'openai',
        modelName: 'mimo-v2.5-pro',
      },
    },
    todos: options.todos ?? [],
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

test('diagnostics expose live reported usage and replace it with durable Run totals', () => {
  clearAiDebugRuns();
  startAiDebugRun('usage-stream', {
    apiKey: 'key',
    messages: [{ role: 'user', content: '分析小说' }],
    options: { model: 'model' },
    enableAgentTools: true,
  });
  recordAiDebugChunk('usage-stream', modelOperation(1, 'run-usage', 'model-1'));
  recordAiDebugChunk('usage-stream', canonicalEvent(2, {
    runId: 'run-usage',
    payload: {
      eventType: 'context.usage_recorded',
      data: {
        actualInputTokens: 1200,
        actualOutputTokens: 80,
        actualTotalTokens: 1280,
        reasoningOutputTokens: 25,
      },
    },
  }));

  let run = getAiDebugSnapshot().runs[0];
  assert.deepEqual(run.tokenUsage, {
    inputTokens: 1200,
    outputTokens: 80,
    reasoningTokens: 25,
    totalTokens: 1280,
    unreportedAttempts: 0,
    modelAttempts: 1,
    complete: false,
  });

  recordAiDebugChunk('usage-stream', canonicalEvent(3, {
    runId: 'run-usage',
    kind: 'run.lifecycle',
    payload: { status: 'done' },
  }));

  recordAiDebugRunUsageSnapshot(persistedSnapshot([], {
    runId: 'run-usage',
    status: 'done',
    modelAttemptCount: 3,
    usage: {
      inputTokens: 3600,
      outputTokens: 240,
      reasoningTokens: 75,
      totalTokens: 3840,
      unreportedAttempts: 1,
    },
  }));

  run = getAiDebugSnapshot().runs[0];
  assert.deepEqual(run.tokenUsage, {
    inputTokens: 3600,
    outputTokens: 240,
    reasoningTokens: 75,
    totalTokens: 3840,
    unreportedAttempts: 1,
    modelAttempts: 3,
    complete: true,
  });
  assert.deepEqual(aiDebugTurnTokenUsage([run]), run.tokenUsage);
});

test('persisted recovery merges todo updates without discarding the full plan', () => {
  clearAiDebugRuns();
  const steps = [{
    id: 'overview', title: '梳理故事概览', status: 'pending',
    type: 'analyze', executor: 'model', dependsOn: [],
  }, {
    id: 'facts', title: '梳理事实脉络', status: 'pending',
    type: 'analyze', executor: 'model', dependsOn: [],
  }] as AiAgentRunSnapshot['todos'];
  const events: AiAgentRunSnapshot['events'] = [{
    version: 1, cursor: 1, type: 'run.todos_updated', runId: 'run-plan', payload: {},
    chunk: canonicalEvent(1, {
      runId: 'run-plan',
      payload: { eventType: 'run.todos_updated', data: { title: '小说分析', status: 'running', steps } },
    }),
  }, {
    version: 1, cursor: 2, type: 'run.todo_updated', runId: 'run-plan', payload: {},
    chunk: canonicalEvent(2, {
      runId: 'run-plan',
      payload: {
        eventType: 'run.todo_updated',
        data: { step_id: 'overview', step: { ...steps[0], status: 'failed' } },
      },
    }),
  }];

  hydrateAiDebugRunSnapshot({
    snapshot: persistedSnapshot(events, {
      runId: 'run-plan', status: 'failed', todos: steps, providerOutputEvents: 12,
    }),
    prompt: '分析小说',
  });

  const run = getAiDebugSnapshot().runs[0];
  const plan = run.agentPlan as { status: string; steps: Array<{ id: string; status: string }> };
  assert.equal(plan.status, 'failed');
  assert.deepEqual(plan.steps.map((step) => [step.id, step.status]), [
    ['overview', 'failed'],
    ['facts', 'pending'],
  ]);
  assert.equal(run.providerOutputEvents, 12);
});

test('persisted recovery restores Planner calls without duplicating cursors', () => {
  clearAiDebugRuns();
  const initial = persistedSnapshot([
    {
      version: 1,
      cursor: 1,
      type: 'run.started',
      runId: 'run-recovered',
      payload: {},
      chunk: canonicalEvent(1, {
        runId: 'run-recovered',
        kind: 'run.lifecycle',
        payload: { status: 'running' },
      }),
    },
    {
      version: 1,
      cursor: 2,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: modelOperation(2, 'run-recovered', 'model-1', {
        phase: 'planning',
        messageCount: 2,
      }),
    },
    {
      version: 1,
      cursor: 3,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: modelOperation(3, 'run-recovered', 'model-2', {
        phase: 'planning',
        messageCount: 4,
      }),
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
  assert.equal(run.id, 'persisted-run-recovered');
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
  assert.equal(run.finishedAt, undefined);
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
    ...canonicalEvent(1, {
      runId: 'run-recovered',
      kind: 'run.lifecycle',
      payload: { status: 'running' },
    }),
  });
  recordAiDebugChunk(
    'live-before-detach',
    modelOperation(2, 'run-recovered', 'live-model', { phase: 'generation' }),
  );

  hydrateAiDebugRunSnapshot({
    snapshot: persistedSnapshot([{
      version: 1,
      cursor: 1,
      type: 'model.call_recorded',
      runId: 'run-recovered',
      payload: {},
      chunk: modelOperation(1, 'run-recovered', 'persisted-model', {
        phase: 'planning',
      }),
    }]),
    prompt: '继续创作三集',
    source: '剧本 Agent',
  });

  const runs = getAiDebugSnapshot().runs;
  assert.equal(runs.length, 1);
  assert.equal(runs[0].id, 'persisted-run-recovered');
  assert.deepEqual(runs[0].modelCalls.map((call) => call.phase), ['model']);
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
  recordAgentConversationDebugChunk({
    ...input,
    chunk: providerDelta(1, input.runId, 'commentary', '实时片段'),
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
  assert.equal(completed.id, 'persisted-run-terminal-race');
  assert.equal(completed.status, 'completed');
  assert.equal(completed.commentary, '实时片段');
  assert.ok(finishedAt);

  recordAgentConversationDebugChunk({
    ...input,
    chunk: providerDelta(2, input.runId, 'final', '晚到的历史片段'),
  });
  const runs = getAiDebugSnapshot().runs;
  assert.equal(runs.length, 1);
  assert.equal(runs[0].status, 'completed');
  assert.equal(runs[0].finishedAt, finishedAt);
  assert.equal(runs[0].output, '晚到的历史片段');
});

test("debug store preserves a canonical tool failure code", () => {
    clearAiDebugRuns();
    startAiDebugRun("screenplay-test", {
      apiKey: "key",
      messages: [{ role: "user", content: "分析限定范围" }],
      options: { model: "model" },
      enableAgentTools: true,
    });
    recordAiDebugChunk("screenplay-test", canonicalEvent(1, {
      kind: "operation.started",
      channel: "operation",
      payload: {
        operationId: "operation-scope",
        kind: "tool",
        startedAt: "2026-08-12T08:00:01Z",
        display: { labelParams: { toolName: "getSourceCharacters" } },
      },
    }));
    recordAiDebugChunk("screenplay-test", canonicalEvent(2, {
      source: "tool",
      kind: "tool.event",
      channel: "operation",
      payload: {
        operationId: "operation-scope",
        toolCallId: "call-scope",
        toolName: "getSourceCharacters",
        status: "failed",
      },
    }));
    recordAiDebugChunk("screenplay-test", canonicalEvent(3, {
      kind: "operation.finished",
      channel: "operation",
      payload: {
        operationId: "operation-scope",
        status: "failed",
        finishedAt: "2026-08-12T08:00:03Z",
        durationMs: 2000,
        errorCode: "tool_scope_violation",
        display: {},
      },
    }));

    const tool = getAiDebugSnapshot().runs[0].tools[0];
    assert.equal(tool.status, "failed");
    assert.equal(tool.outcome, "failed");
    assert.equal(tool.errorCode, "tool_scope_violation");
    assert.equal(tool.errorMessage, undefined);
});

test("debug store does not treat a successful tool message as an error", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-success", {
    apiKey: "key",
    messages: [{ role: "user", content: "提交正式提案" }],
    options: { model: "model" },
    enableAgentTools: true,
  });
  recordAiDebugChunk("screenplay-success", canonicalEvent(1, {
    kind: "operation.started",
    channel: "operation",
    payload: {
      operationId: "operation-proposal",
      kind: "tool",
      startedAt: "2026-08-12T08:00:01Z",
      display: { labelParams: { toolName: "proposeSourceAnalysis" } },
    },
  }));
  recordAiDebugChunk("screenplay-success", canonicalEvent(2, {
    kind: "operation.finished",
    channel: "operation",
    payload: {
      operationId: "operation-proposal",
      status: "succeeded",
      finishedAt: "2026-08-12T08:00:02Z",
      durationMs: 1000,
      errorCode: null,
      display: {},
    },
  }));

  const tool = getAiDebugSnapshot().runs[0].tools[0];
  assert.equal(tool.status, "completed");
  assert.equal(tool.errorCode, undefined);
  assert.equal(tool.errorMessage, undefined);
  assert.equal(tool.argumentsValue, undefined);
  assert.equal(tool.result, undefined);
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
  assert.equal(getAiDebugSnapshot().runs[0].finishedAt, undefined);
});

test("screenplay persisted SSE creates a live diagnostic Run", () => {
  clearAiDebugRuns();
  recordAgentConversationDebugChunk({
    source: '剧本 Agent',
    runId: "run-live-screenplay",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: canonicalEvent(1, {
      runId: "run-live-screenplay",
      kind: "run.lifecycle",
      payload: { status: "running" },
    }),
  });
  recordAgentConversationDebugChunk({
    source: '剧本 Agent',
    runId: "run-live-screenplay",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: providerDelta(
      2,
      "run-live-screenplay",
      "commentary",
      "先检查场景连续性",
    ),
  });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.source, "剧本 Agent");
  assert.equal(run.taskType, "剧本 Agent任务");
  assert.equal(run.agentRunId, "run-live-screenplay");
  assert.equal(run.turnId, "turn-live-screenplay");
  assert.equal(run.commentary, "先检查场景连续性");

  recordAgentConversationDebugChunk({
    runId: "run-live-screenplay-writer",
    turnId: "turn-live-screenplay",
    sessionId: 9,
    prompt: "继续创作下一集",
    model: "model",
    chunk: providerDelta(
      1,
      "run-live-screenplay-writer",
      "commentary",
      "开始创作",
    ),
  });
  const turns = groupAiDebugRunsByTurn(getAiDebugSnapshot().runs);
  assert.equal(turns.length, 1);
  assert.deepEqual(
    turns[0].runs.map((item) => item.agentRunId),
    ["run-live-screenplay-writer", "run-live-screenplay"],
  );
});

test("same-Run delegated model activity stays under its owning Run", () => {
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

  recordAiDebugRunEvent("run-root", canonicalEvent(1, {
    runId: "run-root",
    kind: "run.lifecycle",
    payload: { status: "running" },
  }));
  recordAiDebugRunEvent(
    "run-root",
    modelOperation(2, "run-root", "delegated-model", {
      phase: "generation",
      round: 1,
    }),
  );
  recordAiDebugRunEvent("run-root", { done: true });

  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.agentRunId, "run-root");
  assert.equal(run.modelCalls.length, 1);
  assert.equal(run.status, "completed");
  assert.ok((run.finishedAt || 0) >= run.startedAt);
});

test("delegations keep status metadata inside the owning Run", () => {
  clearAiDebugRuns();
  startAiDebugRun("screenplay-multi-agent", {
    apiKey: "key",
    messages: [{ role: "user", content: "并行创作" }],
    options: { model: "root-model" },
    enableAgentTools: true,
  });
  recordAiDebugChunk("screenplay-multi-agent", canonicalEvent(1, {
    runId: "run-root",
    kind: "run.lifecycle",
    payload: { status: "running" },
  }));
  recordAiDebugChunk("screenplay-multi-agent", canonicalEvent(2, {
    runId: "run-root",
    kind: "delegation.event",
    channel: "delegation",
    payload: {
      eventType: "status",
      delegationId: "delegation-writer-a",
      runId: "run-root",
      agentName: "screenplay_writer",
      agentTitle: "剧本 Writer · ep05",
      objective: "创作第五集",
      unitId: "ep05",
      attempt: 2,
      status: "running",
      required: true,
      priority: 0,
    },
  }));
  const run = getAiDebugSnapshot().runs[0];
  assert.equal(run.agentRunId, "run-root");
  assert.equal(run.modelCalls.length, 0);
  assert.equal(run.delegationActivities.length, 1);
  assert.equal(run.delegationActivities[0].unitId, "ep05");
  assert.equal(run.delegationActivities[0].attempt, 2);
  assert.equal(run.delegationActivities[0].status, "thinking");
});
