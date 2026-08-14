import assert from 'node:assert/strict'
import test from 'node:test'
import {
  initialCanonicalOutputState,
  reduceCanonicalOutput,
  replayCanonicalOutput,
  type CanonicalOutputEvent,
} from './canonicalOutput.ts'

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

test('real-time reduction equals zero-based replay', () => {
  const live = events.reduce(reduceCanonicalOutput, initialCanonicalOutputState())
  assert.deepEqual(replayCanonicalOutput(events), live)
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

test('federated child output reuses the same canonical reducer', () => {
  const childDelta = event(1, {
    eventId: 'child-event-1',
    runId: 'child-run-1',
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    outputStreamId: 'child-stream',
    invocationId: 'child-invocation',
    payload: { delta: '子任务真实输出' },
  })
  const state = replayCanonicalOutput([
    event(1, {
      kind: 'delegation.event',
      channel: 'delegation',
      payload: {
        eventType: 'status',
        delegationId: 'delegation-1',
        parentRunId: 'run-1',
        childRunId: 'child-run-1',
        agentRole: 'reviewer',
        agentTitle: '审阅 Agent',
        objective: '独立审阅',
        status: 'running',
      },
    }),
    event(2, {
      kind: 'delegation.event',
      channel: 'delegation',
      payload: {
        eventType: 'child_output',
        delegationId: 'delegation-1',
        parentRunId: 'run-1',
        sourceRunId: 'child-run-1',
        agentRole: 'reviewer',
        agentTitle: '审阅 Agent',
        objective: '独立审阅',
        event: childDelta,
      },
    }),
  ])

  assert.equal(
    state.delegations['delegation-1']?.output.finalText,
    '子任务真实输出',
  )
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
