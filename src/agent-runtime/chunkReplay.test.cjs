'use strict'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')

const { AgentChunkReplay } = loadTypeScriptModule(
  path.join(__dirname, 'chunkReplay.ts'),
)
const {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
} = loadTypeScriptModule(
  path.join(__dirname, 'taskPlan.ts'),
)

const model = {
  id: 'model-1',
  name: 'test-model',
  supportsThinking: true,
  thinkingOnly: false,
  apiKey: '',
  baseUrl: '',
}

const appMessage = Object.fromEntries(
  ['success', 'error', 'warning', 'info', 'loading', 'open', 'destroy']
    .map((name) => [name, () => undefined]),
)

const canonical = (runId, sequence, overrides = {}) => ({
  eventId: `${runId}-event-${sequence}`,
  outputStreamId: null,
  runId,
  turnId: null,
  invocationId: null,
  sequence,
  source: 'runtime',
  kind: 'runtime.event',
  channel: 'lifecycle',
  visibility: 'public',
  payload: {},
  occurredAt: `2026-08-12T08:00:${String(sequence).padStart(2, '0')}+00:00`,
  emittedAt: `2026-08-12T08:00:${String(sequence).padStart(2, '0')}+00:00`,
  ...overrides,
})

test('business Agents replay the canonical chunk protocol through the shared reducer', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-1',
    sessionId: 7,
    userContent: '创作下一集',
    model: model.name,
    turnStartedAt: performance.now() - 250,
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, canonical('run-1', 1, {
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-1', 2, {
    payload: {
      eventType: 'run.todos_updated',
      data: {
        runId: 'run-1',
        title: '剧本创作任务',
        status: 'running',
        steps: [{
          id: 'plan',
          title: '理解请求',
          type: 'analyze',
          status: 'running',
        }],
      },
    },
  }), dependencies)
  replay.dispatch(seed, canonical('run-1', 3, {
    outputStreamId: 'commentary-stream',
    invocationId: 'commentary-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'commentary',
    payload: { delta: '先检查连续性' },
  }), dependencies)
  replay.dispatch(seed, {
    longTaskProgress: {
      runId: 'turn-1',
      taskId: 'task-1',
      status: 'running',
      revision: 2,
      totalUnits: 2,
      completedUnits: 0,
      failedUnits: 0,
      units: [{
        id: 'generate',
        position: 0,
        title: '生成审阅报告',
        status: 'running',
        attempt: 1,
        maxAttempts: 2,
      }, {
        id: 'publish',
        position: 1,
        title: '整理并发布候选稿',
        status: 'pending',
        attempt: 0,
        maxAttempts: 1,
      }],
    },
  }, dependencies)
  replay.dispatch(seed, canonical('run-1', 4, {
    outputStreamId: 'final-stream',
    invocationId: 'final-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '第一场正文。' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-1', 5, {
    outputStreamId: 'final-stream',
    invocationId: 'final-invocation',
    kind: 'stream.committed',
    channel: 'final',
    payload: { finishReason: 'stop' },
  }), dependencies)
  replay.dispatch(seed, { done: true }, dependencies)

  const assistant = replay.assistant('turn-1')
  assert.equal(assistant?.content, '第一场正文。')
  assert.deepEqual(assistant?.commentaryBlocks, ['先检查连续性'])
  assert.equal(assistant?.taskPlan?.title, '剧本创作任务')
  assert.equal(assistant?.longTaskId, 'task-1')
  assert.equal(assistant?.durationMs != null && assistant.durationMs >= 200, true)
  assert.equal(assistant?.turnStartedAt, undefined)
})

test('canonical long-task progress restores the execution status plan without todo events', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-long-task-only',
    sessionId: 534,
    userContent: '分析原作',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, canonical('run-long-task-only', 1, {
    payload: {
      eventType: 'long_task.progress',
      data: {
        taskId: 'longtask-1',
        status: 'running',
        revision: 6,
        totalUnits: 4,
        completedUnits: 1,
        failedUnits: 0,
        units: [{
          id: 'document:evidence',
          position: 0,
          kind: 'collect_evidence',
          status: 'completed',
          attempt: 1,
          maxAttempts: 2,
        }, {
          id: 'section:characters',
          position: 1,
          kind: 'generate_document_section',
          status: 'claimed',
          attempt: 1,
          maxAttempts: 4,
        }, {
          id: 'section:story',
          position: 2,
          kind: 'generate_document_section',
          status: 'claimed',
          attempt: 1,
          maxAttempts: 4,
        }, {
          id: 'compose-final-response',
          position: 3,
          kind: 'compose_final_response',
          status: 'pending',
          attempt: 0,
          maxAttempts: 2,
        }],
      },
    },
  }), dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.longTaskId, 'longtask-1')
  assert.equal(assistant?.taskPlan?.status, 'running')
  assert.deepEqual(
    assistant?.taskPlan?.steps.map((step) => [step.id, step.type, step.status]),
    [
      ['document:evidence', 'read', 'done'],
      ['section:characters', 'write', 'running'],
      ['section:story', 'write', 'running'],
      ['compose-final-response', 'write', 'pending'],
    ],
  )
  assert.equal(getActiveTaskPlan([assistant], true), assistant.taskPlan)
  assert.equal(
    getTaskPlanCountLabel(assistant.taskPlan),
    '并行 2 项 · 已完成 1/4',
  )
  assert.equal(getActiveTaskPlan([assistant], false), assistant.taskPlan)
})

