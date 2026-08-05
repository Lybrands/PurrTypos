import assert from 'node:assert/strict'
import test from 'node:test'
import { getAgentConversationCapabilities } from './conversationCapabilities.ts'

test('a running Agent keeps input and session navigation available', () => {
  assert.deepEqual(getAgentConversationCapabilities({
    running: true,
    readOnly: false,
    sessionLoading: false,
  }), {
    inputDisabled: false,
    sessionNavigationDisabled: false,
    submitMode: 'queue',
  })
})

test('only domain read-only state disables typing', () => {
  assert.deepEqual(getAgentConversationCapabilities({
    running: false,
    readOnly: true,
    sessionLoading: true,
  }), {
    inputDisabled: true,
    sessionNavigationDisabled: true,
    submitMode: 'send',
  })
})
