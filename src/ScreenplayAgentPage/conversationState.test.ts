import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
} from '../types'
import {
  conversationPageRequiresSnapshot,
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

test('native screenplay cursor events only invalidate the canonical snapshot', () => {
    const state = stateFromScreenplayConversationSnapshot({
      projectId: 'project-1',
      sessionId: 7,
      cursor: 12,
      turns: [turn()],
    })

  assert.equal(conversationPageRequiresSnapshot(state, {
      events: [
        { cursor: 13, turnId: 'turn-1', sequence: 4, type: 'chunk', payload: {} },
        { cursor: 14, turnId: 'turn-1', sequence: 5, type: 'completed', payload: {} },
      ],
      nextCursor: 14,
      hasMore: false,
  }), true)
  assert.equal(conversationPageRequiresSnapshot(state, {
      events: [
        { cursor: 14, turnId: 'turn-1', sequence: 5, type: 'completed', payload: {} },
      ],
      nextCursor: 14,
      hasMore: false,
  }), true)
  assert.equal(conversationPageRequiresSnapshot(state, {
      events: [
        { cursor: 12, turnId: 'turn-1', sequence: 3, type: 'started', payload: {} },
      ],
      nextCursor: 12,
      hasMore: false,
  }), false)
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
