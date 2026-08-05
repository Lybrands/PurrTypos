import test from 'node:test'
import assert from 'node:assert/strict'
import type { AiLongTask, AiLongTaskUnit } from '../types'
import type { AiStreamChunk } from '../agent-runtime'
import { parseLongTaskTime } from './longTaskProgress.ts'
import {
  applyLongTaskProgress,
  buildLongTaskTaskPlan,
  createLongTaskConversationAdapter,
  extractLongTaskProposal,
  longTaskBelongsToSession,
  resetLongTaskAssistantMessage,
  resolveProposalSourceRunId,
} from './longTaskConversationAdapter.ts'
import { findPersistedProposalDocument } from './proposalPersistence.ts'

function unit(
  id: string,
  position: number,
  status: AiLongTaskUnit['status'],
  attempt = 1,
): AiLongTaskUnit {
  return {
    id,
    position,
    status,
    attempt,
    maxAttempts: 3,
    metadata: {},
  }
}

function task(
  status: AiLongTask['status'],
  units: AiLongTaskUnit[],
  kind = 'screenplay_draft_generation',
): AiLongTask {
  return {
    id: 'task-1',
    workItemId: 'work-1',
    namespace: 'purrtypos.screenplay',
    kind,
    ownerId: 'project-1',
    parentRunId: 'run-1',
    status,
    revision: 1,
    totalUnits: units.length,
    completedUnits: units.filter((item) => item.status === 'completed').length,
    failedUnits: units.filter((item) => item.status === 'failed').length,
    maxParallelism: 1,
    metadata: {},
    units,
  }
}

test('naive ISO task timestamps are interpreted as SQLite UTC', () => {
  assert.equal(
    parseLongTaskTime('2026-08-04T11:04:00'),
    Date.parse('2026-08-04T11:04:00Z'),
  )
  assert.equal(
    parseLongTaskTime('2026-08-04 11:04:00'),
    Date.parse('2026-08-04T11:04:00Z'),
  )
  assert.equal(
    parseLongTaskTime('2026-08-04T19:04:00+08:00'),
    Date.parse('2026-08-04T19:04:00+08:00'),
  )
})

test('persisted long-task proposal survives normalized titles and hydration', () => {
  const proposal = {
    kind: 'scene_draft' as const,
    title: '  Agent 长篇正文 · s01–s05  ',
    contentJson: { longTaskId: 'longtask-1', newSceneIds: ['s01'] },
    contentText: 'INT. 网吧 - 夜',
    derivedFromIds: [],
  }
  const document = {
    id: 'document-1',
    project_id: 'project-1',
    kind: 'scene_draft' as const,
    title: 'Agent 长篇正文 · s01–s05',
    content_json: { longTaskId: 'longtask-1', newSceneIds: ['s01'] },
    content_text: 'INT. 网吧 - 夜',
    version: 1,
    status: 'draft' as const,
    derived_from_ids: [],
  }

  assert.equal(
    findPersistedProposalDocument([document], proposal)?.id,
    'document-1',
  )
})

test('persisted ordinary proposal matches exact body after title normalization', () => {
  const proposal = {
    kind: 'creative_brief' as const,
    title: ' 创作简报 ',
    contentJson: {},
    contentText: '正式内容',
    derivedFromIds: [],
  }
  const document = {
    id: 'document-2',
    project_id: 'project-1',
    kind: 'creative_brief' as const,
    title: '创作简报',
    content_json: {},
    content_text: '正式内容',
    version: 1,
    status: 'accepted' as const,
    derived_from_ids: [],
  }

  assert.equal(
    findPersistedProposalDocument([document], proposal)?.status,
    'accepted',
  )
})

test('restored active conversation timer uses the same UTC task timestamp', () => {
  const active = task('running', [unit('batch-0001', 0, 'running')])
  active.createTime = '2026-08-04T11:04:00'
  const originalNow = Date.now
  Date.now = () => Date.parse('2026-08-04T11:04:20Z')
  try {
    const before = performance.now()
    const restored = resetLongTaskAssistantMessage({
      role: 'assistant',
      content: '',
    }, active)
    const after = performance.now()
    assert.ok(restored.turnStartedAt != null)
    const elapsed = after - restored.turnStartedAt
    assert.ok(elapsed >= 20_000)
    assert.ok(elapsed <= 20_000 + (after - before) + 1)
  } finally {
    Date.now = originalNow
  }
})

test('durable task capsule mirrors the executor units instead of a receipt plan', () => {
  const first = unit('batch-0001', 0, 'completed')
  first.metadata = { sceneHeadings: ['黄阿福', '门卫亭'] }
  const second = unit('batch-0002', 1, 'running')
  second.metadata = { sceneHeadings: ['嫉妒之火'] }
  const liveTask = task('running', [
    first,
    second,
    unit('finalize', 2, 'pending'),
  ])

  const plan = buildLongTaskTaskPlan(liveTask)

  assert.equal(plan.title, '长篇正文分批创作')
  assert.equal(plan.status, 'running')
  assert.deepEqual(plan.steps.map((step) => step.status), [
    'done',
    'running',
    'pending',
  ])
  assert.equal(plan.steps[0].title, '创作并校验：黄阿福、门卫亭')
  assert.equal(plan.steps[2].title, '核验并生成可应用提案')
})

