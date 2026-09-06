import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createScreenplayConversationSessionLifecycle,
} from './screenplayConversationSessionLifecycle.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

test('deferred A hydration and actions cannot cross the B load epoch', async () => {
  const lifecycle = createScreenplayConversationSessionLifecycle()
  const aLoad = deferred<string>()
  const a = lifecycle.beginLoad('project-1', 1)
  lifecycle.setDraft(a, 'A 草稿')
  const lateA = aLoad.promise.then(() => lifecycle.finishLoad(a))

  const b = lifecycle.beginLoad('project-1', 2)
  lifecycle.setDraft(b, 'B 草稿')
  assert.equal(lifecycle.canAct(b), false)
  assert.equal(lifecycle.canAct(a), false)

  assert.equal(lifecycle.finishLoad(b), true)
  assert.equal(lifecycle.canAct(b), true)
  assert.equal(lifecycle.getDraft(b), 'B 草稿')

  aLoad.resolve('late A')
  assert.equal(await lateA, false)
  assert.equal(lifecycle.currentIdentity(), b.identity)
  assert.equal(lifecycle.getDraft(b), 'B 草稿')
})

test('action captured before edit truncation cannot submit after session switch', async () => {
  const lifecycle = createScreenplayConversationSessionLifecycle()
  const truncate = deferred<void>()
  const calls: string[] = []
  const a = lifecycle.beginLoad('project-1', 1)
  lifecycle.finishLoad(a)

  const edit = (async () => {
    if (!lifecycle.canAct(a)) return
    calls.push('truncate:A')
    await truncate.promise
    if (lifecycle.canAct(a)) calls.push('submit:A')
  })()

  const b = lifecycle.beginLoad('project-1', 2)
  lifecycle.finishLoad(b)
  truncate.resolve()
  await edit

  assert.deepEqual(calls, ['truncate:A'])
})
