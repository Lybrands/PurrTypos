'use strict'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')

const { AgentChunkReplay } = loadTypeScriptModule(
  path.join(__dirname, 'chunkReplay.ts'),
)
const {
  dispatchAgentChunk,
  initialAgentAccumulator,
} = loadTypeScriptModule(path.join(__dirname, 'chunkHandlers/index.ts'))

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

const requestReceipt = (runId) => ({
  requestReceipt: {
    requestId: `request-for-${runId}`,
    sessionId: 7,
    status: 'run_bound',
    runId,
    cancelRequested: false,
    rejectionCode: null,
    revision: 3,
  },
})

const reduceLive = (seed, chunks) => {
  let messages = [
    { role: 'user', content: seed.userContent },
    {
      role: 'assistant',
      content: '',
      model: seed.model,
      turnStartedAt: seed.turnStartedAt,
    },
  ]
  const context = {
    acc: initialAgentAccumulator({
      sessionId: seed.sessionId,
      userText: seed.userContent,
      model: seed.model,
      turnStartedAt: seed.turnStartedAt,
    }),
    sessionId: seed.sessionId,
    turnId: seed.turnId,
    modelIdentity: { configId: model.id, name: seed.model || model.name },
    host: {
      readMessages: () => messages,
      replaceMessages: (next) => { messages = next },
      scheduleCommit: (updater) => { messages = updater(messages) },
      flushCommits: () => undefined,
      setRunning: () => undefined,
      isVisible: () => true,
      onHostChunk: () => undefined,
      onSettled: () => undefined,
    },
    persistConversation: false,
    now: () => performance.now(),
  }
  chunks.forEach((chunk) => dispatchAgentChunk(chunk, context))
  return messages.at(-1)
}

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

test('canonical long-task progress restores the long task id without public plan events', () => {
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
          title: '读取原作内容',
          status: 'completed',
          attempt: 1,
          maxAttempts: 2,
        }, {
          id: 'section:characters',
          position: 1,
          kind: 'generate_document_section',
          title: '分析人物',
          status: 'claimed',
          attempt: 1,
          maxAttempts: 4,
        }, {
          id: 'section:story',
          position: 2,
          kind: 'generate_document_section',
          title: '梳理故事',
          status: 'claimed',
          attempt: 1,
          maxAttempts: 4,
        }, {
          id: 'compose-final-response',
          position: 3,
          kind: 'compose_final_response',
          title: '生成回复',
          status: 'pending',
          attempt: 0,
          maxAttempts: 2,
        }],
      },
    },
  }), dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.longTaskId, 'longtask-1')
  assert.equal(assistant?.taskPlan, undefined)
})

test('replay keeps the LLM public plan when Recipe progress arrives', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-public-plan-replay',
    sessionId: 535,
    userContent: '分析原作并续写',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, canonical('root-run-1', 1, {
    payload: {
      eventType: 'run.todos_updated',
      data: {
        runId: 'root-run-1',
        title: 'LLM 公开计划',
        status: 'running',
        steps: [{
          id: 'understand-source',
          title: '理解原作',
          type: 'read',
          status: 'done',
          dependsOn: [],
        }, {
          id: 'write-continuation',
          title: '撰写续篇',
          type: 'write',
          status: 'running',
          dependsOn: ['understand-source'],
        }],
      },
    },
  }), dependencies)
  replay.dispatch(seed, canonical('root-run-1', 2, {
    payload: {
      eventType: 'long_task.progress',
      data: {
        taskId: 'recipe-task-1',
        taskTitle: 'Recipe execution units',
        status: 'running',
        units: [{
          id: 'understand-source',
          position: 0,
          title: 'Recipe 收集素材',
          kind: 'collect_evidence',
          status: 'completed',
        }, {
          id: 'write-continuation',
          position: 1,
          title: 'Recipe 生成正文',
          kind: 'generate_document_section',
          status: 'running',
          dependsOn: [],
        }],
      },
    },
  }), dependencies)

  const plan = replay.assistant(seed.turnId)?.taskPlan
  assert.equal(plan?.title, 'LLM 公开计划')
  assert.deepEqual(
    plan?.steps.map((step) => [step.id, step.title, step.dependsOn]),
    [
      ['understand-source', '理解原作', []],
      ['write-continuation', '撰写续篇', ['understand-source']],
    ],
  )
})

