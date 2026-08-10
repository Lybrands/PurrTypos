import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayAgentTask,
  ScreenplayAgentChunkPage,
  ScreenplayConversationEvent,
  ScreenplayConversationStreamEvent,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayV2RevisionSummary,
  ScreenplayV2Workspace,
} from '../types'
import {
  isScreenplayTurnTerminal,
  screenplayTurnArtifacts,
  screenplayTurnReconciliationKey,
  stateFromScreenplayConversationSnapshot,
} from './conversationState.ts'
import { ScreenplayConversationClient } from './conversationClient.ts'

function turn(overrides: Partial<ScreenplayConversationTurn> = {}): ScreenplayConversationTurn {
  return {
    id: 'turn-1',
    projectId: 'project-1',
    sessionId: 7,
    status: 'planning',
    userContent: '创作接下来三集',
    assistantContent: '',
    runtimeProfile: { model: 'glm-5.2' },
    intent: null,
    plannerRunId: 'run-plan-1',
    taskId: null,
    error: null,
    createdAt: '2026-08-09T00:00:00Z',
    updatedAt: '2026-08-09T00:00:01Z',
    ...overrides,
  }
}

function task(overrides: Partial<ScreenplayAgentTask> = {}): ScreenplayAgentTask {
  return {
    id: 'task-1',
    projectId: 'project-1',
    sessionId: 7,
    turnId: 'turn-1',
    status: 'running',
    targetRole: 'screenplayDraft',
    intent: {},
    plannerRunId: 'run-plan-1',
    totalUnits: 2,
    completedUnits: 1,
    resultRevisionId: null,
    resultRevision: null,
    error: null,
    units: [{
      id: 'draft-episode-1',
      position: 0,
      kind: 'generate_episode_draft',
      status: 'completed',
      input: {},
      output: { runId: 'run-draft-1' },
      error: null,
      attempt: 1,
    }],
    ...overrides,
  }
}

function revision(
  id: string,
  role: ScreenplayV2RevisionSummary['role'],
  overrides: Partial<ScreenplayV2RevisionSummary> = {},
): ScreenplayV2RevisionSummary {
  return {
    id,
    deliverableId: `deliverable-${role}`,
    role,
    revisionNo: 1,
    parentRevisionId: null,
    contentDigest: `digest-${id}`,
    summary: {},
    agentTaskId: null,
    status: 'candidate',
    ...overrides,
  }
}

function snapshot(
  currentTurn = turn(),
  currentTask: ScreenplayAgentTask | null = task(),
  cursor = 12,
): ScreenplayConversationSnapshot {
  return {
    projectId: 'project-1',
    sessionId: 7,
    cursor,
    turns: [currentTurn],
    tasks: currentTask ? [currentTask] : [],
  }
}

test('screenplay Agent rebuilds messages from persisted Turn and Task state', () => {
  const state = stateFromScreenplayConversationSnapshot(snapshot())

  assert.equal(state.cursor, 12)
  assert.deepEqual(state.messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    taskId: message.taskId,
    runId: message.runId,
  })), [
    {
      id: 'turn-1:user',
      role: 'user',
      content: '创作接下来三集',
      taskId: 'task-1',
      runId: 'run-draft-1',
    },
    {
      id: 'turn-1:assistant',
      role: 'assistant',
      content: '',
      taskId: 'task-1',
      runId: 'run-draft-1',
    },
  ])
})

test('Turn and Task must both be terminal before reconciliation', () => {
  assert.equal(isScreenplayTurnTerminal(turn()), false)
  assert.equal(isScreenplayTurnTerminal(turn({ status: 'failed' })), true)
  const completedTurn = turn({ status: 'completed', taskId: 'task-1' })
  assert.equal(screenplayTurnReconciliationKey(completedTurn, task()), null)
  assert.equal(
    screenplayTurnReconciliationKey(completedTurn, task({
      status: 'completed',
      resultRevisionId: 'revision-1',
    })),
    'turn-1:completed:revision-1',
  )
})

