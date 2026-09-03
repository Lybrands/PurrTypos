import assert from 'node:assert/strict'
import test from 'node:test'
import type { AiWritingChatRequestReceipt } from '../types.ts'
import {
  replayWritingChatPostUntilObserved,
  reserveWritingChatRequest,
} from './writingChatRequestReceipt.ts'

const accepted = (requestId: string): AiWritingChatRequestReceipt => ({
  requestId,
  sessionId: 7,
  status: 'accepted',
  runId: null,
  cancelRequested: false,
  rejectionCode: null,
  revision: 1,
})

test('reserve replays the same request identity after the response is lost', async () => {
  const attempts: string[] = []
  const result = await reserveWritingChatRequest({
    requestId: 'request-1',
    send: async (requestId) => {
      attempts.push(requestId)
      if (attempts.length === 1) throw new TypeError('network lost')
      return { kind: 'accepted', receipt: accepted(requestId) }
    },
    wait: async () => undefined,
  })

  assert.equal(result.kind, 'accepted')
  assert.deepEqual(attempts, ['request-1', 'request-1'])
})

test('reserve does not retry an explicit validation rejection', async () => {
  let attempts = 0
  const result = await reserveWritingChatRequest({
    requestId: 'request-invalid',
    send: async () => {
      attempts += 1
      return { kind: 'rejected', error: '无效的模型参数', status: 400 }
    },
    wait: async () => undefined,
  })

  assert.deepEqual(result, {
    kind: 'rejected',
    error: '无效的模型参数',
    status: 400,
  })
  assert.equal(attempts, 1)
})

test('reserve retry loop stops when its owning stream is aborted', async () => {
  let aborted = false
  let attempts = 0
  await assert.rejects(
    reserveWritingChatRequest({
      requestId: 'request-abort',
      send: async () => {
        attempts += 1
        throw new TypeError('offline')
      },
      wait: async () => { aborted = true },
      isAborted: () => aborted,
    }),
    { name: 'AbortError' },
  )
  assert.equal(attempts, 1)
})

test('an accepted request replays POST when the response is lost before headers', async () => {
  let attempts = 0
  const response = { ok: true }
  const observed = await replayWritingChatPostUntilObserved({
    send: async () => {
      attempts += 1
      if (attempts === 1) throw new TypeError('connection reset')
      return response
    },
    wait: async () => undefined,
    isAborted: () => false,
  })

  assert.equal(observed, response)
  assert.equal(attempts, 2)
})

test('an accepted request replays POST after a transient server response', async () => {
  let attempts = 0
  const response = await replayWritingChatPostUntilObserved({
    send: async () => {
      attempts += 1
      return attempts === 1
        ? { ok: false, status: 503 }
        : { ok: true, status: 200 }
    },
    accept: (candidate) => candidate.ok || candidate.status < 500,
    wait: async () => undefined,
    isAborted: () => false,
  })

  assert.equal(response.status, 200)
  assert.equal(attempts, 2)
})
