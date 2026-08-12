import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyExecutionLogAutoOpen,
  getInitialExecutionLogOpenState,
  readExecutionLogOpenState,
  toggleExecutionLogOpenState,
  writeExecutionLogOpenState,
} from './state.ts'

test('stale automatic state cannot reopen a later execution batch', () => {
  assert.deepEqual(
    getInitialExecutionLogOpenState({ open: true, manuallySet: false }, false),
    { open: false, manuallySet: false },
  )
  assert.deepEqual(
    applyExecutionLogAutoOpen({ open: false, manuallySet: true }, true),
    { open: false, manuallySet: true },
  )
})

test('manual execution disclosure state survives harmless rerenders', () => {
  const manuallyClosed = toggleExecutionLogOpenState({
    open: true,
    manuallySet: false,
  })
  assert.deepEqual(
    getInitialExecutionLogOpenState(manuallyClosed, true),
    { open: false, manuallySet: true },
  )
  assert.equal(applyExecutionLogAutoOpen(manuallyClosed, true), manuallyClosed)
})

test('automatic disclosure state follows transitions until manually set', () => {
  const initial = getInitialExecutionLogOpenState(undefined, true)
  assert.deepEqual(initial, { open: true, manuallySet: false })
  assert.deepEqual(
    applyExecutionLogAutoOpen(initial, false),
    { open: false, manuallySet: false },
  )
  assert.deepEqual(
    getInitialExecutionLogOpenState(undefined, false),
    { open: false, manuallySet: false },
  )
})

test('recent execution disclosure state stays bounded and retains reads', () => {
  const cache = new Map()
  writeExecutionLogOpenState(
    cache,
    'turn-a',
    { open: true, manuallySet: true },
    2,
  )
  writeExecutionLogOpenState(
    cache,
    'turn-b',
    { open: false, manuallySet: true },
    2,
  )
  assert.deepEqual(
    readExecutionLogOpenState(cache, 'turn-a'),
    { open: true, manuallySet: true },
  )
  writeExecutionLogOpenState(
    cache,
    'turn-c',
    { open: true, manuallySet: false },
    2,
  )
  assert.deepEqual([...cache.keys()], ['turn-a', 'turn-c'])
  assert.equal(readExecutionLogOpenState(cache, 'turn-b'), undefined)
})