test('live and replay converge when Recipe progress interleaves Root plan events', () => {
  const seed = {
    turnId: 'turn-plan-ordering',
    sessionId: 537,
    userContent: '理解原作并续写',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const chunks = [
    canonical('root-run-ordering', 1, {
      payload: {
        eventType: 'run.todos_updated',
        data: {
          title: '续写故事',
          status: 'running',
          steps: [{
            id: 'understand-source',
            title: '理解原作',
            type: 'analyze',
            status: 'done',
          }, {
            id: 'draft-continuation',
            title: '撰写续篇',
            type: 'write',
            status: 'running',
            dependsOn: ['understand-source'],
          }],
        },
      },
    }),
    canonical('root-run-ordering', 2, {
      payload: {
        eventType: 'long_task.progress',
        data: {
          taskId: 'recipe-task-ordering',
          taskTitle: 'Recipe 内部执行',
          status: 'completed',
          units: [{
            id: 'validate',
            title: '校验候选稿',
            status: 'completed',
            plannerStepId: 'draft-continuation',
          }, {
            id: 'publish',
            title: '发布候选稿',
            status: 'completed',
            plannerStepId: 'draft-continuation',
          }],
        },
      },
    }),
    canonical('root-run-ordering', 3, {
      payload: {
        eventType: 'run.todo_updated',
        data: {
          stepId: 'draft-continuation',
          status: 'running',
          step: {
            id: 'draft-continuation',
            title: '撰写续篇',
            type: 'write',
            status: 'done',
            dependsOn: ['understand-source'],
          },
        },
      },
    }),
    canonical('root-run-ordering', 4, {
      kind: 'run.lifecycle',
      payload: { status: 'done' },
    }),
  ]

  const liveAfterProgress = reduceLive(seed, chunks.slice(0, 2))
  const replay = new AgentChunkReplay()
  chunks.slice(0, 2).forEach((chunk) => replay.dispatch(
    seed,
    chunk,
    { cfg: model, appMessage },
  ))
  const replayAfterProgress = replay.assistant(seed.turnId)

  assert.deepEqual(replayAfterProgress, liveAfterProgress)
  assert.equal(replayAfterProgress?.longTaskId, 'recipe-task-ordering')
  assert.equal(replayAfterProgress?.taskPlan?.status, 'running')
  assert.deepEqual(
    replayAfterProgress?.taskPlan?.steps.map((step) => step.status),
    ['done', 'running'],
  )

  chunks.slice(2).forEach((chunk) => replay.dispatch(
    seed,
    chunk,
    { cfg: model, appMessage },
  ))
  const liveAssistant = reduceLive(seed, chunks)
  const replayedAssistant = replay.assistant(seed.turnId)

  assert.deepEqual(replayedAssistant, liveAssistant)
  assert.equal(replayedAssistant?.longTaskId, 'recipe-task-ordering')
  const plan = replayedAssistant?.taskPlan
  assert.deepEqual(
    plan?.steps.map((step) => [
      step.id,
      step.title,
      step.status,
      step.dependsOn,
    ]),
    [
      ['understand-source', '理解原作', 'done', undefined],
      ['draft-continuation', '撰写续篇', 'done', ['understand-source']],
    ],
  )
  assert.equal(plan?.runId, 'root-run-ordering')
  assert.equal(plan?.title, '续写故事')
  assert.equal(plan?.status, 'done')
  const encodedPlan = JSON.stringify(plan)
  assert.equal(encodedPlan.includes('Recipe'), false)
  assert.equal(encodedPlan.includes('校验候选稿'), false)
  assert.equal(encodedPlan.includes('发布候选稿'), false)
  assert.equal(encodedPlan.includes('plannerStepId'), false)
})

test('screenplay canonical Root lifecycle is identical live and on replay', () => {
  const seed = {
    turnId: 'turn-screenplay-canonical',
    rootRunId: 'root-screenplay-canonical',
    sessionId: 538,
    userContent: '创作下一集并生成候选稿',
    model: model.name,
    // Keep the wall-clock-derived duration deterministic so the assertion below
    // compares only canonical reducer state, not scheduling jitter.
    turnStartedAt: performance.now() + 60_000,
  }
  const root = seed.rootRunId
  const delegation = 'delegation-screenplay-research'
  const dependencies = { cfg: model, appMessage }
  const delegationEvent = (sequence, payload) => canonical(root, sequence, {
    turnId: seed.turnId,
    kind: 'delegation.event',
    channel: 'delegation',
    payload: {
      delegationId: delegation,
      runId: root,
      agentName: 'researcher',
      agentTitle: '研究 Agent',
      objective: '核验场景证据',
      ...payload,
    },
  })
  const chunks = [
    requestReceipt(root),
    canonical(root, 1, {
      turnId: seed.turnId,
      kind: 'run.lifecycle',
      payload: { status: 'running' },
    }),
    canonical(root, 2, {
      turnId: seed.turnId,
      payload: {
        eventType: 'run.todos_updated',
        data: {
          runId: root,
          title: '完成下一集候选稿',
          goal: '依据原作完成下一集',
          status: 'running',
          steps: [{
            id: 'collect-evidence',
            title: '理解原作依据',
            type: 'analyze',
            executor: 'tool',
            status: 'running',
            depends_on: [],
          }, {
            id: 'draft-episode',
            title: '创作下一集',
            type: 'write',
            executor: 'model',
            status: 'pending',
            depends_on: ['collect-evidence'],
          }, {
            id: 'deliver-candidate',
            title: '交付候选稿',
            type: 'review',
            executor: 'model',
            status: 'pending',
            depends_on: ['draft-episode'],
          }],
        },
      },
    }),
    canonical(root, 3, {
      turnId: seed.turnId,
      payload: {
        eventType: 'long_task.dispatched',
        data: {
          taskId: 'screenplay-task-1',
          dispatchReceiptId: 'dispatch-receipt-screenplay-1',
          status: 'running',
        },
      },
    }),
    // Sequence 4 is the persisted PRIVATE long_task.progress event. Public
    // live and replay cursors intentionally retain this gap.
    canonical(root, 5, {
      turnId: seed.turnId,
      kind: 'operation.started',
      channel: 'operation',
      payload: {
        operationId: 'tool-read-source',
        kind: 'tool',
        display: {
          labelKey: 'agent.operation.tool',
          labelParams: { toolName: 'readScreenplaySource' },
        },
      },
    }),
    canonical(root, 6, {
      turnId: seed.turnId,
      source: 'tool',
      kind: 'tool.event',
      channel: 'operation',
      payload: {
        operationId: 'tool-read-source',
        toolCallId: 'tool-call-read-source',
        toolName: 'readScreenplaySource',
      },
    }),
    canonical(root, 7, {
      turnId: seed.turnId,
      kind: 'operation.finished',
      channel: 'operation',
      payload: {
        operationId: 'tool-read-source',
        status: 'succeeded',
        durationMs: 12,
      },
    }),
    canonical(root, 8, {
      turnId: seed.turnId,
      payload: {
        eventType: 'run.todo_updated',
        data: {
          step_id: 'collect-evidence',
          status: 'running',
          step: {
            id: 'collect-evidence',
            title: '理解原作依据',
            type: 'analyze',
            executor: 'tool',
            status: 'done',
            depends_on: [],
            result_summary: '已读取并核验原作依据',
          },
        },
      },
    }),
    canonical(root, 9, {
      turnId: seed.turnId,
      payload: {
        eventType: 'conversation.compaction.started',
        data: { status: 'running', beforeTokens: 12000 },
      },
    }),
    canonical(root, 10, {
      turnId: seed.turnId,
      payload: {
        eventType: 'conversation.compaction.completed',
        data: { status: 'completed', beforeTokens: 12000, afterTokens: 4800 },
      },
    }),
    delegationEvent(11, { eventType: 'status', status: 'running' }),
    delegationEvent(16, { eventType: 'status', status: 'done' }),
    canonical(root, 17, {
      turnId: seed.turnId,
      payload: {
        eventType: 'run.todos_updated',
        data: {
          runId: root,
          title: '完成下一集候选稿',
          goal: '依据原作完成下一集',
          status: 'running',
          steps: [{
            id: 'collect-evidence',
            title: '理解原作依据',
            type: 'analyze',
            executor: 'tool',
            status: 'done',
            depends_on: [],
            result_summary: '已读取并核验原作依据',
          }, {
            id: 'draft-episode',
            title: '根据证据完成下一集',
            description: '检查点调整后的创作说明',
            type: 'write',
            executor: 'model',
            status: 'running',
            depends_on: ['collect-evidence'],
          }, {
            id: 'deliver-candidate',
            title: '交付下一集候选稿',
            type: 'review',
            executor: 'model',
            status: 'pending',
            depends_on: ['draft-episode'],
          }],
        },
      },
    }),
    canonical(root, 18, {
      turnId: seed.turnId,
      payload: {
        eventType: 'run.todo_updated',
        data: {
          step_id: 'draft-episode',
          status: 'running',
          step: {
            id: 'draft-episode',
            title: '根据证据完成下一集',
            description: '检查点调整后的创作说明',
            type: 'write',
            executor: 'model',
            status: 'done',
            depends_on: ['collect-evidence'],
            result_summary: '下一集正文候选已经完成',
          },
        },
      },
    }),
    canonical(root, 19, {
      turnId: seed.turnId,
      payload: {
        eventType: 'run.todo_updated',
        data: {
          step_id: 'deliver-candidate',
          status: 'running',
          step: {
            id: 'deliver-candidate',
            title: '交付下一集候选稿',
            type: 'review',
            executor: 'model',
            status: 'done',
            depends_on: ['draft-episode'],
            result_summary: '候选稿已完成权威校验并准备交付',
          },
        },
      },
    }),
    canonical(root, 22, {
      turnId: seed.turnId,
      kind: 'run.lifecycle',
      payload: {
        status: 'done',
        final_response: '下一集候选稿已经完成。',
      },
    }),
    { done: true, model: 'screenplay-final-model' },
  ]

  const live = reduceLive(seed, chunks)
  const replay = new AgentChunkReplay()
  chunks.forEach((chunk) => replay.dispatch(seed, chunk, dependencies))
  const restored = replay.assistant(seed.turnId)

  assert.equal(chunks.some((chunk) => chunk?.visibility === 'private'), false)
  assert.equal(JSON.stringify(chunks).includes('Recipe 生成正文'), false)
  assert.equal(JSON.stringify(chunks).includes('plannerStepId'), false)
  assert.equal(chunks.some((chunk) => (
    chunk?.runId === root
    && chunk?.kind === 'provider.content_delta'
    && chunk?.channel === 'final'
  )), false)
  assert.deepEqual(restored, live)
  assert.equal(restored?.agentRunId, root)
  assert.equal(restored?.content, '下一集候选稿已经完成。')
  assert.equal(restored?.longTaskId, 'screenplay-task-1')
  assert.equal(restored?.taskPlan?.runId, root)
  assert.equal(restored?.taskPlan?.status, 'done')
  assert.deepEqual(
    restored?.taskPlan?.steps.map((step) => [
      step.id,
      step.title,
      step.status,
      step.dependsOn,
    ]),
    [
      ['collect-evidence', '理解原作依据', 'done', []],
      ['draft-episode', '根据证据完成下一集', 'done', ['collect-evidence']],
      ['deliver-candidate', '交付下一集候选稿', 'done', ['draft-episode']],
    ],
  )
  const publicPlan = JSON.stringify(restored?.taskPlan)
  assert.equal(publicPlan.includes('Recipe'), false)
  assert.equal(publicPlan.includes('校验候选稿'), false)
  assert.equal(publicPlan.includes('plannerStepId'), false)
  const dispatch = chunks.find((chunk) => (
    chunk?.payload?.eventType === 'long_task.dispatched'
  ))
  assert.deepEqual(dispatch?.payload?.data, {
    taskId: 'screenplay-task-1',
    dispatchReceiptId: 'dispatch-receipt-screenplay-1',
    status: 'running',
  })
  assert.equal(chunks.some((chunk) => chunk?.sequence === 4), false)
  assert.deepEqual(restored?.canonicalOutput?.operationOrder, ['tool-read-source'])
  assert.equal(
    restored?.canonicalOutput?.operations['tool-read-source']?.toolName,
    'readScreenplaySource',
  )
  assert.deepEqual(restored?.contextCompaction, {
    status: 'completed',
    beforeTokens: 12000,
    afterTokens: 4800,
  })
  assert.deepEqual(restored?.delegations?.map((item) => [
    item.delegationId,
    item.runId,
    item.status,
  ]), [[delegation, root, 'done']])
  const delegationMessage = restored?.subAgentActivities?.[0]?.message
  assert.equal(delegationMessage?.content, '')
  assert.equal(delegationMessage?.agentRunId, root)
  assert.deepEqual(
    delegationMessage?.canonicalOutput?.operationOrder,
    [],
  )
  assert.deepEqual(
    restored?.taskPlan?.steps.map((step) => step.resultSummary),
    [
      '已读取并核验原作依据',
      '下一集正文候选已经完成',
      '候选稿已完成权威校验并准备交付',
    ],
  )
})

test('replay keeps the owning Run public plan isolated from foreign Run events', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-root-plan-ownership',
    sessionId: 536,
    userContent: '续写正文',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch(seed, canonical('root-run-a', 1, {
    turnId: seed.turnId,
    payload: {
      eventType: 'run.todos_updated',
      data: {
        title: 'Root Run 公开计划',
        status: 'running',
        steps: [{
          id: 'draft',
          title: '起草正文',
          type: 'write',
          status: 'running',
        }],
      },
    },
  }), dependencies)
  replay.dispatch(seed, canonical('root-run-a', 2, {
    turnId: seed.turnId,
    outputStreamId: 'root-final',
    invocationId: 'root-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: 'Root 回答' },
  }), dependencies)
  replay.dispatch(seed, canonical('foreign-run-a', 1, {
    payload: {
      eventType: 'run.todo_updated',
      data: {
        stepId: 'draft',
        status: 'done',
        step: {
          id: 'draft',
          title: '外部 Run 改写步骤',
          type: 'write',
          status: 'done',
        },
      },
    },
  }), dependencies)
  replay.dispatch(seed, canonical('foreign-run-a', 2, {
    kind: 'run.lifecycle',
    payload: { status: 'done' },
  }), dependencies)
  replay.dispatch(seed, canonical('foreign-run-a', 3, {
    outputStreamId: 'foreign-final',
    invocationId: 'foreign-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '外部 Run 回答' },
  }), dependencies)

  let assistant = replay.assistant(seed.turnId)
  let plan = assistant?.taskPlan
  assert.equal(assistant?.agentRunId, 'root-run-a')
  assert.equal(assistant?.canonicalOutput?.runId, 'root-run-a')
  assert.equal(assistant?.streamingContent, 'Root 回答')
  assert.equal(assistant?.canonicalOutput?.finalText, 'Root 回答')
  assert.equal(plan?.runId, 'root-run-a')
  assert.equal(plan?.status, 'running')
  assert.equal(plan?.steps[0]?.title, '起草正文')
  assert.equal(plan?.steps[0]?.status, 'running')

  replay.dispatch(seed, canonical('root-run-a', 3, {
    kind: 'run.lifecycle',
    visibility: 'private',
    payload: { status: 'done' },
  }), dependencies)

  plan = replay.assistant(seed.turnId)?.taskPlan
  assert.equal(plan?.status, 'running')

  replay.dispatch(seed, canonical('root-run-a', 4, {
    kind: 'run.lifecycle',
    payload: { status: 'done' },
  }), dependencies)

  plan = replay.assistant(seed.turnId)?.taskPlan
  assert.equal(plan?.status, 'done')
})