test('every completed Task result is projected onto its own conversation turn', () => {
  const sceneRevision = revision('revision-scenes', 'sceneList', {
    revisionNo: 3,
    summary: {
      proposalKind: 'scene_list',
      title: '第 1 至 8 集场景规划',
    },
  })
  const draftRevision = revision('revision-draft', 'screenplayDraft', {
    revisionNo: 2,
    summary: {
      proposalKind: 'scene_draft',
      title: '第 1 至 2 集剧本',
    },
  })

  const artifacts = screenplayTurnArtifacts([
    task({
      id: 'task-scenes',
      turnId: 'turn-scenes',
      status: 'completed',
      targetRole: 'sceneList',
      resultRevisionId: sceneRevision.id,
      resultRevision: sceneRevision,
    }),
    task({
      id: 'task-draft',
      turnId: 'turn-draft',
      status: 'completed',
      targetRole: 'screenplayDraft',
      resultRevisionId: draftRevision.id,
      resultRevision: draftRevision,
    }),
  ], null)

  assert.deepEqual([...artifacts.entries()].map(([turnId, artifact]) => ({
    turnId,
    taskId: artifact.taskId,
    revisionId: artifact.revisionId,
    role: artifact.role,
    revisionNo: artifact.revisionNo,
    kind: artifact.kind,
    title: artifact.title,
  })), [
    {
      turnId: 'turn-scenes',
      taskId: 'task-scenes',
      revisionId: 'revision-scenes',
      role: 'sceneList',
      revisionNo: 3,
      kind: 'scene_list',
      title: '第 1 至 8 集场景规划',
    },
    {
      turnId: 'turn-draft',
      taskId: 'task-draft',
      revisionId: 'revision-draft',
      role: 'screenplayDraft',
      revisionNo: 2,
      kind: 'scene_draft',
      title: '第 1 至 2 集剧本',
    },
  ])
})

test('artifact projection keeps exact Revision targets without optional summaries', () => {
  const artifacts = screenplayTurnArtifacts([
    task({
      id: 'task-legacy',
      turnId: 'turn-legacy',
      status: 'completed',
      targetRole: 'review',
      resultRevisionId: 'revision-legacy',
      resultRevision: null,
    }),
    task({
      id: 'task-running',
      turnId: 'turn-running',
      status: 'running',
      resultRevisionId: 'revision-uncommitted',
      resultRevision: null,
    }),
    task({
      id: 'task-answer',
      turnId: 'turn-answer',
      status: 'completed',
      resultRevisionId: null,
      resultRevision: null,
    }),
  ], null)

  assert.deepEqual([...artifacts.values()], [{
    turnId: 'turn-legacy',
    taskId: 'task-legacy',
    revisionId: 'revision-legacy',
    role: 'review',
    revisionNo: null,
    title: '审阅修订候选稿',
    kind: null,
    status: 'candidate',
    sourceRunId: 'run-draft-1',
  }])
})

test('current Workspace truth overrides stale artifact status snapshots', () => {
  const sceneRevision = revision('revision-scenes', 'sceneList', {
    status: 'historical',
    summary: { proposalKind: 'scene_list', title: '场景规划' },
  })
  const draftRevision = revision('revision-draft', 'screenplayDraft', {
    status: 'candidate',
    summary: { proposalKind: 'scene_draft', title: '剧本正文' },
  })
  const workspace = {
    workflow: {
      heads: {
        sceneList: sceneRevision,
      },
    },
    candidates: [draftRevision],
  } as ScreenplayV2Workspace

  const artifacts = screenplayTurnArtifacts([
    task({
      id: 'task-scenes',
      turnId: 'turn-scenes',
      status: 'completed',
      targetRole: 'sceneList',
      resultRevisionId: sceneRevision.id,
      resultRevision: sceneRevision,
    }),
    task({
      id: 'task-draft',
      turnId: 'turn-draft',
      status: 'completed',
      targetRole: 'screenplayDraft',
      resultRevisionId: draftRevision.id,
      resultRevision: draftRevision,
    }),
  ], workspace)

  assert.equal(artifacts.get('turn-scenes')?.status, 'current')
  assert.equal(artifacts.get('turn-draft')?.status, 'candidate')
})

