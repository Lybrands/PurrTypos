import assert from 'node:assert/strict'
import test from 'node:test'
import {
  cancelViewportFrame,
  createViewportEditTarget,
  resolveViewportEditTarget,
} from './viewportSession.ts'

test('A editor cannot resolve against B identity or a replaced message at the same index', () => {
  const a = [{ role: 'user' as const, content: 'A', conversationId: 11 }]
  const target = createViewportEditTarget('session:7:1', 0, a[0])

  assert.equal(resolveViewportEditTarget(target, 'session:8:2', [
    { role: 'user', content: 'B', conversationId: 22 },
  ]), undefined)
  assert.equal(resolveViewportEditTarget(target, 'session:7:1', [
    { role: 'user', content: '替换后的 A', conversationId: 33 },
  ]), undefined)
  assert.equal(resolveViewportEditTarget(target, 'session:7:1', a), 0)
})

test('session reset cancels a pending viewport frame exactly once', () => {
  const canceled: number[] = []
  const next = cancelViewportFrame(42, (frame) => canceled.push(frame))

  assert.equal(next, null)
  assert.deepEqual(canceled, [42])
  cancelViewportFrame(next, (frame) => canceled.push(frame))
  assert.deepEqual(canceled, [42])
})
