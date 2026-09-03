import assert from 'node:assert/strict'
import test from 'node:test'
import {
  initialCanonicalOutputState,
  reduceCanonicalOutput,
  type CanonicalOutputEvent,
} from './canonicalOutput.ts'

const replayCanonicalOutput = (events: readonly CanonicalOutputEvent[], afterSequence = 0) =>
  events.reduce(reduceCanonicalOutput, initialCanonicalOutputState(afterSequence))

const event = (
  sequence: number,
  overrides: Partial<CanonicalOutputEvent>,
): CanonicalOutputEvent => ({
  eventId: `event-${sequence}`,
  outputStreamId: null,
  runId: 'run-1',
  turnId: 'turn-1',
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

const events: CanonicalOutputEvent[] = [
  event(1, {
    kind: 'operation.started',
    channel: 'operation',
    payload: {
      operationId: 'operation-1',
      kind: 'tool',
      startedAt: '2026-08-12T08:00:01+00:00',
      display: {
        labelKey: 'agent.operation.tool',
        labelParams: { toolName: 'readScreenplay' },
      },
    },
  }),
  event(2, {
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'commentary',
    outputStreamId: 'stream-commentary',
    invocationId: 'invocation-commentary',
    payload: { delta: '正在核对' },
  }),
  event(3, {
    kind: 'operation.finished',
    channel: 'operation',
    payload: {
      operationId: 'operation-1',
      status: 'succeeded',
      finishedAt: '2026-08-12T08:00:01.048+00:00',
      durationMs: 48,
      display: {},
    },
  }),
  event(4, {
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    outputStreamId: 'stream-final',
    invocationId: 'invocation-final',
    payload: { delta: '已完成' },
  }),
]

test('canonical events project the public answer and invocation ownership', () => {
  const live = events.reduce(reduceCanonicalOutput, initialCanonicalOutputState())
  assert.equal(live.finalText, '已完成')
  assert.equal(live.operations['operation-1'].status, 'succeeded')
  assert.equal(
    live.commentaryBlocks[0]?.invocationId,
    'invocation-commentary',
  )
})

test('runtime event text does not impersonate Provider deltas', () => {
  const state = replayCanonicalOutput([
    event(1, {
      source: 'runtime',
      kind: 'runtime.event',
      channel: 'final',
      payload: { delta: 'host text' },
    }),
    event(2, {
      source: 'provider',
      kind: 'provider.content_delta',
      channel: 'final',
      outputStreamId: 'stream-final',
      invocationId: 'invocation-final',
      payload: { delta: 'Provider text' },
    }),
  ])

  assert.equal(state.finalText, 'Provider text')
})

test('Provider delta batches replay as ordered public text', () => {
  const state = replayCanonicalOutput([
    event(1, {
      source: 'provider',
      kind: 'provider.delta_batch',
      channel: 'final',
      outputStreamId: 'stream-final',
      invocationId: 'invocation-final',
      payload: {
        schemaVersion: 'purra.provider-delta-batch/v1',
        entries: [
          {
            sourceChunkIndex: 1,
            sourcePartIndex: 0,
            kind: 'provider.content_delta',
            payload: { delta: '批量' },
          },
          {
            sourceChunkIndex: 2,
            sourcePartIndex: 0,
            kind: 'provider.content_delta',
            payload: { delta: '输出' },
          },
        ],
      },
    }),
  ])

  assert.equal(state.finalText, '批量输出')
  assert.equal(state.finalStreamStatus, 'open')
})

test('Planner progress replays as typed intent without exposing private plan bytes', () => {
  const state = replayCanonicalOutput([
    event(1, {
      kind: 'operation.started',
      channel: 'operation',
      payload: {
        operationId: 'planning-1',
        kind: 'planning',
        startedAt: '2026-08-12T08:00:01+00:00',
        display: {
          labelKey: 'agent.operation.planning',
          labelParams: { revision: 0 },
        },
      },
    }),
    event(2, {
      source: 'provider',
      kind: 'provider.content_delta',
      channel: 'diagnostic',
      visibility: 'private',
      outputStreamId: 'planning-stream',
      invocationId: 'planning-invocation',
      payload: { delta: 'PRIVATE_PLAN_BYTES' },
    }),
    event(3, {
      source: 'provider',
      kind: 'planning.progress',
      channel: 'commentary',
      outputStreamId: 'planning-stream',
      invocationId: 'planning-invocation',
      payload: {
        schemaVersion: 'purra.planning-stream/v1',
        operationId: 'planning-1',
        revision: 0,
        attempt: 0,
        recordIndex: 1,
        sourceStart: 0,
        sourceEnd: 64,
        text: '先核对请求范围，再安排执行步骤。',
      },
    }),
    event(4, {
      source: 'runtime',
      kind: 'planning.progress',
      channel: 'commentary',
      payload: {
        schemaVersion: 'purra.planning-stream/v1',
        operationId: 'planning-1',
        revision: 0,
        attempt: 0,
        recordIndex: 2,
        text: '这条不是 Provider 投影。',
      },
    }),
  ])

  assert.deepEqual(state.planningProgress.map((item) => item.text), [
    '先核对请求范围，再安排执行步骤。',
  ])
  assert.equal(JSON.stringify(state).includes('PRIVATE_PLAN_BYTES'), false)
  assert.equal(state.commentaryText, '')
})

test('Provider public progress replays separately from final text and reasoning', () => {
  const state = replayCanonicalOutput([
    event(1, {
      source: 'provider',
      kind: 'provider.delta_batch',
      channel: 'diagnostic',
      visibility: 'private',
      outputStreamId: 'answer-stream',
      invocationId: 'answer-invocation',
      payload: {
        entries: [{
          sourceChunkIndex: 1,
          sourcePartIndex: 3,
          kind: 'provider.progress_delta',
          payload: { delta: '正在核对人物动机' },
        }],
      },
    }),
    event(2, {
      source: 'provider',
      kind: 'agent.progress',
      channel: 'commentary',
      outputStreamId: 'answer-stream',
      invocationId: 'answer-invocation',
      payload: {
        schemaVersion: 'purra.agent-progress/v1',
        text: '正在核对人物动机',
        sourceChunkIndex: 1,
      },
    }),
    event(3, {
      source: 'provider',
      kind: 'agent.progress',
      channel: 'commentary',
      outputStreamId: 'answer-stream',
      invocationId: 'answer-invocation',
      payload: {
        schemaVersion: 'purra.agent-progress/v1',
        text: '正在核对人物动机与关系',
        sourceChunkIndex: 2,
      },
    }),
  ])

  assert.deepEqual(state.agentProgress.map((item) => item.text), [
    '正在核对人物动机与关系',
  ])
  assert.equal(state.finalText, '')
  assert.equal(state.commentaryText, '')
  assert.equal(JSON.stringify(state).includes('provider.progress_delta'), false)
})

test('terminal Root lifecycle restores its authoritative final response', () => {
  const state = replayCanonicalOutput([
    event(1, {
      kind: 'run.lifecycle',
      payload: {
        status: 'done',
        final_response: '候选稿已经完成。',
      },
    }),
  ])

  assert.equal(state.finalText, '候选稿已经完成。')
  assert.equal(state.finalStreamStatus, 'committed')
  assert.equal(state.runTerminal, true)
})

test('terminal operation freezes backend duration', () => {
  const state = replayCanonicalOutput(events)
  assert.equal(state.operations['operation-1']?.durationMs, 48)
  assert.equal(state.operations['operation-1']?.status, 'succeeded')
})

test('duplicates are ignored while private journal gaps stay hidden', () => {
  const first = reduceCanonicalOutput(initialCanonicalOutputState(), events[0])
  assert.equal(reduceCanonicalOutput(first, events[0]), first)
  assert.equal(reduceCanonicalOutput(first, events[2]).lastSequence, 3)
})

test('event identity deduplicates a replay even when its envelope sequence changes', () => {
  const original = event(1, {
    source: 'provider',
    kind: 'planning.progress',
    channel: 'commentary',
    outputStreamId: 'planning-stream',
    invocationId: 'planning-invocation',
    payload: {
      schemaVersion: 'purra.planning-stream/v1',
      operationId: 'planning-operation',
      revision: 0,
      attempt: 0,
      recordIndex: 1,
      text: '先核对范围。',
    },
  })
  const first = reduceCanonicalOutput(initialCanonicalOutputState(), original)
  const replayed = reduceCanonicalOutput(first, { ...original, sequence: 2 })

  assert.equal(replayed, first)
  assert.equal(replayed.planningProgress.length, 1)
})

test('terminal Run rejects late planning progress without rewriting repair history', () => {
  const firstAttempt = event(1, {
    source: 'provider',
    kind: 'planning.progress',
    channel: 'commentary',
    outputStreamId: 'planning-stream-1',
    invocationId: 'planning-invocation-1',
    payload: {
      schemaVersion: 'purra.planning-stream/v1',
      operationId: 'planning-operation',
      revision: 0,
      attempt: 0,
      recordIndex: 1,
      text: '先核对范围。',
    },
  })
  const repairedAttempt = event(2, {
    source: 'provider',
    kind: 'planning.progress',
    channel: 'commentary',
    outputStreamId: 'planning-stream-2',
    invocationId: 'planning-invocation-2',
    payload: {
      schemaVersion: 'purra.planning-stream/v1',
      operationId: 'planning-operation',
      revision: 0,
      attempt: 1,
      recordIndex: 1,
      text: '正在修正规划约束。',
    },
  })
  const canceled = event(3, {
    kind: 'run.lifecycle',
    payload: { status: 'canceled' },
  })
  const late = event(4, {
    ...repairedAttempt,
    eventId: 'late-progress',
    sequence: 4,
    payload: {
      ...repairedAttempt.payload,
      recordIndex: 2,
      text: '迟到的说明。',
    },
  })
  const state = [firstAttempt, repairedAttempt, canceled, late].reduce(
    reduceCanonicalOutput,
    initialCanonicalOutputState(),
  )

  assert.deepEqual(state.planningProgress.map((item) => [
    item.invocationId,
    item.attempt,
    item.text,
  ]), [
    ['planning-invocation-1', 0, '先核对范围。'],
    ['planning-invocation-2', 1, '正在修正规划约束。'],
  ])
  assert.equal(state.runStatus, 'canceled')
})

test('delegation status remains scoped to the owning Run', () => {
  const state = replayCanonicalOutput([
    event(1, {
      kind: 'delegation.event',
      channel: 'delegation',
      payload: {
        eventType: 'status',
        delegationId: 'delegation-1',
        runId: 'run-1',
        agentName: 'reviewer',
        agentTitle: '审阅 Agent',
        objective: '独立审阅',
        status: 'running',
      },
    }),
  ])

  assert.equal(state.delegations['delegation-1']?.runId, 'run-1')
  assert.equal(state.delegations['delegation-1']?.status, 'running')
})

test('approval lifecycle is replayed from canonical runtime events', () => {
  const state = replayCanonicalOutput([
    event(1, {
      payload: {
        eventType: 'approval.requested',
        data: {
          approvalId: 'approval-1',
          toolName: 'writeScreenplay',
          title: '写入剧本',
          riskLevel: 'write',
          summary: '将当前候选稿写入正文',
        },
      },
    }),
    event(2, {
      payload: {
        eventType: 'approval.resolved',
        data: {
          approvalId: 'approval-1',
          toolName: 'writeScreenplay',
          status: 'approved',
        },
      },
    }),
  ])

  assert.deepEqual(state.approvalOrder, ['approval-1'])
  assert.deepEqual(state.approvals['approval-1'], {
    approvalId: 'approval-1',
    toolName: 'writeScreenplay',
    title: '写入剧本',
    riskLevel: 'write',
    summary: '将当前候选稿写入正文',
    status: 'approved',
  })
})