test('paused execution settles streaming without fabricating assistant content', () => {
  const pausedTurn = turn({ status: 'paused', taskId: 'task-1' })
  const pausedTask = task({
    status: 'paused',
    error: { code: 'provider_bad_request', message: '请求能力不兼容' },
    units: [{
      ...task().units[0],
      status: 'blocked',
      error: { code: 'provider_bad_request', message: '请求能力不兼容' },
    }],
  })

  const state = stateFromScreenplayConversationSnapshot(snapshot(
    pausedTurn,
    pausedTask,
  ))

  assert.equal(isScreenplayTurnTerminal(pausedTurn), true)
  assert.equal(state.messages[1].content, '')
  assert.equal(screenplayTurnReconciliationKey(pausedTurn, pausedTask), null)
})

test('screenplay client refreshes canonical snapshot after cursor events', async () => {
  let snapshotReads = 0
  let truncatedTurnId = ''
  const completedTurn = turn({ status: 'completed', taskId: 'task-1' })
  const completedTask = task({
    status: 'completed',
    resultRevisionId: 'revision-1',
  })
  const client = new ScreenplayConversationClient({
    submitScreenplayConversationTurn: async () => ({ success: true, data: completedTurn }),
    getScreenplayConversationSnapshot: async () => {
      snapshotReads += 1
      return { success: true, data: snapshot(completedTurn, completedTask, 13) }
    },
    listScreenplayConversationEvents: async () => ({
      success: true,
      data: {
        events: [{
          cursor: 13,
          turnId: 'turn-1',
          taskId: 'task-1',
          type: 'screenplay.agent.task.completed',
          payload: {},
        }],
        nextCursor: 13,
        hasMore: false,
      },
    }),
    watchScreenplayConversationEvents: () => () => undefined,
    cancelScreenplayConversationTurn: async () => ({ success: true, data: completedTurn }),
    truncateScreenplayConversationFromTurn: async ({ turnId }) => {
      truncatedTurnId = turnId
      return {
        success: true,
        data: {
          projectId: 'project-1',
          sessionId: 7,
          deletedTurnIds: [turnId],
          deletedTaskIds: ['task-1'],
        },
      }
    },
  })
  const refreshed = await client.refresh(
    stateFromScreenplayConversationSnapshot(snapshot()),
  )
  await client.truncateFromTurn('turn-1')

  assert.equal(snapshotReads, 1)
  assert.equal(refreshed.cursor, 13)
  assert.equal(
    refreshed.messages[1].content,
    '',
  )
  assert.equal(truncatedTurnId, 'turn-1')
})

test('screenplay SSE routes business invalidation and shared Agent chunks independently', () => {
  let onEvent: ((event: ScreenplayConversationStreamEvent) => void) | undefined
  let closed = false
  const client = new ScreenplayConversationClient({
    submitScreenplayConversationTurn: async () => ({ success: true, data: turn() }),
    getScreenplayConversationSnapshot: async () => ({ success: true, data: snapshot() }),
    listScreenplayConversationEvents: async () => ({
      success: true,
      data: { events: [], nextCursor: 12, hasMore: false },
    }),
    watchScreenplayConversationEvents: (input) => {
      onEvent = input.onEvent
      return () => { closed = true }
    },
    cancelScreenplayConversationTurn: async () => ({ success: true, data: turn() }),
    truncateScreenplayConversationFromTurn: async () => ({
      success: true,
      data: {
        projectId: 'project-1',
        sessionId: 7,
        deletedTurnIds: ['turn-1'],
        deletedTaskIds: ['task-1'],
      },
    }),
  })
  const state = stateFromScreenplayConversationSnapshot(snapshot())
  let invalidations = 0
  const chunkPages: ScreenplayAgentChunkPage[] = []
  const stop = client.watch(state, {
    chunkAfter: 20,
    onInvalidate: () => { invalidations += 1 },
    onChunks: (page) => { chunkPages.push(page) },
  })
  const event = (cursor: number): ScreenplayConversationEvent => ({
    cursor,
    turnId: 'turn-1',
    taskId: 'task-1',
    type: 'updated',
    payload: {},
  })

  onEvent?.(event(12))
  onEvent?.(event(13))
  onEvent?.(event(13))
  onEvent?.(event(14))
  const chunkPage: ScreenplayAgentChunkPage = {
    kind: 'agent_chunks',
    chunks: [],
    nextCursor: 21,
    hasMore: false,
  }
  onEvent?.(chunkPage)
  onEvent?.(chunkPage)
  stop()

  assert.equal(invalidations, 2)
  assert.equal(chunkPages.length, 1)
  assert.equal(closed, true)
})
