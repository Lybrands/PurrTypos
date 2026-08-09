import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayConversationEvent,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
} from '../types'
import {
  isScreenplayTurnTerminal,
  stateFromScreenplayConversationSnapshot,
} from './conversationState.ts'
import { ScreenplayConversationClient } from './conversationClient.ts'

function turn(overrides: Partial<ScreenplayConversationTurn> = {}): ScreenplayConversationTurn {
  return {
    id: 'turn-1',
    projectId: 'project-1',
    sessionId: 7,
    commandId: 'command-1',
    route: 'operation',
    status: 'running',
    attempt: 1,
    userContent: '生成场景表',
    assistantContent: '正在生成',
    runtimeProfile: { model: 'glm-5.2' },
    operationId: 'operation-1',
    runId: 'run-1',
    revisionId: null,
    error: null,
    retryable: false,
    createdAt: '2026-08-09T00:00:00Z',
    updatedAt: '2026-08-09T00:00:01Z',
    ...overrides,
  }
}

test('native screenplay conversation rebuilds UI messages from the snapshot', () => {
    const snapshot: ScreenplayConversationSnapshot = {
      projectId: 'project-1',
      sessionId: 7,
      cursor: 12,
      turns: [turn()],
    }

    const state = stateFromScreenplayConversationSnapshot(snapshot)

  assert.equal(state.cursor, 12)
  assert.deepEqual(state.messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    operationId: message.operationId,
    runId: message.runId,
  })), [
    {
      id: 'turn-1:user',
      role: 'user',
      content: '生成场景表',
      operationId: 'operation-1',
      runId: 'run-1',
    },
    {
      id: 'turn-1:assistant',
      role: 'assistant',
      content: '正在生成',
      operationId: 'operation-1',
      runId: 'run-1',
    },
  ])
})

test('native screenplay terminal states include cancellation and failure', () => {
  assert.equal(isScreenplayTurnTerminal(turn({ status: 'completed' })), true)
  assert.equal(isScreenplayTurnTerminal(turn({ status: 'failed' })), true)
  assert.equal(isScreenplayTurnTerminal(turn({ status: 'canceled' })), true)
  assert.equal(isScreenplayTurnTerminal(turn({ status: 'running' })), false)
})

test('native screenplay client refreshes from API snapshot after cursor events', async () => {
  let snapshotReads = 0
  const currentTurn = turn({ status: 'completed', assistantContent: '完成' })
  const client = new ScreenplayConversationClient({
    submitScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
    getScreenplayConversationSnapshot: async () => {
      snapshotReads += 1
      return {
        success: true,
        data: {
          projectId: 'project-1',
          sessionId: 7,
          cursor: 13,
          turns: [currentTurn],
        },
      }
    },
    listScreenplayConversationEvents: async () => ({
      success: true,
      data: {
        events: [{
          cursor: 13,
          turnId: 'turn-1',
          sequence: 4,
          type: 'screenplay.conversation.turn_completed',
          payload: {},
        }],
        nextCursor: 13,
        hasMore: false,
      },
    }),
    watchScreenplayConversationEvents: () => () => undefined,
    cancelScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
    resumeScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
  })
  const initial = stateFromScreenplayConversationSnapshot({
    projectId: 'project-1',
    sessionId: 7,
    cursor: 12,
    turns: [turn()],
  })

  const refreshed = await client.refresh(initial)

  assert.equal(snapshotReads, 1)
  assert.equal(refreshed.cursor, 13)
  assert.equal(refreshed.messages[1].content, '完成')
})

test('native screenplay SSE notices invalidate only on forward cursors', () => {
  let onEvent: ((event: ScreenplayConversationEvent) => void) | undefined
  let closed = false
  const currentTurn = turn()
  const client = new ScreenplayConversationClient({
    submitScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
    getScreenplayConversationSnapshot: async () => ({
      success: true,
      data: { projectId: 'project-1', sessionId: 7, cursor: 12, turns: [currentTurn] },
    }),
    listScreenplayConversationEvents: async () => ({
      success: true,
      data: { events: [], nextCursor: 12, hasMore: false },
    }),
    watchScreenplayConversationEvents: (input) => {
      onEvent = input.onEvent
      return () => { closed = true }
    },
    cancelScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
    resumeScreenplayConversationTurn: async () => ({ success: true, data: currentTurn }),
  })
  const state = stateFromScreenplayConversationSnapshot({
    projectId: 'project-1',
    sessionId: 7,
    cursor: 12,
    turns: [currentTurn],
  })
  let invalidations = 0
  const stop = client.watch(state, () => { invalidations += 1 })

  onEvent?.({ cursor: 12, turnId: 'turn-1', sequence: 1, type: 'old', payload: {} })
  onEvent?.({ cursor: 13, turnId: 'turn-1', sequence: 2, type: 'updated', payload: {} })
  onEvent?.({ cursor: 13, turnId: 'turn-1', sequence: 2, type: 'duplicate', payload: {} })
  onEvent?.({ cursor: 14, turnId: 'turn-1', sequence: 3, type: 'updated', payload: {} })
  stop()

  assert.equal(invalidations, 2)
  assert.equal(closed, true)
})
