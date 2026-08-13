import assert from 'node:assert/strict'
import test from 'node:test'
import type { AiAgentRunSnapshot } from '../types.ts'
import { recoverDurableAgentStream } from './durableAgentStreamRecovery.ts'

const snapshot = (
  status: AiAgentRunSnapshot['run']['status'],
  events: AiAgentRunSnapshot['events'] = [],
): AiAgentRunSnapshot => ({
  version: 1,
  run: {
    runId: 'run-recover',
    sessionId: 7,
    conversationId: null,
    status,
    lineage: { rootRunId: 'run-recover', depth: 0 },
    finalResponse: status === 'done' ? '完成' : '',
    execution: { attempt: 1, cancellationRequested: false },
    provenance: {},
  },
  todos: [],
  events,
  delegations: {
    items: [],
    aggregate: {
      state: 'ready',
      counts: { queued: 0, claimed: 0, running: 0, done: 0, failed: 0, canceled: 0 },
      requiredFailures: [],
      results: [],
    },
  },
  nextCursor: events.at(-1)?.cursor ?? 0,
  hasMore: false,
})

test('durable EOF replays journal and emits no business terminal until Run settles', async () => {
  let poll = 0
  const emitted: Record<string, unknown>[] = []
  let waits = 0
  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async () => {
      poll += 1
      return {
        success: true,
        data: poll === 1
          ? snapshot('running', [{
              version: 1,
              cursor: 1,
              type: 'runtime.event',
              runId: 'run-recover',
              payload: {},
              chunk: { eventId: 'event-1', sequence: 1, kind: 'runtime.event' },
            }])
          : snapshot('done'),
      }
    },
    wait: async () => { waits += 1 },
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(waits, 1)
  assert.equal(emitted.filter((chunk) => chunk.done).length, 1)
  assert.deepEqual(emitted.at(-1)?.runResult, {
    runId: 'run-recover',
    status: 'done',
  })
  assert.equal(emitted[0].eventId, 'event-1')
})

test('exact request receipt overrides a stale latest-Run baseline exclusion', async () => {
  const emitted: Record<string, unknown>[] = []
  await recoverDurableAgentStream({
    excludeRunId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({
      success: true,
      data: {
        request: {
          requestId: 'request-replayed',
          sessionId: 7,
          status: 'run_bound',
          runId: 'run-recover',
          cancelRequested: false,
          rejectionCode: null,
          revision: 3,
        },
        prompt: '问题',
        snapshot: snapshot('done'),
      },
    }),
    getRunSnapshot: async () => {
      throw new Error('exact latest snapshot should be reused')
    },
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(
    (emitted.at(-1)?.runResult as { runId?: string } | undefined)?.runId,
    'run-recover',
  )
})

test('recovery retries read failures and deduplicates cursor and proposal occurrence', async () => {
  let poll = 0
  const emitted: Record<string, unknown>[] = []
  const running = snapshot('running', [{
    version: 1,
    cursor: 2,
    type: 'runtime.event',
    runId: 'run-recover',
    payload: {},
    chunk: { eventId: 'event-2', sequence: 2, kind: 'runtime.event' },
  }])
  running.productEvents = [{
    version: 1,
    type: 'writing.proposed_setting_diff',
    runId: 'run-recover',
    proposalId: 'proposal-1',
    effectIndex: 0,
    payload: {
      proposalId: 'proposal-1',
      kind: 'background',
      bookId: 'book-1',
      before: { content: '旧' },
      proposed: { content: '新' },
    },
    chunk: {
      runId: 'run-recover',
      proposedSettingDiff: {
        proposalId: 'proposal-1',
        kind: 'background',
        bookId: 'book-1',
        before: { content: '旧' },
        proposed: { content: '新' },
      },
    },
  }]
  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 1,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async () => {
      poll += 1
      if (poll === 1) return { success: false, data: running, error: 'offline' }
      if (poll < 4) return { success: true, data: running }
      return { success: true, data: snapshot('canceled') }
    },
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(emitted.filter((chunk) => chunk.eventId === 'event-2').length, 1)
  assert.equal(emitted.filter((chunk) => chunk.proposedSettingDiff).length, 1)
  assert.equal((emitted.at(-1)?.runResult as { status: string }).status, 'canceled')
})

test('terminal recovery drains every page and publishes authoritative final response', async () => {
  const emitted: Record<string, unknown>[] = []
  const first = snapshot('done', [{
    version: 1,
    cursor: 500,
    type: 'provider.content_delta',
    runId: 'run-recover',
    payload: {},
    chunk: { eventId: 'event-500', sequence: 500, kind: 'provider.content_delta' },
  }])
  first.hasMore = true
  first.nextCursor = 500
  first.run.finalResponse = '服务端完整终稿'
  const second = snapshot('done', [{
    version: 1,
    cursor: 501,
    type: 'stream.committed',
    runId: 'run-recover',
    payload: {},
    chunk: { eventId: 'event-501', sequence: 501, kind: 'stream.committed' },
  }])
  second.run.finalResponse = '服务端完整终稿'
  let reads = 0
  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async ({ after }) => {
      reads += 1
      assert.equal(after, reads === 1 ? 0 : 500)
      return { success: true, data: reads === 1 ? first : second }
    },
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(reads, 2)
  assert.deepEqual(emitted.map((chunk) => chunk.eventId).filter(Boolean), [
    'event-500',
    'event-501',
  ])
  assert.equal(emitted.at(-1)?.finalResponse, '服务端完整终稿')
})

test('pagination advances across a private-only output page without exposing it', async () => {
  const emitted: Record<string, unknown>[] = []
  const privateOnly = snapshot('done')
  privateOnly.hasMore = true
  privateOnly.nextCursor = 500
  const terminal = snapshot('done')
  terminal.nextCursor = 501
  terminal.run.finalResponse = '完成'
  let reads = 0
  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async ({ after }) => {
      reads += 1
      assert.equal(after, reads === 1 ? 0 : 500)
      return { success: true, data: reads === 1 ? privateOnly : terminal }
    },
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })
  assert.equal(reads, 2)
  assert.equal(emitted.length, 1)
  assert.equal(emitted[0].done, true)
})