test('screenplay replay projects authorized unit work and final-response text under the Root', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-screenplay-related-runs',
    rootRunId: 'root-screenplay',
    sessionId: 537,
    userContent: '完成下一集',
    model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }

  replay.dispatch({
    ...seed,
    eventRunId: 'root-screenplay',
    runRole: 'root',
  }, canonical('root-screenplay', 1, {
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }), dependencies)
  const unitSeed = {
    ...seed,
    eventRunId: 'unit-screenplay',
    runRole: 'unit',
  }
  replay.dispatch(unitSeed, canonical('unit-screenplay', 1, {
    outputStreamId: 'unit-commentary',
    invocationId: 'unit-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'commentary',
    payload: { delta: '我先读取项目和分集上下文。' },
  }), dependencies)
  replay.dispatch(unitSeed, canonical('unit-screenplay', 2, {
    kind: 'operation.started',
    channel: 'operation',
    payload: {
      operationId: 'read-project',
      kind: 'tool',
      startedAt: '2026-08-12T08:00:02+00:00',
      display: {
        labelKey: 'agent.operation.tool',
        labelParams: { toolName: 'inspectScreenplayProject' },
      },
    },
  }), dependencies)
  replay.dispatch(unitSeed, canonical('unit-screenplay', 3, {
    kind: 'operation.finished',
    channel: 'operation',
    payload: {
      operationId: 'read-project',
      status: 'succeeded',
      finishedAt: '2026-08-12T08:00:03+00:00',
      durationMs: 15,
    },
  }), dependencies)
  replay.dispatch(unitSeed, canonical('unit-screenplay', 4, {
    outputStreamId: 'unit-private-final',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '不应成为对话最终回答的单元结果。' },
  }), dependencies)
  replay.dispatch(unitSeed, canonical('unit-screenplay', 5, {
    kind: 'run.lifecycle',
    payload: { status: 'done', finalResponse: '不应覆盖 Root。' },
  }), dependencies)
  replay.dispatch({
    ...seed,
    eventRunId: 'unbound-related-run',
    runRole: 'related',
  }, canonical('unbound-related-run', 1, {
    outputStreamId: 'unbound-commentary',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'commentary',
    payload: { delta: '未绑定的相关 Run 不应获得投影权限。' },
  }), dependencies)

  const finalSeed = {
    ...seed,
    eventRunId: 'final-screenplay',
    runRole: 'final_response',
  }
  replay.dispatch(finalSeed, canonical('final-screenplay', 1, {
    outputStreamId: 'final-response-stream',
    invocationId: 'final-response-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '第 4 集候选稿已经完成。' },
  }), dependencies)
  replay.dispatch(finalSeed, canonical('final-screenplay', 2, {
    outputStreamId: 'final-response-stream',
    invocationId: 'final-response-invocation',
    kind: 'stream.committed',
    channel: 'final',
    payload: {},
  }), dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.agentRunId, 'root-screenplay')
  assert.equal(assistant?.canonicalOutput?.runId, 'root-screenplay')
  assert.equal(assistant?.canonicalOutput?.runStatus, 'running')
  assert.equal(assistant?.content, '第 4 集候选稿已经完成。')
  assert.equal(assistant?.canonicalOutput?.finalText, '第 4 集候选稿已经完成。')
  assert.deepEqual(assistant?.commentaryBlocks, ['我先读取项目和分集上下文。'])
  assert.equal(
    assistant?.canonicalOutput?.operations['read-project']?.runId,
    'unit-screenplay',
  )
  assert.equal(
    assistant?.canonicalOutput?.operations['read-project']?.status,
    'succeeded',
  )
})

