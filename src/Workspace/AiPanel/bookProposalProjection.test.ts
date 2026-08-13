import assert from 'node:assert/strict'
import test from 'node:test'
import type { AiAgentRunSnapshot } from '../../types.ts'
import { createBookProposalProjectionReconciler } from './bookProposalProjection.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

const productSnapshot = (proposalId: string): AiAgentRunSnapshot => ({
  version: 1,
  run: {
    runId: 'run-1',
    sessionId: 7,
    conversationId: null,
    status: 'running',
    lineage: { rootRunId: 'run-1', depth: 0 },
    finalResponse: '',
    execution: { attempt: 1, cancellationRequested: false },
    provenance: {},
  },
  todos: [],
  events: [],
  productEvents: [{
    version: 1,
    type: 'writing.proposed_setting_diff',
    runId: 'run-1',
    proposalId,
    toolCallId: 'call-1',
    effectIndex: 0,
    payload: {
      proposalId,
      kind: 'character',
      bookId: 'book-1',
      characterId: 1,
      before: { name: '甲', tags: '', profileMd: '旧' },
      proposed: { name: '甲', tags: '', profileMd: '新' },
    },
    chunk: {
      runId: 'run-1',
      proposedSettingDiff: {
        proposalId,
        kind: 'character',
        bookId: 'book-1',
        characterId: 1,
        before: { name: '甲', tags: '', profileMd: '旧' },
        proposed: { name: '甲', tags: '', profileMd: '新' },
      },
    },
  }],
  delegations: {
    items: [],
    aggregate: {
      state: 'ready',
      counts: { queued: 0, claimed: 0, running: 0, done: 0, failed: 0, canceled: 0 },
      requiredFailures: [],
      results: [],
    },
  },
  nextCursor: 0,
  hasMore: false,
})

test('tool completion reconciles durable proposal identity once before terminal', async () => {
  const pending = deferred<{ success: true; data: AiAgentRunSnapshot }>()
  const dispatched: unknown[] = []
  let reads = 0
  const reconciler = createBookProposalProjectionReconciler({
    getRunSnapshot: async () => {
      reads += 1
      return pending.promise
    },
    dispatch: (chunk) => { dispatched.push(chunk) },
  })

  const first = reconciler.refresh('run-1')
  const duplicate = reconciler.refresh('run-1')
  assert.equal(reads, 1)
  pending.resolve({ success: true, data: productSnapshot('proposal-1') })
  await Promise.all([first, duplicate])
  await reconciler.refresh('run-1')

  assert.equal(reads, 2)
  assert.deepEqual(dispatched, [productSnapshot('proposal-1').productEvents![0].chunk])
})

test('failed snapshot read is retryable and never marks an unseen proposal seen', async () => {
  const dispatched: unknown[] = []
  let attempt = 0
  const reconciler = createBookProposalProjectionReconciler({
    getRunSnapshot: async () => {
      attempt += 1
      return attempt === 1
        ? { success: false, error: 'temporary' }
        : { success: true, data: productSnapshot('proposal-retry') }
    },
    dispatch: (chunk) => { dispatched.push(chunk) },
  })

  await reconciler.refresh('run-1')
  await reconciler.refresh('run-1')
  assert.equal(dispatched.length, 1)
})

test('terminal refresh reads once after an early in-flight empty projection', async () => {
  const early = deferred<{ success: true; data: AiAgentRunSnapshot }>()
  const dispatched: unknown[] = []
  let reads = 0
  const reconciler = createBookProposalProjectionReconciler({
    getRunSnapshot: async () => {
      reads += 1
      if (reads === 1) return early.promise
      return { success: true, data: productSnapshot('proposal-terminal') }
    },
    dispatch: (chunk) => { dispatched.push(chunk) },
  })
  const toolRefresh = reconciler.refresh('run-1')
  const terminalRefresh = reconciler.refreshFinal('run-1')
  early.resolve({
    success: true,
    data: { ...productSnapshot('unused'), productEvents: [] },
  })
  await Promise.all([toolRefresh, terminalRefresh])

  assert.equal(reads, 2)
  assert.equal(dispatched.length, 1)
  assert.equal(
    (dispatched[0] as { proposedSettingDiff: { proposalId: string } })
      .proposedSettingDiff.proposalId,
    'proposal-terminal',
  )
})

test('terminal refresh retries transient snapshot failures before settling', async () => {
  const dispatched: unknown[] = []
  let reads = 0
  const reconciler = createBookProposalProjectionReconciler({
    getRunSnapshot: async () => {
      reads += 1
      if (reads < 3) return { success: false, error: 'temporary' }
      return { success: true, data: productSnapshot('proposal-late') }
    },
    dispatch: (chunk) => { dispatched.push(chunk) },
    waitBeforeFinalRetry: async () => {},
  })

  await reconciler.refreshFinal('run-1')

  assert.equal(reads, 3)
  assert.equal(dispatched.length, 1)
  assert.equal(
    (dispatched[0] as { proposedSettingDiff: { proposalId: string } })
      .proposedSettingDiff.proposalId,
    'proposal-late',
  )
})
