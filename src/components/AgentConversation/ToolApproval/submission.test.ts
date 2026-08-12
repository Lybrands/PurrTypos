import assert from 'node:assert/strict'
import test from 'node:test'
import { submitToolApprovalDecision } from './submission.ts'

test('successful approval decisions resolve to their terminal state', async () => {
  const approveGuard = { pending: false }
  const rejectGuard = { pending: false }

  assert.deepEqual(
    await submitToolApprovalDecision(
      approveGuard,
      async () => ({ success: true }),
      'approval-1',
      true,
    ),
    { state: 'approved' },
  )
  assert.deepEqual(
    await submitToolApprovalDecision(
      rejectGuard,
      async () => ({ success: true }),
      'approval-2',
      false,
    ),
    { state: 'rejected' },
  )
})

test('unsuccessful approval decisions return explicit error feedback', async () => {
  assert.deepEqual(
    await submitToolApprovalDecision(
      { pending: false },
      async () => ({ success: false, error: '确认请求已过期。' }),
      'approval-1',
      true,
    ),
    { state: 'error', error: '确认请求已过期。' },
  )
})

test('rejected promises become retryable error results', async () => {
  const guard = { pending: false }
  let attempts = 0
  const onResolve = async () => {
    attempts += 1
    if (attempts === 1) throw new Error('network unavailable')
    return { success: true }
  }

  assert.deepEqual(
    await submitToolApprovalDecision(guard, onResolve, 'approval-1', true),
    { state: 'error', error: '确认请求已过期或处理失败。' },
  )
  assert.deepEqual(
    await submitToolApprovalDecision(guard, onResolve, 'approval-1', true),
    { state: 'approved' },
  )
  assert.equal(attempts, 2)
})

test('concurrent duplicate decisions invoke the callback only once', async () => {
  const guard = { pending: false }
  let calls = 0
  let finish!: (result: { success: boolean }) => void
  const pendingResult = new Promise<{ success: boolean }>((resolve) => {
    finish = resolve
  })
  const onResolve = async () => {
    calls += 1
    return pendingResult
  }

  const first = submitToolApprovalDecision(
    guard,
    onResolve,
    'approval-1',
    true,
  )
  const duplicateResult = submitToolApprovalDecision(
    guard,
    onResolve,
    'approval-1',
    true,
  )

  await Promise.resolve()
  const concurrentCalls = calls
  finish({ success: true })
  assert.equal(await duplicateResult, null)
  assert.equal(concurrentCalls, 1)
  assert.deepEqual(await first, { state: 'approved' })
})