test('delegated child output is isolated from the root and remains inspectable', () => {
  const replay = new AgentChunkReplay()
  const seed = {
    turnId: 'turn-child-inspection', rootRunId: 'root-child-inspection',
    sessionId: 540, userContent: '检查资料', model: model.name,
    turnStartedAt: performance.now(),
  }
  const dependencies = { cfg: model, appMessage }
  replay.dispatch({ ...seed, eventRunId: seed.rootRunId, runRole: 'root' }, canonical(seed.rootRunId, 1, {
    kind: 'delegation.event', channel: 'delegation', payload: {
      eventType: 'status', delegationId: 'delegation-reader', runId: 'child-reader',
      agentName: 'reader', agentTitle: '资料核对 Agent', objective: '核对第一章', status: 'running',
    },
  }), dependencies)
  const childSeed = { ...seed, eventRunId: 'child-reader', runRole: 'unit' }
  replay.dispatch(childSeed, canonical('child-reader', 1, {
    outputStreamId: 'child-commentary', source: 'provider',
    kind: 'provider.content_delta', channel: 'commentary', payload: { delta: '正在核对原文。' },
  }), dependencies)
  replay.dispatch(childSeed, canonical('child-reader', 2, {
    outputStreamId: 'child-final', source: 'provider',
    kind: 'provider.content_delta', channel: 'final', payload: { delta: '核对完成。' },
  }), dependencies)
  replay.dispatch(childSeed, canonical('child-reader', 3, {
    outputStreamId: 'child-final', kind: 'stream.committed', channel: 'final', payload: {},
  }), dependencies)

  const assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.content, '')
  assert.equal(assistant?.commentaryBlocks, undefined)
  assert.equal(assistant?.subAgentActivities?.[0]?.message.content, '核对完成。')
  assert.deepEqual(assistant?.subAgentActivities?.[0]?.message.commentaryBlocks, undefined)
  assert.equal(
    assistant?.subAgentActivities?.[0]?.message.canonicalOutput?.commentaryBlocks[0]?.text,
    '正在核对原文。',
  )
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

  replay.dispatch(seed, requestReceipt('run-resumed'), dependencies)
  replay.dispatch(seed, canonical('run-resumed', 1, {
    turnId: seed.turnId,
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-resumed', 2, {
    turnId: seed.turnId,
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
  replay.dispatch(seed, canonical('run-resumed', 3, {
    turnId: seed.turnId,
    outputStreamId: 'resumed-final',
    invocationId: 'resumed-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '候选稿已发布。' },
  }), dependencies)
  replay.dispatch(seed, canonical('run-resumed', 4, {
    turnId: seed.turnId,
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

test('historical source Root events do not cancel a resumed turn replay', () => {
  const replay = new AgentChunkReplay()
  const dependencies = { cfg: model, appMessage }
  const currentRoot = {
    turnId: 'turn-resumed-history',
    rootRunId: 'run-continuation',
    sessionId: 7,
    userContent: '继续完成结构设计',
    turnStartedAt: performance.now(),
  }

  replay.dispatch({
    ...currentRoot,
    eventRunId: 'run-source',
    runRole: 'related',
  }, { done: true, aborted: true }, dependencies)

  assert.equal(replay.assistant(currentRoot.turnId), undefined)

  const activeSeed = {
    ...currentRoot,
    eventRunId: 'run-continuation',
    runRole: 'root',
  }
  replay.dispatch(activeSeed, canonical('run-continuation', 1, {
    turnId: currentRoot.turnId,
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }), dependencies)
  replay.dispatch(activeSeed, { done: true, model: 'resumed-model' }, dependencies)

  assert.equal(replay.assistant(currentRoot.turnId)?.agentRunId, 'run-continuation')
  assert.equal(replay.assistant(currentRoot.turnId)?.termination, undefined)
})

test('paused resume switches the canonical root once and blocks late same-run terminal chunks', () => {
  const replay = new AgentChunkReplay()
  const dependencies = { cfg: model, appMessage }
  const seed = {
    turnId: 'turn-paused-twice',
    rootRunId: 'run-a',
    sessionId: 8,
    userContent: '暂停后继续，再次暂停',
    turnStartedAt: performance.now(),
  }

  const rootPlanChunk = canonical('run-a', 1, {
    turnId: seed.turnId,
    payload: {
      eventType: 'run.todos_updated',
      data: { runId: 'run-a', title: 'A', status: 'paused', steps: [] },
    },
  })
  const rootResponseChunk = canonical('run-a', 2, {
    turnId: seed.turnId,
    outputStreamId: 'run-a-final',
    invocationId: 'run-a-invocation',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    payload: { delta: '保留 Root A 的响应。' },
  })
  replay.dispatch(seed, rootPlanChunk, dependencies)
  replay.dispatch(seed, rootResponseChunk, dependencies)
  replay.dispatch(seed, { done: true, finalResponseExpected: false }, dependencies)

  const foreignLifecycleChunk = canonical('foreign-child', 1, {
    // backendApi attaches the live request stream id to every envelope. It is
    // transport correlation only and cannot authorize a child as the Root.
    streamId: seed.turnId,
    turnId: 'different-turn',
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  })
  const foreignPlanChunk = canonical('foreign-child', 2, {
    streamId: seed.turnId,
    turnId: 'different-turn',
    payload: {
      eventType: 'run.todos_updated',
      data: {
        runId: 'foreign-child',
        title: 'Foreign',
        status: 'running',
        steps: [],
      },
    },
  })
  const foreignTerminalChunks = [{
    done: true,
    runResult: {
      runId: 'foreign-child',
      status: 'failed',
      errorCode: 'foreign_failure',
    },
  }, {
    done: true,
    finalResponse: 'Foreign child response',
    runResult: { runId: 'foreign-child', status: 'done' },
  }]
  replay.dispatch(seed, foreignLifecycleChunk, dependencies)
  replay.dispatch(seed, foreignPlanChunk, dependencies)
  foreignTerminalChunks.forEach((chunk) => replay.dispatch(seed, chunk, dependencies))
  let assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.agentRunId, 'run-a')
  assert.equal(assistant?.canonicalOutput?.runId, 'run-a')
  assert.equal(assistant?.canonicalOutput?.finalText, '保留 Root A 的响应。')
  assert.equal(assistant?.content, '保留 Root A 的响应。')
  assert.equal(assistant?.taskPlan?.runId, 'run-a')
  assert.equal(assistant?.taskPlan?.status, 'paused')
  assert.equal(assistant?.error, undefined)
  assert.equal(assistant?.termination, undefined)
  const live = reduceLive(seed, [
    requestReceipt('run-a'),
    rootPlanChunk,
    rootResponseChunk,
    { done: true, finalResponseExpected: false },
    foreignLifecycleChunk,
    foreignPlanChunk,
    ...foreignTerminalChunks,
  ])
  assert.equal(live?.agentRunId, assistant?.agentRunId)
  assert.equal(live?.canonicalOutput?.runId, assistant?.canonicalOutput?.runId)
  assert.equal(live?.content, assistant?.content)
  assert.equal(live?.taskPlan?.runId, assistant?.taskPlan?.runId)
  assert.equal(live?.taskPlan?.status, assistant?.taskPlan?.status)

  const resumedSeed = { ...seed, rootRunId: 'run-b' }
  replay.dispatch(resumedSeed, canonical('run-b', 1, {
    turnId: seed.turnId,
    kind: 'run.lifecycle',
    payload: { status: 'running' },
  }), dependencies)
  assert.equal(replay.assistant(seed.turnId)?.agentRunId, 'run-b')
  assert.equal(replay.assistant(seed.turnId)?.taskPlan, undefined)

  replay.dispatch(resumedSeed, canonical('run-b', 2, {
    payload: {
      eventType: 'run.todos_updated',
      data: { runId: 'run-b', title: 'B', status: 'running', steps: [] },
    },
  }), dependencies)
  assert.equal(replay.assistant(seed.turnId)?.agentRunId, 'run-b')
  assert.equal(replay.assistant(seed.turnId)?.canonicalOutput?.runId, 'run-b')
  assert.equal(replay.assistant(seed.turnId)?.taskPlan?.runId, 'run-b')

  replay.dispatch(resumedSeed, {
    done: true,
    finalResponseExpected: false,
    model: 'run-b-paused-model',
  }, dependencies)
  replay.dispatch(resumedSeed, canonical('run-b', 3, {
    payload: {
      eventType: 'run.todo_updated',
      data: { runId: 'run-b', stepId: 'late', step: null },
    },
  }), dependencies)
  replay.dispatch(resumedSeed, {
    done: true,
    finalResponseExpected: false,
    model: 'late-run-b-model',
  }, dependencies)

  assistant = replay.assistant(seed.turnId)
  assert.equal(assistant?.model, 'run-b-paused-model')
  assert.equal(assistant?.agentRunId, 'run-b')
  assert.equal(assistant?.canonicalOutput?.runId, 'run-b')
})
