import assert from 'node:assert/strict'
import test from 'node:test'
import {
  sortConversationSessionsNewestFirst,
  toAgentConversationSession,
} from './sessionView.ts'

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

test('conversation sessions are ordered newest first regardless of input order', () => {
  const older = { id: 'older', title: '旧对话', createdAt: '2026-09-13 08:00:00' }
  const newer = { id: 'newer', title: '新对话', createdAt: '2026-09-15 08:00:00' }
  assert.deepEqual(
    sortConversationSessionsNewestFirst([newer, older]).map(item => item.id),
    ['newer', 'older'],
  )
  assert.deepEqual(
    sortConversationSessionsNewestFirst([older, newer]).map(item => item.id),
    ['newer', 'older'],
  )
})
