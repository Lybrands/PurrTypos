import assert from 'node:assert/strict'
import test from 'node:test'
import { getAgentConversationCapabilities } from './conversationCapabilities.ts'

test('a running Agent keeps input and session navigation available', () => {
  assert.deepEqual(getAgentConversationCapabilities({
    running: true,
    readOnly: false,
  }), {
    inputDisabled: false,
    submitMode: 'queue',
    sessionlessSend: true,
  })
})

test('only domain read-only state disables typing', () => {
  assert.deepEqual(getAgentConversationCapabilities({
    running: false,
    readOnly: true,
  }), {
    inputDisabled: true,
    submitMode: 'send',
    sessionlessSend: true,
  })
})

test('sessionless send is enabled by default and only an explicit false turns it off', () => {
  assert.equal(getAgentConversationCapabilities({
    running: false,
    readOnly: false,
    sessionlessSend: true,
  }).sessionlessSend, true)
  assert.equal(getAgentConversationCapabilities({
    running: false,
    readOnly: false,
    sessionlessSend: false,
  }).sessionlessSend, false)
})
