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
  coordinator.activate()
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

test('deactivate prevents pending requests from mutating component state', () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const request = coordinator.beginLatest()

  coordinator.deactivate()

  assert.equal(coordinator.isMounted(), false)
  assert.equal(coordinator.isCurrent(request), false)
})

test('duplicate deletion runs the service and callback only once per session', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
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

test('StrictMode cleanup invalidates old work and a second setup accepts new work', async () => {
  const coordinator = createHistoryRequestCoordinator()
  const beforeFirstSetup = coordinator.captureLatest()
  coordinator.activate()
  assert.equal(coordinator.captureLatest(), beforeFirstSetup + 1)
  const oldRequest = coordinator.beginLatest()
  const oldPending = deferred<void>()
  let oldDeleteCallbacks = 0
  const oldDelete = coordinator.runOnce('session:old', async () => {
    await oldPending.promise
    if (coordinator.isCurrent(oldRequest)) oldDeleteCallbacks += 1
  })

  const beforeCleanup = coordinator.captureLatest()
  coordinator.deactivate()
  assert.equal(coordinator.captureLatest(), beforeCleanup + 1)
  assert.equal(coordinator.isCurrent(oldRequest), false)

  const beforeSecondSetup = coordinator.captureLatest()
  coordinator.activate()
  assert.equal(coordinator.captureLatest(), beforeSecondSetup + 1)
  assert.equal(coordinator.isCurrent(oldRequest), false)
  const newRequest = coordinator.beginLatest()
  let newDeleteCallbacks = 0
  await coordinator.runOnce('session:new', async () => {
    if (coordinator.isCurrent(newRequest)) newDeleteCallbacks += 1
  })

  assert.equal(coordinator.isCurrent(newRequest), true)
  assert.equal(newDeleteCallbacks, 1)
  oldPending.resolve()
  await oldDelete
  assert.equal(oldDeleteCallbacks, 0)
})

test('deactivate isolates same-key in-flight work from the next lifecycle', async () => {
  const coordinator = createHistoryRequestCoordinator()
  coordinator.activate()
  const oldRequest = coordinator.beginLatest()
  const oldPending = deferred<void>()
  let operationCalls = 0
  let oldDeleteCallbacks = 0
  const oldDelete = coordinator.runOnce('session:X', async () => {
    operationCalls += 1
    await oldPending.promise
    if (coordinator.isCurrent(oldRequest)) oldDeleteCallbacks += 1
  })

  coordinator.deactivate()
  coordinator.activate()
  const currentRequest = coordinator.beginLatest()
  const currentPending = deferred<void>()
  let currentDeleteCallbacks = 0
  const currentDelete = coordinator.runOnce('session:X', async () => {
    operationCalls += 1
    await currentPending.promise
    if (coordinator.isCurrent(currentRequest)) currentDeleteCallbacks += 1
  })

  assert.notEqual(currentDelete, oldDelete)
  assert.equal(operationCalls, 2)

  oldPending.resolve()
  await oldDelete
  assert.equal(oldDeleteCallbacks, 0)

  const duplicateCurrentDelete = coordinator.runOnce('session:X', async () => {
    operationCalls += 1
  })
  assert.equal(duplicateCurrentDelete, currentDelete)
  assert.equal(operationCalls, 2)

  currentPending.resolve()
  await currentDelete
  assert.equal(currentDeleteCallbacks, 1)

  await coordinator.runOnce('session:X', async () => {
    operationCalls += 1
  })
  assert.equal(operationCalls, 3)
})