test('accepted request waits for later Run binding and never synthesizes a terminal', async () => {
  const emitted: Record<string, unknown>[] = []
  let reads = 0
  let waits = 0
  await recoverDurableAgentStream({
    sessionId: 7,
    after: 0,
    getLatestRun: async () => {
      reads += 1
      if (reads === 1) {
        return {
          success: true,
          data: {
            request: {
              requestId: 'chat-receipt',
              sessionId: 7,
              status: 'accepted',
              runId: null,
              cancelRequested: false,
              rejectionCode: null,
              revision: 1,
            },
            prompt: '',
            snapshot: null,
          },
        }
      }
      return {
        success: true,
        data: {
          request: {
            requestId: 'chat-receipt',
            sessionId: 7,
            status: 'run_bound',
            runId: 'run-recover',
            cancelRequested: false,
            rejectionCode: null,
            revision: 3,
          },
          prompt: '问题',
          snapshot: snapshot('done'),
        },
      }
    },
    getRunSnapshot: async () => ({ success: true, data: snapshot('done') }),
    wait: async () => { waits += 1 },
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(waits, 1)
  assert.equal(emitted.length, 1)
  assert.equal((emitted[0].runResult as { status: string }).status, 'done')
})

test('authoritative pre-Run rejection settles as request result, never Run result', async () => {
  const emitted: Record<string, unknown>[] = []
  await recoverDurableAgentStream({
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({
      success: true,
      data: {
        request: {
          requestId: 'chat-rejected',
          sessionId: 7,
          status: 'rejected',
          runId: null,
          cancelRequested: false,
          rejectionCode: 'request_start_failed',
          revision: 2,
        },
        prompt: '',
        snapshot: null,
      },
    }),
    getRunSnapshot: async () => {
      throw new Error('no Run must be queried')
    },
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(emitted.length, 1)
  assert.equal(emitted[0].done, true)
  assert.equal(emitted[0].runResult, undefined)
  assert.equal(
    (emitted[0].requestResult as { status: string }).status,
    'rejected',
  )
})

test('retired bound request settles when its detached Run has no snapshot', async () => {
  const emitted: Record<string, unknown>[] = []
  let waits = 0
  await recoverDurableAgentStream({
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({
      success: true,
      data: {
        request: {
          requestId: 'chat-truncated',
          sessionId: 7,
          status: 'canceled',
          runId: 'run-truncated',
          cancelRequested: true,
          rejectionCode: 'history_truncated',
          revision: 4,
        },
        prompt: '',
        snapshot: null,
      },
    }),
    getRunSnapshot: async () => {
      throw new Error('retired Run is no longer session-readable')
    },
    wait: async () => { waits += 1 },
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(waits, 0)
  assert.equal(emitted.length, 1)
  assert.equal(emitted[0].aborted, true)
  assert.equal((emitted[0].requestResult as { status: string }).status, 'canceled')
})

test('long-task dispatch recovery never publishes its host receipt as authored text', async () => {
  const emitted: Record<string, unknown>[] = []
  const terminal = snapshot('done', [{
    version: 1,
    cursor: 1,
    type: 'long_task.dispatched',
    runId: 'run-recover',
    payload: {},
    chunk: {
      eventId: 'event-dispatched',
      sequence: 1,
      kind: 'runtime.event',
      payload: {
        eventType: 'long_task.dispatched',
        data: { taskId: 'long-task-1' },
      },
    },
  }])
  terminal.run.finalResponse = '已恢复原有长篇正文任务。'
  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async () => ({ success: true, data: terminal }),
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(emitted.at(-1)?.finalResponse, undefined)
  assert.equal(emitted.at(-1)?.finalResponseExpected, false)
})

test('long-task dispatch state survives pagination before terminal projection', async () => {
  const emitted: Record<string, unknown>[] = []
  const first = snapshot('done', [{
    version: 1,
    cursor: 500,
    type: 'long_task.dispatched',
    runId: 'run-recover',
    payload: {},
    chunk: {
      eventId: 'event-dispatched-page-1',
      sequence: 500,
      kind: 'runtime.event',
      payload: {
        eventType: 'long_task.dispatched',
        data: { taskId: 'long-task-paged' },
      },
    },
  }])
  first.hasMore = true
  first.nextCursor = 500
  const terminal = snapshot('done')
  terminal.nextCursor = 501
  terminal.run.finalResponse = '不可成为正文的分页派发回执'
  let reads = 0

  await recoverDurableAgentStream({
    runId: 'run-recover',
    sessionId: 7,
    after: 0,
    getLatestRun: async () => ({ success: true, data: null }),
    getRunSnapshot: async () => ({
      success: true,
      data: ++reads === 1 ? first : terminal,
    }),
    wait: async () => undefined,
    isAborted: () => false,
    emit: (chunk) => { emitted.push(chunk) },
  })

  assert.equal(reads, 2)
  assert.equal(emitted.at(-1)?.finalResponse, undefined)
  assert.equal(emitted.at(-1)?.finalResponseExpected, false)
})
