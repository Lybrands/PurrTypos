import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createConversationSessionLifecycle,
  readStableConversationProjection,
  retryCurrentConversationRead,
} from './conversationSessionLifecycle.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

test('A hydration and edit cannot commit after switching to B', () => {
  const lifecycle = createConversationSessionLifecycle<number>()
  const a = lifecycle.beginLoad(7)
  lifecycle.setDraft(7, 'A 草稿')
  const editA = lifecycle.beginEdit(a, 'client:turn-a:user')

  const b = lifecycle.beginLoad(8)
  lifecycle.setDraft(8, 'B 草稿')
  assert.equal(lifecycle.canAct(b), false)
  assert.equal(lifecycle.finishLoad(b), true)
  assert.equal(lifecycle.canAct(b), true)
  assert.equal(lifecycle.finishLoad(a), false)
  assert.equal(lifecycle.canSubmitEdit(editA), false)
  assert.equal(lifecycle.getDraft(8), 'B 草稿')
  assert.equal(lifecycle.getDraft(7), 'A 草稿')
})

test('committed switch cancels editor and pending viewport work by identity', () => {
  const lifecycle = createConversationSessionLifecycle<number>()
  const a = lifecycle.beginLoad(7)
  lifecycle.finishLoad(a)
  const editA = lifecycle.beginEdit(a, 'conversation:1:user')
  const b = lifecycle.beginLoad(8)

  assert.notEqual(a.identity, b.identity)
  assert.equal(lifecycle.currentIdentity(), b.identity)
  assert.equal(lifecycle.canSubmitEdit(editA), false)
})

test('switching from A to no session invalidates late hydration and edits', () => {
  const lifecycle = createConversationSessionLifecycle<number>()
  const a = lifecycle.beginLoad(7)
  const editA = lifecycle.beginEdit(a, 'conversation:1:user')

  lifecycle.invalidate()

  assert.equal(lifecycle.currentToken(), undefined)
  assert.equal(lifecycle.isCurrent(a), false)
  assert.equal(lifecycle.finishLoad(a), false)
  assert.equal(lifecycle.canSubmitEdit(editA), false)
})

test('stable projection read retries across every runtime revision change', async () => {
  let revision = 0
  const first = deferred<{ rows: string[]; latest: string }>()
  const second = deferred<{ rows: string[]; latest: string }>()
  const third = deferred<{ rows: string[]; latest: string }>()
  const reads = [first, second, third]
  let index = 0

  const pending = readStableConversationProjection({
    isCurrent: () => true,
    getRevision: () => revision,
    read: () => reads[index++].promise,
    isSuccessful: () => true,
    wait: async () => undefined,
  })

  revision = 1
  first.resolve({ rows: ['before run A'], latest: 'none' })
  while (index < 2) await Promise.resolve()
  revision = 2
  second.resolve({ rows: ['before run B'], latest: 'run-a' })
  while (index < 3) await Promise.resolve()
  third.resolve({ rows: ['after run B'], latest: 'run-b' })

  assert.deepEqual(await pending, {
    revision: 2,
    value: {
      rows: ['after run B'],
      latest: 'run-b',
    },
  })
  assert.equal(index, 3)
})

test('authoritative read retries transient failure without completing current load', async () => {
  const lifecycle = createConversationSessionLifecycle<number>()
  const token = lifecycle.beginLoad(7)
  let reads = 0
  let waits = 0

  const value = await retryCurrentConversationRead({
    isCurrent: () => lifecycle.isCurrent(token),
    read: async () => {
      reads += 1
      if (reads === 1) throw new Error('snapshot unavailable')
      return 'authoritative running projection'
    },
    isRetryable: () => true,
    wait: async () => { waits += 1 },
  })

  assert.equal(value, 'authoritative running projection')
  assert.equal(reads, 2)
  assert.equal(waits, 1)
  assert.equal(lifecycle.canAct(token), false)
})
