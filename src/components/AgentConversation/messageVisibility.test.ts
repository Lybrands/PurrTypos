import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assistantMessageMetadataVisible,
  assistantMessageVisible,
  latestAssistantMessageIndex,
} from './messageVisibility.ts'

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

test('the latest assistant index ignores later user messages', () => {
  assert.equal(latestAssistantMessageIndex([
    { role: 'user' },
    { role: 'assistant' },
    { role: 'user' },
  ]), 1)
  assert.equal(latestAssistantMessageIndex([
    { role: 'user' },
  ]), -1)
})

test('only the active last assistant hides its metadata', () => {
  assert.equal(assistantMessageMetadataVisible({
    isLast: true,
    loading: true,
  }), false)
  assert.equal(assistantMessageMetadataVisible({
    isLast: false,
    loading: true,
  }), true)
  assert.equal(assistantMessageMetadataVisible({
    isLast: true,
    loading: false,
  }), true)
})
