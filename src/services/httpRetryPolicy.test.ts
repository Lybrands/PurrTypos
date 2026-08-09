import assert from 'node:assert/strict'
import test from 'node:test'

import { canRetryHttpRequest } from './httpRetryPolicy.ts'

test('only safe requests retry transient failures', () => {
  assert.equal(canRetryHttpRequest('GET'), true)
  assert.equal(canRetryHttpRequest('HEAD', 503), true)
  assert.equal(canRetryHttpRequest('GET', 429), true)
  assert.equal(canRetryHttpRequest('GET', 409), false)
  assert.equal(canRetryHttpRequest('POST'), false)
  assert.equal(canRetryHttpRequest('POST', 503), false)
})
