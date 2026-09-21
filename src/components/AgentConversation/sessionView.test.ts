import assert from 'node:assert/strict'
import test from 'node:test'
import {
  computeSessionReorder,
  sortConversationSessionsForDisplay,
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
      pinned: false,
      sortOrder: null,
    },
  )
})

test('session mapping carries pin state and manual sort order', () => {
  const pinnedRow = {
    id: 3,
    title: '全局对话',
    pinned: 1,
    sort_order: 2,
  }
  assert.deepEqual(
    toAgentConversationSession(pinnedRow),
    {
      id: 3,
      title: '全局对话',
      createdAt: undefined,
      pinned: true,
      sortOrder: 2,
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

test('display order puts pinned group first and keeps manual order within groups', () => {
  const sessions = [
    { id: 'a', title: 'A', createdAt: '2026-09-10 08:00:00', pinned: false, sortOrder: 0 },
    { id: 'b', title: 'B', createdAt: '2026-09-11 08:00:00', pinned: false, sortOrder: 1 },
    { id: 'c', title: 'C', createdAt: '2026-09-12 08:00:00', pinned: true, sortOrder: 3 },
    { id: 'd', title: 'D', createdAt: '2026-09-13 08:00:00', pinned: true, sortOrder: 2 },
    // 未参与手动排序（新会话）：按时间倒序排在各自分组前部
    { id: 'e', title: 'E', createdAt: '2026-09-14 08:00:00', pinned: false, sortOrder: null },
    { id: 'f', title: 'F', createdAt: '2026-09-15 08:00:00', pinned: true, sortOrder: null },
  ]

  assert.deepEqual(
    sortConversationSessionsForDisplay(sessions).map(item => item.id),
    ['f', 'd', 'c', 'e', 'a', 'b'],
  )
})

test('display order without manual ranks degrades to newest first', () => {
  const sessions = [
    { id: 'a', title: 'A', createdAt: '2026-09-10 08:00:00', pinned: false, sortOrder: null },
    { id: 'b', title: 'B', createdAt: '2026-09-12 08:00:00', pinned: false, sortOrder: null },
    { id: 'c', title: 'C', createdAt: '2026-09-11 08:00:00', pinned: true, sortOrder: null },
  ]

  assert.deepEqual(
    sortConversationSessionsForDisplay(sessions).map(item => item.id),
    ['c', 'b', 'a'],
  )
})

test('drag reorder moves a session within its pin group', () => {
  const sessions = [
    { id: 'p1', title: '置顶1', pinned: true, sortOrder: 0 },
    { id: 'p2', title: '置顶2', pinned: true, sortOrder: 1 },
    { id: 'u1', title: '普通1', pinned: false, sortOrder: 2 },
    { id: 'u2', title: '普通2', pinned: false, sortOrder: 3 },
    { id: 'u3', title: '普通3', pinned: false, sortOrder: 4 },
  ]

  // 把 u3 拖到 u1 上半部 → u3 插到普通组首位
  assert.deepEqual(
    computeSessionReorder(sessions, 'u3', { id: 'u1', before: true }),
    ['p1', 'p2', 'u3', 'u1', 'u2'],
  )
  // 把 u1 拖到 u3 下半部 → u1 移到普通组末尾
  assert.deepEqual(
    computeSessionReorder(sessions, 'u1', { id: 'u3', before: false }),
    ['p1', 'p2', 'u2', 'u3', 'u1'],
  )
  // 把 u2 拖到 u3 上半部 → 位置不变
  assert.equal(
    computeSessionReorder(sessions, 'u2', { id: 'u3', before: true }),
    null,
  )
  // 把 p2 拖到普通组目标 → 拒绝跨分组
  assert.equal(
    computeSessionReorder(sessions, 'p2', { id: 'u2', before: true }),
    null,
  )
  // 把 u2 拖到 p2 下半部（置顶组末尾之后）→ 拒绝跨分组
  assert.equal(
    computeSessionReorder(sessions, 'u2', { id: 'p2', before: false }),
    null,
  )
})