test('child Run events are adapted to the ordinary Agent chunk contract', () => {
  const generate = unit('generate', 1, 'running', 1)
  const liveTask = task('running', [generate])
  liveTask.createTime = new Date(Date.now() - 5_000).toISOString()
  const base = {
    taskId: liveTask.id,
    unitId: 'generate',
    attempt: 1,
    runId: 'run-child',
  }
  const adapter = createLongTaskConversationAdapter('', liveTask)
  const chunks = [
    { type: 'turn.started' as const, ...base, title: '生成场景正文' },
    {
      type: 'turn.thinking.delta' as const,
      ...base,
      cursor: 2,
      delta: '先读取分集结构，再整理每场目标。',
    },
    {
      type: 'turn.chunk' as const,
      ...base,
      cursor: 3,
      chunk: {
        toolCalls: [{
          id: 'tool-1',
          type: 'function',
          function: {
            name: 'getScreenplayDocument',
            arguments: '{"documentId":"doc-1"}',
          },
          displayNames: { 'zh-CN': '读取剧本文档' },
        }],
        toolCallsInProgress: true,
      },
    },
    {
      type: 'turn.chunk' as const,
      ...base,
      cursor: 4,
      chunk: { toolResults: [{
        tool_call_id: 'tool-1',
        name: 'getScreenplayDocument',
        content: '{"documentId":"doc-1"}',
      }] },
    },
    {
      type: 'turn.chunk' as const,
      ...base,
      cursor: 5,
      chunk: {
        toolCallId: 'tool-1',
        toolIndexCompleted: 0,
        toolOutcome: 'completed',
      },
    },
    {
      type: 'turn.response' as const,
      ...base,
      cursor: 6,
      content: '已完成本批场景正文。',
    },
  ].flatMap((event) => adapter.toChunks(event))

  assert.equal(chunks[0].agentDelegationCreated?.agentRole, 'screenplay_writer')
  const nested = chunks.slice(1).map(
    (chunk) => chunk.agentSubRunEvent?.chunk as AiStreamChunk | undefined,
  )
  assert.equal(nested[0]?.agentRunStarted?.runId, 'run-child')
  assert.equal(nested[1]?.thinkingDelta, '先读取分集结构，再整理每场目标。')
  assert.equal(nested[2]?.toolCalls?.[0].displayNames?.['zh-CN'], '读取剧本文档')
  assert.equal(nested[2]?.toolCalls?.[0].function.arguments, '{"documentId":"doc-1"}')
  assert.equal(nested[3]?.toolResults?.[0].tool_call_id, 'tool-1')
  assert.equal(nested[4]?.toolIndexCompleted, 0)
  assert.equal(nested[5]?.delta, '已完成本批场景正文。')
  assert.equal(chunks[1].agentSubRunEvent?.parentRunId, liveTask.parentRunId)

  const projected = resetLongTaskAssistantMessage({
    role: 'assistant',
    content: '已恢复原有长篇正文任务，将从上次检查点继续；本轮将持续更新执行进度与结果。',
    longTaskId: 'task-1',
  }, liveTask)

  assert.equal(projected.content, '')
  assert.equal(projected.taskPlan?.steps.length, 1)
  assert.equal(typeof projected.turnStartedAt, 'number')
  assert.ok(performance.now() - projected.turnStartedAt! >= 4_500)
  assert.ok(performance.now() - projected.turnStartedAt! < 6_500)
})

test('durable child chunks preserve the ordinary Agent chunk without remapping', () => {
  const ordinaryChunk = {
    toolApprovalRequired: {
      runId: 'run-child',
      approvalId: 'approval-1',
      toolName: 'writeDocument',
      status: 'pending',
      title: '写入剧本文档',
      riskLevel: 'write',
      summary: '提交正文提案',
    },
    contextCompaction: {
      status: 'completed',
      compactedTurns: 4,
    },
    agentDelegationCreated: {
      runId: 'run-child',
      delegationId: 'delegate-1',
      parentRunId: 'run-child',
      rootRunId: 'run-child',
      childRunId: null,
      agentRole: 'researcher',
      agentTitle: '原作核对',
      objective: '核对原作',
      status: 'queued',
      required: true,
      priority: 0,
      resultSummary: null,
      error: null,
    },
  }
  const adapter = createLongTaskConversationAdapter()

  const chunks = adapter.toChunks({
    type: 'turn.chunk',
    taskId: 'task-1',
    unitId: 'batch-1',
    attempt: 1,
    runId: 'run-child',
    cursor: 9,
    chunk: ordinaryChunk,
  })

  assert.equal(chunks.length, 1)
  assert.equal(chunks[0].agentSubRunEvent?.delegationId, 'longtask:task-1:batch-1:1')
  assert.deepEqual(chunks[0].agentSubRunEvent?.chunk, ordinaryChunk)
})

