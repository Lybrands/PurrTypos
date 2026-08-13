import assert from 'node:assert/strict'
import test from 'node:test'
import { createScreenplayOperationCommandLatch } from './screenplayOperationCommandLatch.ts'

test('cancel and resume are mutually exclusive for one operation revision', () => {
  const latch = createScreenplayOperationCommandLatch()
  const cancel = latch.tryAcquire('operation-1', 3, 'cancel')
  assert.ok(cancel)
  assert.equal(latch.tryAcquire('operation-1', 3, 'resume'), undefined)
  assert.equal(latch.tryAcquire('operation-1', 3, 'cancel'), undefined)

  latch.release(cancel)
  const resume = latch.tryAcquire('operation-1', 4, 'resume')
  assert.ok(resume)
  assert.equal(latch.tryAcquire('operation-1', 4, 'cancel'), undefined)
})

test('a stale completion cannot release a newer command token', () => {
  const latch = createScreenplayOperationCommandLatch()
  const first = latch.tryAcquire('operation-1', 3, 'resume')!
  latch.release(first)
  const second = latch.tryAcquire('operation-1', 3, 'cancel')!

  latch.release(first)
  assert.equal(latch.current()?.command, 'cancel')
  latch.release(second)
  assert.equal(latch.current(), undefined)
})

test('deferred cancel service keeps resume excluded until the exact command settles', async () => {
  let finishCancel!: () => void
  const cancelPending = new Promise<void>((resolve) => { finishCancel = resolve })
  const calls: string[] = []
  const latch = createScreenplayOperationCommandLatch()
  const issue = async (command: 'cancel' | 'resume') => {
    const token = latch.tryAcquire('operation-slow', 9, command)
    if (!token) return false
    calls.push(command)
    try {
      if (command === 'cancel') await cancelPending
    } finally {
      latch.release(token)
    }
    return true
  }

  const cancel = issue('cancel')
  assert.equal(await issue('resume'), false)
  assert.deepEqual(calls, ['cancel'])
  finishCancel()
  assert.equal(await cancel, true)
  assert.equal(await issue('resume'), true)
  assert.deepEqual(calls, ['cancel', 'resume'])
})

test('accepted cancel stays latched across reload failure until authoritative state', () => {
  const latch = createScreenplayOperationCommandLatch()
  const cancel = latch.tryAcquire('operation-slow', 9, 'cancel')!

  latch.holdUntilAuthoritative(cancel)
  latch.release(cancel)

  assert.equal(latch.hasPendingCancel(), true)
  assert.equal(latch.tryAcquire('operation-slow', 9, 'resume'), undefined)
  latch.observeOperation({
    id: 'operation-slow',
    revision: 9,
    status: 'paused',
    cancelRequestedAt: '2026-08-13T01:00:00Z',
  })
  assert.equal(latch.hasPendingCancel(), false)
  assert.equal(latch.current(), undefined)
  assert.ok(latch.tryAcquire('operation-slow', 10, 'resume'))
})

test('held cancel from an old operation owner cannot disable a new owner', () => {
  const latch = createScreenplayOperationCommandLatch()
  const oldCancel = latch.tryAcquire('operation-old', 3, 'cancel')!
  latch.holdUntilAuthoritative(oldCancel)
  latch.release(oldCancel)

  const nextOwner = latch.tryAcquire('operation-new', 1, 'resume')
  assert.ok(nextOwner)
  latch.clear()
  assert.equal(latch.current(), undefined)
})
