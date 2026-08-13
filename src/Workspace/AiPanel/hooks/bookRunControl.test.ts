import assert from 'node:assert/strict'
import test from 'node:test'
import { createDurableBookRunControl } from './bookRunControl.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

test('stop intent before Run identity cancels the durable request exactly once immediately', async () => {
  const pending = deferred<void>()
  const canceledRequests: string[] = []
  const canceledRuns: string[] = []
  const stopping: boolean[] = []
  const control = createDurableBookRunControl({
    requestId: 'request-late',
    cancelRequest: async (requestId) => {
      canceledRequests.push(requestId)
      await pending.promise
    },
    cancelRun: async (runId) => {
      canceledRuns.push(runId)
    },
    setStopping: (value) => stopping.push(value),
  })

  assert.equal(control.requestStop(), true)
  assert.equal(control.requestStop(), false)
  await Promise.resolve()
  assert.deepEqual(canceledRequests, ['request-late'])
  assert.deepEqual(canceledRuns, [])
  control.observeRunId('run-late')
  control.observeRunId('run-late')
  assert.deepEqual(canceledRuns, [])
  assert.deepEqual(stopping, [true])

  pending.resolve()
  await control.cancelRequest()
  assert.equal(control.observeAuthoritativeTerminal('canceled'), true)
  assert.equal(control.observeAuthoritativeTerminal('canceled'), false)
  assert.deepEqual(stopping, [true, false])
})

test('cancel receipt is not a terminal and transport remains attached', async () => {
  let detachCalls = 0
  const control = createDurableBookRunControl({
    requestId: 'request-slow',
    cancelRequest: async () => undefined,
    cancelRun: async () => undefined,
    setStopping: () => undefined,
    detachTransport: () => { detachCalls += 1 },
  })
  control.observeRunId('run-slow')

  assert.equal(control.requestStop(), true)
  await control.cancelRequest()
  assert.equal(control.stopping(), true)
  assert.equal(detachCalls, 0)
  assert.equal(control.observeAuthoritativeTerminal('canceled'), true)
  assert.equal(control.stopping(), false)
})

test('legacy recovered Run without a request receipt keeps the Run cancel fallback', async () => {
  const canceledRuns: string[] = []
  const control = createDurableBookRunControl({
    cancelRun: async (runId) => { canceledRuns.push(runId) },
    setStopping: () => undefined,
  })
  control.observeRunId('legacy-run')

  assert.equal(control.requestStop(), true)
  await control.cancelRequest()
  assert.deepEqual(canceledRuns, ['legacy-run'])
})