test('lightweight SSE progress updates task state without replacing unit metadata', () => {
  const active = task('running', [
    unit('batch-1', 0, 'running', 1),
    unit('batch-2', 1, 'pending', 1),
  ])
  active.units[0].metadata = { label: '动态规划第一批', sceneIds: ['s05'] }

  const updated = applyLongTaskProgress(active, {
    type: 'task.progress',
    taskId: active.id,
    status: 'running',
    revision: 4,
    totalUnits: 2,
    completedUnits: 1,
    failedUnits: 0,
    updateTime: '2026-08-05T10:00:00',
    units: [
      {
        id: 'batch-1',
        position: 0,
        status: 'completed',
        attempt: 1,
        maxAttempts: 3,
        runId: 'run-child-1',
        outputRef: 'result-1',
        errorCode: null,
      },
      {
        id: 'batch-2',
        position: 1,
        status: 'running',
        attempt: 1,
        maxAttempts: 3,
        runId: 'run-child-2',
        outputRef: null,
        errorCode: null,
      },
    ],
  })

  assert.equal(updated.completedUnits, 1)
  assert.equal(updated.units[0].status, 'completed')
  assert.equal(updated.units[1].status, 'running')
  assert.deepEqual(updated.units[0].metadata, {
    label: '动态规划第一批',
    sceneIds: ['s05'],
  })
})

test('durable proposal persistence uses the root Run instead of the visible child Run', () => {
  const durable = task('completed', [unit('finalize', 1, 'completed', 1)])
  durable.parentRunId = 'run-dispatch-root'

  assert.equal(
    resolveProposalSourceRunId(durable, 'run-visible-child'),
    'run-dispatch-root',
  )
  assert.equal(
    resolveProposalSourceRunId(null, 'run-ordinary-chat'),
    'run-ordinary-chat',
  )
})

test('completed task proposal is discovered without a hard-coded finalize unit id', () => {
  const generate = unit('planner-generated-summary', 7, 'completed', 1)
  generate.metadata = {
    proposal: {
      kind: 'scene_draft',
      title: '后续场景正文',
      contentJson: { newSceneIds: ['s05'] },
      contentText: 'INT. 门卫亭 - 日',
      derivedFromIds: ['scene-list-1'],
    },
  }
  const completed = task('completed', [generate])

  assert.deepEqual(extractLongTaskProposal(completed), generate.metadata.proposal)
})

test('terminal proposal enters the ordinary screenplay proposal chunk before done', () => {
  const proposal = {
    kind: 'scene_draft' as const,
    title: '后续场景正文',
    contentJson: { newSceneIds: ['s05'] },
    contentText: 'INT. 门卫亭 - 日',
    derivedFromIds: ['scene-list-1'],
  }
  const chunks = createLongTaskConversationAdapter().toChunks({
    type: 'task.terminal',
    taskId: 'task-1',
    status: 'completed',
    proposal,
    finalResponse: '已完成本次批量创作。',
  })

  assert.deepEqual(chunks, [
    { delta: '已完成本次批量创作。' },
    { proposedScreenplayDocument: proposal },
    { done: true, aborted: false },
  ])
})

test('replayed child Run cursors do not duplicate streamed thinking', () => {
  const event = {
    type: 'turn.thinking.delta' as const,
    taskId: 'task-1',
    unitId: 'batch-1',
    attempt: 1,
    runId: 'run-child',
    cursor: 8,
    delta: '逐字流式输出。',
  }
  const adapter = createLongTaskConversationAdapter()
  const once = adapter.toChunks(event)
  const replayed = adapter.toChunks(event)

  assert.equal(
    once[0].agentSubRunEvent?.chunk.thinkingDelta,
    '逐字流式输出。',
  )
  assert.deepEqual(replayed, [])
})

test('child model and context events keep the debug accounting contract', () => {
  const adapter = createLongTaskConversationAdapter()
  const base = {
    taskId: 'task-1',
    unitId: 'batch-1',
    attempt: 1,
    runId: 'run-child',
  }
  const model = adapter.toChunks({
    type: 'turn.model.call',
    ...base,
    cursor: 2,
    modelInvocation: {
      phase: 'generation',
      count: 1,
      round: 1,
      toolNames: ['proposeSceneDraft'],
    },
  })
  const context = adapter.toChunks({
    type: 'turn.context.usage',
    ...base,
    cursor: 3,
    contextBudget: {
      actualInputTokens: 1200,
      actualOutputTokens: 800,
    },
  })

  const modelChunk = model[0].agentSubRunEvent?.chunk as AiStreamChunk | undefined
  const contextChunk = context[0].agentSubRunEvent?.chunk as AiStreamChunk | undefined
  assert.equal(modelChunk?.modelInvocation?.round, 1)
  assert.equal(
    contextChunk?.contextBudget?.actualOutputTokens,
    800,
  )
})

test('a task from another session is never attached to the new conversation', () => {
  const foreignTask = task('running', [unit('batch-1', 0, 'running')])
  foreignTask.metadata = { sessionId: 21 }
  assert.equal(longTaskBelongsToSession(foreignTask, 22), false)
  assert.equal(longTaskBelongsToSession(foreignTask, 21), true)
})
