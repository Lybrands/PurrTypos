import assert from 'node:assert/strict'
import test from 'node:test'
import { toAgentConversationSession } from './sessionView.ts'

test('product session timestamps are explicitly normalized to createdAt', () => {
  const legacySession = {
    id: 7,
    title: '旧对话',
    create_time: '2026-08-12 09:30:00',
  }

  assert.deepEqual(
    toAgentConversationSession(legacySession, legacySession.create_time),
    {
      id: 7,
      title: '旧对话',
      createdAt: '2026-08-12 09:30:00',
    },
  )
})