test('raw Provider events are visible before transport completion', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-canonical-live',
    sessionId: 17,
    userContent: '审阅完整剧本',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }
  const canonical = (sequence, overrides) => ({
    eventId: `event-${sequence}`,
    outputStreamId: null,
    runId: 'run-public',
    turnId: seed.turnId,
    invocationId: null,
    sequence,
    source: 'runtime',
    kind: 'runtime.event',
    channel: 'lifecycle',
    visibility: 'public',
    payload: {},
    occurredAt: `2026-08-12T08:00:0${sequence}+00:00`,
    emittedAt: `2026-08-12T08:00:0${sequence}+00:00`,
    ...overrides,
  })

  replay.dispatch(seed, canonical(4, {
    outputStreamId: 'final-stream',
    invocationId: 'invocation-final',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '已完成前四集' },
  }), dependencies)

  const live = replay.assistant(seed.turnId)
  assert.equal(live?.content, '')
  assert.equal(live?.streamingContent, '已完成前四集')
  assert.equal(live?.canonicalOutput?.finalText, '已完成前四集')

  replay.dispatch(seed, canonical(5, {
    outputStreamId: 'final-stream',
    invocationId: 'invocation-final',
    kind: 'stream.committed',
    channel: 'final',
    payload: { finishReason: 'stop' },
  }), dependencies)

  const committed = replay.assistant(seed.turnId)
  assert.equal(committed?.content, '已完成前四集')
  assert.equal(committed?.streamingContent, undefined)
})

test('Provider execution commentary is preserved before its operation', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-progress-tool',
    sessionId: 9,
    userContent: '生成场景表',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, canonical('run-progress-tool', 1, {
    outputStreamId: 'commentary-stream',
    invocationId: 'commentary-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'commentary',
    payload: { delta: '核对本集目标、冲突和转折。\n' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-progress-tool', 2, {
    kind: 'operation.started',
    channel: 'operation',
    payload: {
      operationId: 'operation-write',
      kind: 'tool',
      startedAt: '2026-08-12T08:00:02+00:00',
      display: {
        labelKey: 'agent.operation.tool',
        labelParams: { toolName: 'writeScreenplayCandidatePart' },
      },
    },
  }), dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.deepEqual(
    assistant?.commentaryBlocks,
    ['核对本集目标、冲突和转折。'],
  )
  assert.deepEqual(assistant?.canonicalOutput?.operationOrder, ['operation-write'])
})

test('reset removes replay state when the project or session changes', () => {
  const replay = new AgentChunkReplay()
  replay.dispatch({
    turnId: 'turn-1',
    sessionId: 7,
    userContent: '测试',
    turnStartedAt: performance.now(),
  }, canonical('run-reset', 1, {
    outputStreamId: 'reset-stream',
    invocationId: 'reset-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '结果' },
  }), { cfg: model, appMessage })

  replay.reset()

  assert.equal(replay.assistant('turn-1'), undefined)
})

