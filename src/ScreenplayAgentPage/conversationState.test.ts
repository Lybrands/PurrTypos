import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayAgentTask,
  ScreenplayAgentChunkPage,
  ScreenplayConversationStreamEvent,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayOperationProjection,
  ScreenplayV2RevisionSummary,
  ScreenplayV2Workspace,
} from '../types'
import {
  isScreenplayTurnTerminal,
  screenplayTurnArtifacts,
  screenplayTurnReconciliationKey,
  isScreenplayOperationCancellable,
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
    stageCommand: null,
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
    usage: {
      invocationCount: 1,
      inputTokens: 100,
      outputTokens: 20,
      reasoningTokens: 5,
    },
    resultRevisionId: null,
    resultRevision: null,
    error: null,
    units: [{
      id: 'draft-episode-1',
      semanticKey: 'episode:1',
      position: 0,
      kind: 'generate_episode_draft',
      status: 'completed',
      input: {},
      outputRef: 'screenplay-part-artifact://draft-1',
      artifactDigest: 'digest-draft-1',
      validationReceipt: { runId: 'run-draft-1' },
      error: null,
      attempt: 1,
    }],
    ...overrides,
  }
}

function operation(
  overrides: Partial<ScreenplayOperationProjection> = {},
): ScreenplayOperationProjection {
  return {
    id: 'operation-1',
    turnId: 'turn-1',
    taskId: 'task-1',
    status: 'running',
    revision: 1,
    targetRole: 'screenplayDraft',
    parts: task().units,
    resultRevisionId: null,
    finalizationReceiptId: null,
    cancelReceiptId: null,
    cancelRequestedAt: null,
    error: null,
    usage: {
      invocationCount: 1,
      inputTokens: 100,
      outputTokens: 20,
      reasoningTokens: 5,
    },
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
  currentOperation: ScreenplayOperationProjection | null = currentTask
    ? operation({
      turnId: currentTurn.id,
      taskId: currentTask.id,
      status: currentTask.status === 'completed'
        ? 'succeeded'
        : currentTask.status === 'pending'
          ? 'queued'
        : currentTask.status,
      resultRevisionId: currentTask.resultRevisionId,
      finalizationReceiptId: currentTask.status === 'completed'
        && currentTask.resultRevisionId
        ? 'finalization-1'
        : null,
      parts: currentTask.units,
      error: currentTask.error,
    })
    : null,
): ScreenplayConversationSnapshot {
  return {
    projectId: 'project-1',
    sessionId: 7,
    cursor,
    turns: [currentTurn],
    tasks: currentTask ? [currentTask] : [],
    operations: currentOperation ? [currentOperation] : [],
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
  assert.equal(screenplayTurnReconciliationKey(
    completedTurn,
    operation(),
    task(),
  ), null)
  assert.equal(
    screenplayTurnReconciliationKey(
      completedTurn,
      operation({
        status: 'succeeded',
        resultRevisionId: 'revision-1',
        finalizationReceiptId: 'finalization-1',
      }),
      task({
        status: 'completed',
        resultRevisionId: 'revision-1',
      }),
    ),
    'turn-1:succeeded:revision-1:finalization-1',
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
    operation({
      id: 'operation-scenes',
      turnId: 'turn-scenes',
      taskId: 'task-scenes',
      status: 'succeeded',
      targetRole: 'sceneList',
      resultRevisionId: sceneRevision.id,
      finalizationReceiptId: 'finalization-scenes',
    }),
    operation({
      id: 'operation-draft',
      turnId: 'turn-draft',
      taskId: 'task-draft',
      status: 'succeeded',
      resultRevisionId: draftRevision.id,
      finalizationReceiptId: 'finalization-draft',
    }),
  ], [
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
    operation({
      id: 'operation-legacy',
      turnId: 'turn-legacy',
      taskId: 'task-legacy',
      status: 'succeeded',
      targetRole: 'review',
      resultRevisionId: 'revision-legacy',
      finalizationReceiptId: 'finalization-legacy',
    }),
    operation({
      id: 'operation-running',
      turnId: 'turn-running',
      taskId: 'task-running',
      status: 'running',
      resultRevisionId: 'revision-uncommitted',
      finalizationReceiptId: null,
    }),
  ], [
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
    operation({
      id: 'operation-scenes',
      turnId: 'turn-scenes',
      taskId: 'task-scenes',
      status: 'succeeded',
      targetRole: 'sceneList',
      resultRevisionId: sceneRevision.id,
      finalizationReceiptId: 'finalization-scenes',
    }),
    operation({
      id: 'operation-draft',
      turnId: 'turn-draft',
      taskId: 'task-draft',
      status: 'succeeded',
      resultRevisionId: draftRevision.id,
      finalizationReceiptId: 'finalization-draft',
    }),
  ], [
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
  assert.equal(screenplayTurnReconciliationKey(
    pausedTurn,
    operation({ status: 'paused' }),
    pausedTask,
  ), null)
})

test('failed and canceled Operations never become formal Assistant content', () => {
  for (const status of ['failed', 'canceled'] as const) {
    const currentTurn = turn({
      status,
      taskId: 'task-1',
      assistantContent: '这段内容不应显示',
    })
    const currentTask = task({
      status,
      error: status === 'failed'
        ? { code: 'provider_failed', message: '执行失败' }
        : null,
    })
    const state = stateFromScreenplayConversationSnapshot(snapshot(
      currentTurn,
      currentTask,
      12,
      operation({ status, error: currentTask.error }),
    ))

    assert.equal(state.messages[1].content, '')
  }
})

test('artifact and final answer require one succeeded finalization receipt', () => {
  const completedTurn = turn({
    status: 'completed',
    assistantContent: '候选稿已经完成。',
    taskId: 'task-1',
  })
  const completedTask = task({
    status: 'completed',
    resultRevisionId: 'revision-1',
    resultRevision: revision('revision-1', 'screenplayDraft'),
  })
  const withoutReceipt = operation({
    status: 'succeeded',
    resultRevisionId: 'revision-1',
    finalizationReceiptId: null,
  })
  const invalid = stateFromScreenplayConversationSnapshot(snapshot(
    completedTurn,
    completedTask,
    12,
    withoutReceipt,
  ))
  assert.equal(invalid.messages[1].content, '')
  assert.equal(screenplayTurnArtifacts(
    invalid.operations,
    invalid.tasks,
    null,
  ).size, 0)

  const finalized = operation({
    status: 'succeeded',
    resultRevisionId: 'revision-1',
    finalizationReceiptId: 'finalization-1',
  })
  const restored = stateFromScreenplayConversationSnapshot(snapshot(
    completedTurn,
    completedTask,
    13,
    finalized,
  ))
  assert.equal(restored.messages.filter((item) => (
    item.role === 'assistant' && item.content === '候选稿已经完成。'
  )).length, 1)
  assert.equal(screenplayTurnArtifacts(
    restored.operations,
    restored.tasks,
    null,
  ).size, 1)
})

test('cancel is available only before a durable request is pending', () => {
  assert.equal(isScreenplayOperationCancellable(operation()), true)
  assert.equal(isScreenplayOperationCancellable(operation({ status: 'paused' })), true)
  assert.equal(isScreenplayOperationCancellable(operation({
    cancelRequestedAt: '1234',
    cancelReceiptId: 'cancel-1',
  })), false)
  assert.equal(isScreenplayOperationCancellable(operation({ status: 'succeeded' })), false)
})

test('screenplay client refreshes canonical snapshot after cursor events', async () => {
  let snapshotReads = 0
  let truncatedTurnId = ''
  let resumedRevision = 0
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
    watchScreenplayConversationEvents: () => () => undefined,
    cancelScreenplayConversationTurn: async () => ({
      success: true,
      data: {
        id: 'cancel-1',
        cancelReceiptId: 'cancel-1',
        operationId: 'operation-1',
        turnId: 'turn-1',
        requestedAt: '2026-08-09T00:00:02Z',
        terminalStatus: 'succeeded',
      },
    }),
    resumeScreenplayConversationOperation: async (input) => {
      resumedRevision = input.expectedOperationRevision
      return {
        success: true,
        data: {
          operationId: 'operation-1',
          turnId: 'turn-1',
          status: 'running',
          revision: 2,
          capabilitySnapshotDigest: 'capability-1',
        },
      }
    },
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
  const resumed = await client.resume(
    'resume-1',
    'operation-1',
    7,
    {
      apiKey: 'secret',
      options: { model: 'fixture-model' },
    },
  )

  assert.equal(snapshotReads, 1)
  assert.equal(refreshed.cursor, 13)
  assert.equal(
    refreshed.messages[1].content,
    '',
  )
  assert.equal(truncatedTurnId, 'turn-1')
  assert.equal(resumedRevision, 7)
  assert.equal(resumed.revision, 2)
})

test('screenplay SSE uses canonical Agent chunks as its only invalidation', () => {
  let onEvent: ((event: ScreenplayConversationStreamEvent) => void) | undefined
  let closed = false
  const client = new ScreenplayConversationClient({
    submitScreenplayConversationTurn: async () => ({ success: true, data: turn() }),
    getScreenplayConversationSnapshot: async () => ({ success: true, data: snapshot() }),
    watchScreenplayConversationEvents: (input) => {
      onEvent = input.onEvent
      return () => { closed = true }
    },
    cancelScreenplayConversationTurn: async () => ({
      success: true,
      data: {
        id: 'cancel-1',
        cancelReceiptId: 'cancel-1',
        operationId: 'operation-1',
        turnId: 'turn-1',
        requestedAt: '2026-08-09T00:00:02Z',
        terminalStatus: 'cancel_requested',
      },
    }),
    resumeScreenplayConversationOperation: async () => ({
      success: true,
      data: {
        operationId: 'operation-1',
        turnId: 'turn-1',
        status: 'running',
        revision: 2,
        capabilitySnapshotDigest: 'capability-1',
      },
    }),
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
  const chunkPage: ScreenplayAgentChunkPage = {
    kind: 'agent_chunks',
    chunks: [],
    nextCursor: 21,
    hasMore: false,
  }
  onEvent?.(chunkPage)
  onEvent?.(chunkPage)
  stop()

  assert.equal(invalidations, 1)
  assert.equal(chunkPages.length, 1)
  assert.equal(closed, true)
})
