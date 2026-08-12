import assert from 'node:assert/strict'
import test from 'node:test'

import {
  advanceLiveTurnCursor,
  createScrollFollowState,
  detachScrollFollow,
  observeScrollBottom,
} from './scrollFollowPolicy.ts'

test('bottom callback cannot cancel an upward scroll before movement starts', () => {
  const detached = detachScrollFollow(createScrollFollowState(), true)

  assert.deepEqual(observeScrollBottom(detached, true), {
    userDetached: true,
    leftBottomAfterDetach: false,
  })
})

test('following resumes only after leaving and returning to the bottom', () => {
  const detached = detachScrollFollow(createScrollFollowState(), true)
  const leftBottom = observeScrollBottom(detached, false)

  assert.deepEqual(leftBottom, {
    userDetached: true,
    leftBottomAfterDetach: true,
  })
  assert.deepEqual(
    observeScrollBottom(leftBottom, true),
    createScrollFollowState(),
  )
})

test('scroll intent raised away from the bottom can resume on return', () => {
  const detached = detachScrollFollow(createScrollFollowState(), false)

  assert.deepEqual(
    observeScrollBottom(detached, true),
    createScrollFollowState(),
  )
})

const user = (content: string) => ({ role: 'user' as const, content })
const assistant = (clientTurnId?: string, content = '') => ({
  role: 'assistant' as const,
  content,
  clientTurnId,
})

test('initial live turn is recorded without forcing a pin', () => {
  const observed = advanceLiveTurnCursor(undefined, [
    user('第一问'),
    assistant('turn-1'),
  ])

  assert.deepEqual(observed.cursor, { key: 'client:turn-1' })
  assert.equal(observed.anchorIndex, undefined)
})

test('stream updates in the same live turn do not pin again', () => {
  const observed = advanceLiveTurnCursor(
    { key: 'client:turn-1' },
    [user('第一问'), assistant('turn-1', '正在回答')],
  )

  assert.deepEqual(observed.cursor, { key: 'client:turn-1' })
  assert.equal(observed.anchorIndex, undefined)
})

test('a new client turn pins its preceding user message', () => {
  const observed = advanceLiveTurnCursor(
    { key: 'client:turn-1' },
    [
      user('第一问'),
      assistant('turn-1', '已完成'),
      user('第二问'),
      assistant('turn-2'),
    ],
  )

  assert.deepEqual(observed.cursor, { key: 'client:turn-2' })
  assert.equal(observed.anchorIndex, 2)
})

test('stable persisted identity is used when the live client key is absent', () => {
  const observed = advanceLiveTurnCursor(
    { key: 'run:run-1' },
    [
      user('继续'),
      {
        role: 'assistant',
        content: '',
        conversationId: 42,
        agentRunId: 'run-2',
      },
    ],
  )

  assert.deepEqual(observed.cursor, { key: 'conversation:42' })
  assert.equal(observed.anchorIndex, 0)
})

test('changing run ids cannot repin the same stable user turn', () => {
  const messages = [
    {
      role: 'user' as const,
      content: '生成剧本',
      sentAt: '2026-08-12T12:00:00.000Z',
    },
    {
      role: 'assistant' as const,
      content: '正在生成',
      agentRunId: 'unit-run-2',
    },
  ]

  const initial = advanceLiveTurnCursor(undefined, messages)
  const streamed = advanceLiveTurnCursor(initial.cursor, [
    messages[0],
    { ...messages[1], content: '继续生成', agentRunId: 'unit-run-3' },
  ])

  assert.deepEqual(initial.cursor, {
    key: 'user-sent:2026-08-12T12:00:00.000Z',
  })
  assert.equal(streamed.anchorIndex, undefined)
  assert.deepEqual(streamed.cursor, initial.cursor)
})

test('editing an earlier turn pins the replacement user message', () => {
  const observed = advanceLiveTurnCursor(
    { key: 'client:turn-2' },
    [user('改写后的第一问'), assistant('turn-edit')],
  )

  assert.deepEqual(observed.cursor, { key: 'client:turn-edit' })
  assert.equal(observed.anchorIndex, 0)
})

test('an assistant without stable live identity never triggers a pin', () => {
  const observed = advanceLiveTurnCursor(
    { key: 'client:turn-1' },
    [user('无标识问题'), assistant()],
  )

  assert.deepEqual(observed.cursor, {})
  assert.equal(observed.anchorIndex, undefined)
})
