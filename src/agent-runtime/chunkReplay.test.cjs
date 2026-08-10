'use strict'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')

const { AgentChunkReplay } = loadTypeScriptModule(
  path.join(__dirname, 'chunkReplay.ts'),
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

  replay.dispatch(seed, {
    agentRunStarted: { runId: 'turn-1', status: 'running' },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'turn-1',
      title: '剧本创作任务',
      status: 'running',
      steps: [{
        id: 'plan',
        title: '理解请求',
        type: 'analyze',
        status: 'running',
      }],
    },
  }, dependencies)
  replay.dispatch(seed, { commentaryDelta: '先检查连续性' }, dependencies)
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
  replay.dispatch(seed, { delta: '第一场正文。' }, dependencies)
  replay.dispatch(seed, { done: true }, dependencies)

  const assistant = replay.assistant('turn-1')
  assert.equal(assistant?.content, '第一场正文。')
  assert.deepEqual(assistant?.commentaryBlocks, ['先检查连续性'])
  assert.equal(assistant?.taskPlan?.title, '剧本创作任务')
  assert.equal(assistant?.longTaskId, 'task-1')
  assert.equal(assistant?.durationMs != null && assistant.durationMs >= 200, true)
  assert.equal(assistant?.turnStartedAt, undefined)
})

test('projected execution summary is preserved before its tool operation', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-progress-tool',
    sessionId: 9,
    userContent: '生成场景表',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, {
    agentRunStarted: { runId: 'run-progress-tool', status: 'running' },
  }, dependencies)
  replay.dispatch(seed, {
    commentaryDelta: '核对本集目标、冲突和转折。\n',
    toolCalls: [{
      id: 'call-write',
      type: 'function',
      displayNames: { 'zh-CN': '写入剧本候选稿' },
      function: {
        name: 'writeScreenplayCandidatePart',
        arguments: '{"content":"<candidate payload omitted>"}',
      },
    }],
    toolCallsInProgress: true,
  }, dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.deepEqual(
    assistant?.commentaryBlocks,
    ['核对本集目标、冲突和转折。'],
  )
  assert.equal(assistant?.toolCallSegments?.length, 1)
  assert.equal(assistant?.toolCallSegments?.[0]?.commentaryBlockIndex, 0)
})

test('a completed fragment Run cannot terminalize its root execution plan', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-root',
    sessionId: 8,
    userContent: '修订完整剧本',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, {
    agentRunStarted: { runId: 'turn-root', status: 'running' },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'turn-root',
      title: '剧本创作任务',
      status: 'running',
      steps: [{
        id: 'episode-1',
        title: '创作第 1 集正文',
        type: 'write',
        status: 'done',
      }, {
        id: 'episode-2',
        title: '创作第 2 集正文',
        type: 'write',
        status: 'running',
      }, {
        id: 'publish',
        title: '整理候选稿',
        type: 'write',
        status: 'pending',
      }],
    },
  }, dependencies)

  replay.dispatch(seed, {
    agentRunStarted: { runId: 'run-scene-1', status: 'running' },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'run-scene-1',
      title: '第一场局部任务',
      status: 'running',
      steps: [{
        id: 'scene-write',
        title: '写入第一场',
        type: 'write',
        status: 'running',
      }],
    },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunTodoUpdated: {
      runId: 'run-scene-1',
      stepId: 'scene-write',
      status: 'done',
      step: {
        id: 'scene-write',
        title: '写入第一场',
        type: 'write',
        status: 'done',
      },
    },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunCompleted: {
      runId: 'run-scene-1',
      status: 'done',
      finalResponse: '第一场已经写入。',
    },
  }, dependencies)

  const assistant = replay.assistant('turn-root')
  assert.equal(assistant?.agentRunId, 'turn-root')
  assert.equal(assistant?.taskPlan?.runId, 'turn-root')
  assert.equal(assistant?.taskPlan?.status, 'running')
  assert.equal(assistant?.taskPlan?.steps[1]?.status, 'running')
  assert.equal(assistant?.content, '')

  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'turn-root',
      title: '剧本创作任务',
      status: 'done',
      steps: [{
        id: 'episode-1',
        title: '创作第 1 集正文',
        type: 'write',
        status: 'done',
      }, {
        id: 'episode-2',
        title: '创作第 2 集正文',
        type: 'write',
        status: 'done',
      }, {
        id: 'publish',
        title: '整理候选稿',
        type: 'write',
        status: 'done',
      }],
    },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunCompleted: {
      runId: 'turn-root',
      status: 'done',
      finalResponse: '完整剧本已经修订完成。',
    },
  }, dependencies)
  replay.dispatch(seed, { done: true }, dependencies)

  const completedAssistant = replay.assistant('turn-root')
  assert.equal(completedAssistant?.agentRunId, 'turn-root')
  assert.equal(completedAssistant?.taskPlan?.status, 'done')
  assert.equal(completedAssistant?.content, '完整剧本已经修订完成。')
})

test('reset removes replay state when the project or session changes', () => {
  const replay = new AgentChunkReplay()
  replay.dispatch({
    turnId: 'turn-1',
    sessionId: 7,
    userContent: '测试',
    turnStartedAt: performance.now(),
  }, { delta: '结果' }, { cfg: model, appMessage })

  replay.reset()

  assert.equal(replay.assistant('turn-1'), undefined)
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

  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'turn-paused',
      title: '剧本创作任务',
      status: 'paused',
      steps: [{
        id: 'generate',
        title: '生成候选稿',
        type: 'write',
        status: 'blocked',
      }],
    },
  }, dependencies)
  replay.dispatch(seed, { commentaryDelta: '已保留完成的检查点。' }, dependencies)
  replay.dispatch(seed, {
    done: true,
    finalResponseExpected: false,
  }, dependencies)

  const paused = replay.assistant('turn-paused')
  assert.equal(paused?.content, '')
  assert.equal(paused?.taskPlan?.status, 'paused')

  replay.dispatch(seed, {
    agentRunTodosUpdated: {
      runId: 'turn-paused',
      title: '剧本创作任务',
      status: 'running',
      steps: [{
        id: 'generate',
        title: '生成候选稿',
        type: 'write',
        status: 'running',
      }],
    },
  }, dependencies)
  replay.dispatch(seed, {
    agentRunCompleted: {
      runId: 'turn-paused',
      status: 'done',
      finalResponse: '候选稿已发布。',
    },
  }, dependencies)
  replay.dispatch(seed, { done: true }, dependencies)

  assert.equal(replay.assistant('turn-paused')?.content, '候选稿已发布。')
})
