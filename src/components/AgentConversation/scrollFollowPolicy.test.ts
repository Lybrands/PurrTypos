import assert from 'node:assert/strict'
import test from 'node:test'

import {
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
