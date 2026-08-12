import assert from 'node:assert/strict'
import test from 'node:test'
import { createHistoryRequestCoordinator } from './historyRequestCoordinator.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

test('only the latest history request may commit or clear loading', () => {
  const coordinator = createHistoryRequestCoordinator()
  const oldRequest = coordinator.beginLatest()
  const latestRequest = coordinator.beginLatest()
  let loading = true
  let sessions = ['initial']

  const settle = (request: number, nextSessions: string[]) => {
    if (!coordinator.isCurrent(request)) return
    sessions = nextSessions
    loading = false
  }

  assert.equal(coordinator.isCurrent(oldRequest), false)
  assert.equal(coordinator.isCurrent(latestRequest), true)
  settle(oldRequest, ['stale book response'])
  assert.deepEqual(sessions, ['initial'])
  assert.equal(loading, true)
  settle(latestRequest, ['latest scope response'])
  assert.deepEqual(sessions, ['latest scope response'])
  assert.equal(loading, false)

  coordinator.invalidateLatest()
  assert.equal(coordinator.isCurrent(latestRequest), false)
})

test('unmount prevents pending requests from mutating component state', () => {
  const coordinator = createHistoryRequestCoordinator()
  const request = coordinator.beginLatest()

  coordinator.unmount()

  assert.equal(coordinator.isMounted(), false)
  assert.equal(coordinator.isCurrent(request), false)
})

test('duplicate deletion runs the service and callback only once per session', async () => {
  const coordinator = createHistoryRequestCoordinator()
  const pending = deferred<void>()
  let serviceCalls = 0
  let deleteCallbacks = 0
  const deleteSession = () => coordinator.runOnce('session:9', async () => {
    serviceCalls += 1
    await pending.promise
    deleteCallbacks += 1
  })

  const first = deleteSession()
  const duplicate = deleteSession()
  assert.equal(first, duplicate)
  assert.equal(serviceCalls, 1)

  pending.resolve()
  await Promise.all([first, duplicate])
  assert.equal(deleteCallbacks, 1)

  await deleteSession()
  assert.equal(serviceCalls, 2)
  assert.equal(deleteCallbacks, 2)
})
