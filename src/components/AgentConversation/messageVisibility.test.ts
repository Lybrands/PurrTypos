import assert from 'node:assert/strict'
import test from 'node:test'

import { assistantMessageVisible } from './messageVisibility.ts'

test('a persisted artifact keeps an otherwise empty Assistant turn visible', () => {
  assert.equal(assistantMessageVisible({
    hasVisibleContent: false,
    hasAttachment: true,
    isLast: false,
    loading: false,
  }), true)
  assert.equal(assistantMessageVisible({
    hasVisibleContent: false,
    hasAttachment: false,
    isLast: false,
    loading: false,
  }), false)
})

test('the active empty Assistant turn remains visible while loading', () => {
  assert.equal(assistantMessageVisible({
    hasVisibleContent: false,
    hasAttachment: false,
    isLast: true,
    loading: true,
  }), true)
})

test('a failure or cancellation status stays visible without formal content', () => {
  assert.equal(assistantMessageVisible({
    hasVisibleContent: false,
    hasAttachment: false,
    hasStatus: true,
    isLast: false,
    loading: false,
  }), true)
})