test('replay projects a completed turn only once across duplicate terminal chunks', () => {
  const replay = new AgentChunkReplay()
  const dependencies = { cfg: model, appMessage }
  const seed = {
    turnId: 'turn-duplicate-terminal',
    sessionId: 7,
    userContent: '测试重复终态',
    turnStartedAt: performance.now(),
  }

  replay.dispatch(seed, canonical('run-duplicate-terminal', 1, {
    outputStreamId: 'duplicate-final',
    invocationId: 'duplicate-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '唯一终稿。' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-duplicate-terminal', 2, {
    outputStreamId: 'duplicate-final',
    invocationId: 'duplicate-invocation',
    kind: 'stream.committed',
    channel: 'final',
    payload: { finishReason: 'stop' },
  }), dependencies)
  replay.dispatch(seed, { done: true, model: 'first-terminal-model' }, dependencies)
  const firstTerminalMessage = replay.assistant(seed.turnId)

  replay.dispatch(seed, { done: true, model: 'late-terminal-model' }, dependencies)

  assert.equal(replay.assistant(seed.turnId)?.model, 'first-terminal-model')
  assert.deepEqual(replay.assistant(seed.turnId), firstTerminalMessage)
})

test('paused durable task emits no formal answer and resume commits once', () => {
  const replay = new AgentChunkReplay()
  const dependencies = { cfg: model, appMessage }
  const seed = {
    turnId: 'turn-paused',
    sessionId: 7,
    userContent: '继续完成剧本',
    turnStartedAt: performance.now(),
  }

  replay.dispatch(seed, canonical('run-paused', 1, {
    payload: {
      eventType: 'run.todos_updated',
      data: {
        runId: 'run-paused',
        title: '剧本创作任务',
        status: 'paused',
        steps: [{
          id: 'generate',
          title: '生成候选稿',
          type: 'write',
          status: 'blocked',
        }],
      },
    },
  }), dependencies)
  replay.dispatch(seed, {
    done: true,
    finalResponseExpected: false,
    model: 'paused-model',
  }, dependencies)
  replay.dispatch(seed, {
    done: true,
    finalResponseExpected: false,
    model: 'late-paused-model',
  }, dependencies)

  const paused = replay.assistant('turn-paused')
  assert.equal(paused?.content, '')
  assert.equal(paused?.taskPlan?.status, 'paused')
  assert.equal(paused?.model, 'paused-model')

  replay.dispatch(seed, canonical('run-resumed', 1, {
    payload: {
      eventType: 'run.todos_updated',
      data: {
        runId: 'run-resumed',
        title: '剧本创作任务',
        status: 'running',
        steps: [{
          id: 'generate',
          title: '生成候选稿',
          type: 'write',
          status: 'running',
        }],
      },
    },
  }), dependencies)
  replay.dispatch(seed, canonical('run-resumed', 2, {
    outputStreamId: 'resumed-final',
    invocationId: 'resumed-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '候选稿已发布。' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-resumed', 3, {
    outputStreamId: 'resumed-final',
    invocationId: 'resumed-invocation',
    kind: 'stream.committed',
    channel: 'final',
    payload: { finishReason: 'stop' },
  }), dependencies)
  replay.dispatch(seed, { done: true, model: 'resumed-model' }, dependencies)

  assert.equal(replay.assistant('turn-paused')?.content, '候选稿已发布。')
  assert.equal(replay.assistant('turn-paused')?.model, 'resumed-model')
})

test('paused resume switches the canonical root once and blocks late same-run terminal chunks', () => {
  const replay = new AgentChunkReplay()
  const dependencies = { cfg: model, appMessage }
  const seed = {
    turnId: 'turn-paused-twice',
    sessionId: 8,
    userContent: '暂停后继续，再次暂停',
    turnStartedAt: performance.now(),
  }

  replay.dispatch(seed, canonical('run-a', 1, {
    payload: {
      eventType: 'run.todos_updated',
      data: { runId: 'run-a', title: 'A', status: 'paused', steps: [] },
    },
  }), dependencies)
  replay.dispatch(seed, { done: true, finalResponseExpected: false }, dependencies)

  replay.dispatch(seed, canonical('run-b', 1, {
    payload: {
      eventType: 'run.todos_updated',
      data: { runId: 'run-b', title: 'B', status: 'running', steps: [] },
    },
  }), dependencies)
  assert.equal(replay.assistant(seed.turnId)?.agentRunId, 'run-b')
  assert.equal(replay.assistant(seed.turnId)?.canonicalOutput?.runId, 'run-b')

  replay.dispatch(seed, {
    done: true,
    finalResponseExpected: false,
    model: 'run-b-paused-model',
  }, dependencies)
  replay.dispatch(seed, canonical('run-b', 2, {
    payload: {
      eventType: 'run.todo_updated',
      data: { runId: 'run-b', stepId: 'late', step: null },
    },
  }), dependencies)
  replay.dispatch(seed, {
    done: true,
    finalResponseExpected: false,
    model: 'late-run-b-model',
  }, dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.model, 'run-b-paused-model')
  assert.equal(assistant?.agentRunId, 'run-b')
  assert.equal(assistant?.canonicalOutput?.runId, 'run-b')
})
