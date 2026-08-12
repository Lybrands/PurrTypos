import assert from 'node:assert/strict'
import test from 'node:test'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts.ts'
import { initialCanonicalOutputState } from '../../../agent-runtime/canonicalOutput.ts'
import {
  buildAssistantTimeline,
  getAssistantProcessingLabel,
  getExecutionPanelLogKey,
  getExecutionPanelPresentation,
  getOperationGroupProgress,
  groupConsecutiveWorkSteps,
  type AssistantTimelinePart,
} from './timeline.ts'

test('canonical timeline filters model operations and groups consecutive work once', () => {
  const base = initialCanonicalOutputState()
  const operation = (operationId: string, kind: string, status: 'running' | 'succeeded') => ({
    operationId,
    runId: 'run-1',
    invocationId: null,
    kind,
    firstSequence: Number(operationId.at(-1)),
    status,
    startedAt: '2026-08-12T00:00:00Z',
    display: { labelParams: {} },
  })
  const operations = {
    'model-1': operation('model-1', 'model', 'succeeded'),
    'tool-1': operation('tool-1', 'tool', 'succeeded'),
    'tool-2': operation('tool-2', 'tool', 'running'),
  }
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    canonicalOutput: {
      ...base,
      operationOrder: Object.keys(operations),
      operations,
    },
  }
  const timeline = buildAssistantTimeline(message, {
    messageIndex: 0,
    isStreaming: true,
  })
  const visible = timeline.filter((part) => part.type === 'operation')
  assert.deepEqual(visible.map((part) => part.operation.kind), ['tool', 'tool'])
  const grouped = groupConsecutiveWorkSteps(visible, 'turn-1')
  assert.equal(grouped.length, 2)
  assert.equal(getExecutionPanelPresentation(visible, {
    isStreaming: true,
  }).title, '正在进行')
})

test('canonical timeline retains compaction and sequenced delegation without duplicating operations', () => {
  const base = initialCanonicalOutputState()
  const delegation = {
    delegationId: 'delegation-1',
    parentRunId: 'run-1',
    rootRunId: 'run-1',
    childRunId: 'run-child-1',
    agentRole: 'reviewer',
    agentTitle: '审阅 Agent',
    objective: '检查连续性',
    unitId: null,
    attempt: null,
    status: 'running' as const,
    required: true,
    priority: 0,
    resultSummary: null,
    error: null,
  }
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    contextCompaction: { status: 'completed', compactedTurnCount: 4 },
    delegations: [delegation],
    canonicalOutput: {
      ...base,
      commentaryBlocks: [{
        outputStreamId: 'commentary-1',
        firstSequence: 1,
        lastSequence: 1,
        startedAt: '2026-08-12T00:00:00Z',
        text: '先检查素材。',
        committed: true,
        aborted: false,
      }],
      operationOrder: [
        'compaction-1',
        'model-1',
        'tool-1',
        'delegation-operation-1',
      ],
      operations: {
        'compaction-1': {
          operationId: 'compaction-1',
          runId: 'run-1',
          invocationId: null,
          kind: 'context_compaction',
          firstSequence: 0,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
        'model-1': {
          operationId: 'model-1',
          runId: 'run-1',
          invocationId: null,
          kind: 'model',
          firstSequence: 2,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
        'tool-1': {
          operationId: 'tool-1',
          runId: 'run-1',
          invocationId: null,
          kind: 'tool',
          firstSequence: 3,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
        'delegation-operation-1': {
          operationId: 'delegation-operation-1',
          runId: 'run-1',
          invocationId: null,
          kind: 'delegation',
          firstSequence: 4,
          status: 'running',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
      },
      delegationOrder: ['delegation-1'],
      delegations: {
        'delegation-1': {
          delegationId: 'delegation-1',
          firstSequence: 4,
          parentRunId: 'run-1',
          childRunId: 'run-child-1',
          agentRole: 'reviewer',
          agentTitle: '审阅 Agent',
          objective: '检查连续性',
          status: 'running',
          errorCode: null,
          output: initialCanonicalOutputState(),
        },
      },
    },
  }

  const timeline = buildAssistantTimeline(message, { messageIndex: 0 })
  assert.deepEqual(timeline.map((part) => part.type), [
    'contextCompaction',
    'commentary',
    'operation',
    'delegations',
  ])
  assert.deepEqual(
    timeline
      .filter((part) => part.type === 'operation')
      .map((part) => part.operation.kind),
    ['tool'],
  )
  assert.deepEqual(
    groupConsecutiveWorkSteps(timeline, 'turn-canonical')
      .map((part) => part.type),
    ['contextCompaction', 'commentary', 'operation', 'delegations'],
  )
})

test('canonical timeline places the latest compaction state at the highest compaction sequence', () => {
  const base = initialCanonicalOutputState()
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    contextCompaction: { status: 'completed', compactedTurnCount: 8 },
    canonicalOutput: {
      ...base,
      commentaryBlocks: [
        {
          outputStreamId: 'between-compactions',
          firstSequence: 3,
          lastSequence: 3,
          startedAt: '2026-08-12T00:00:00Z',
          text: '第一次压缩后继续检查。',
          committed: true,
          aborted: false,
        },
        {
          outputStreamId: 'after-compactions',
          firstSequence: 7,
          lastSequence: 7,
          startedAt: '2026-08-12T00:00:00Z',
          text: '第二次压缩后继续处理。',
          committed: true,
          aborted: false,
        },
      ],
      operationOrder: [
        'pre-planning-compaction',
        'middle-tool',
        'post-planning-compaction',
      ],
      operations: {
        'pre-planning-compaction': {
          operationId: 'pre-planning-compaction',
          runId: 'run-1',
          invocationId: null,
          kind: 'context_compaction',
          firstSequence: 1,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: { phase: 'pre_planning' } },
        },
        'middle-tool': {
          operationId: 'middle-tool',
          runId: 'run-1',
          invocationId: null,
          kind: 'tool',
          firstSequence: 4,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
        'post-planning-compaction': {
          operationId: 'post-planning-compaction',
          runId: 'run-1',
          invocationId: null,
          kind: 'context_compaction',
          firstSequence: 6,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: { phase: 'post_planning' } },
        },
      },
    },
  }

  const timeline = buildAssistantTimeline(message, { messageIndex: 0 })
  assert.deepEqual(timeline.map((part) => part.type), [
    'commentary',
    'operation',
    'contextCompaction',
    'commentary',
  ])
  assert.equal(
    timeline.filter((part) => part.type === 'contextCompaction').length,
    1,
  )
  assert.deepEqual(
    timeline
      .filter((part) => part.type === 'operation')
      .map((part) => part.operation.kind),
    ['tool'],
  )
})

test('canonical timeline preserves commentary sequence and the real provider stream', () => {
  const base = initialCanonicalOutputState()
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '旧正文',
    streamingContent: '实时正文',
    canonicalOutput: {
      ...base,
      finalStreamStatus: 'open',
      commentaryBlocks: [
        {
          outputStreamId: 'commentary-2',
          firstSequence: 2,
          lastSequence: 2,
          startedAt: '2026-08-12T00:00:00Z',
          text: '第二段说明',
          committed: true,
          aborted: false,
        },
        {
          outputStreamId: 'commentary-0',
          firstSequence: 0,
          lastSequence: 0,
          startedAt: '2026-08-12T00:00:00Z',
          text: '第一段说明',
          committed: true,
          aborted: false,
        },
      ],
      operationOrder: ['tool-1'],
      operations: {
        'tool-1': {
          operationId: 'tool-1',
          runId: 'run-1',
          invocationId: null,
          kind: 'tool',
          firstSequence: 1,
          status: 'succeeded',
          startedAt: '2026-08-12T00:00:00Z',
          display: { labelParams: {} },
        },
      },
    },
  }

  assert.deepEqual(
    buildAssistantTimeline(message, {
      messageIndex: 0,
      isStreaming: true,
    }).map((part) => (
      part.type === 'commentary' || part.type === 'text'
        ? [part.type, part.md]
        : part.type
    )),
    [
      ['commentary', '第一段说明'],
      'operation',
      ['commentary', '第二段说明'],
      ['text', '实时正文'],
    ],
  )
  assert.deepEqual(
    buildAssistantTimeline({
      ...message,
      canonicalOutput: {
        ...message.canonicalOutput!,
        finalStreamStatus: 'committed',
      },
    }, {
      messageIndex: 0,
      isStreaming: true,
    }).at(-1),
    { type: 'text', md: '旧正文' },
  )
})

test('legacy commentary stays after recorded tools and root text remains atomic', () => {
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '最终答复',
    commentaryBlocks: ['两个工具完成后的公开说明'],
    toolCallSegments: [
      { commentaryBlockIndex: null, labels: ['查看人物列表'] },
      { commentaryBlockIndex: null, labels: ['查看人物信息'] },
    ],
  }

  assert.deepEqual(
    buildAssistantTimeline(message, { messageIndex: 0 })
      .map((part) => part.type),
    ['tools', 'tools', 'commentary', 'text'],
  )
  assert.deepEqual(
    buildAssistantTimeline(message, {
      messageIndex: 0,
      isStreaming: true,
      isLastAssistant: true,
      loading: true,
    }).map((part) => part.type),
    ['tools', 'tools', 'commentary'],
  )
})

test('active child timelines may expose received text without exposing root text', () => {
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '已经输出的子 Run 文案',
  }
  const streamingOptions = {
    messageIndex: 0,
    isStreaming: true,
    isLastAssistant: true,
    loading: true,
  }

  assert.equal(
    buildAssistantTimeline(message, streamingOptions)
      .some((part) => part.type === 'text'),
    false,
  )
  assert.deepEqual(
    buildAssistantTimeline(message, {
      ...streamingOptions,
      allowStreamingText: true,
    }).filter((part) => part.type === 'text'),
    [{ type: 'text', md: '已经输出的子 Run 文案' }],
  )
})

test('execution panel covers active empty work and completed visible operations', () => {
  assert.deepEqual(
    getExecutionPanelPresentation([], { isStreaming: true }),
    {
      visible: true,
      active: true,
      autoOpen: false,
      stepCount: 0,
      title: '正在进行',
    },
  )

  const completedParts: AssistantTimelinePart[] = [{
    type: 'tools',
    segmentIndex: 0,
    segment: {
      commentaryBlockIndex: null,
      labels: ['缓存读取', '读取人物资料'],
      cachedFlags: [true, false],
      completedToolCount: 2,
    },
  }]
  assert.deepEqual(
    getExecutionPanelPresentation(completedParts, {
      isStreaming: false,
      durationMs: 4200,
    }),
    {
      visible: true,
      active: false,
      autoOpen: false,
      stepCount: 1,
      title: '执行了 1 个步骤',
    },
  )
  assert.deepEqual(
    getExecutionPanelPresentation([], { isStreaming: false }),
    {
      visible: false,
      active: false,
      autoOpen: false,
      stepCount: 0,
      title: '用时',
    },
  )
})

test('execution panel keys stay turn-specific', () => {
  assert.notEqual(
    getExecutionPanelLogKey({ conversationId: 1201, clientTurnId: 'turn-a' }),
    getExecutionPanelLogKey({ conversationId: 1202, clientTurnId: 'turn-b' }),
  )
  assert.equal(
    getExecutionPanelLogKey({ conversationId: 1201, clientTurnId: 'turn-a' }),
    'client-turn-turn-a-work-log',
  )
  assert.equal(
    getExecutionPanelLogKey({ conversationId: 1201 }),
    'conversation-1201-work-log',
  )
  assert.equal(
    getExecutionPanelLogKey({ turnStartedAt: 42 }),
    'live-turn-42-work-log',
  )
  assert.equal(getExecutionPanelLogKey({}), null)
})

test('execution progress counts only visible rows at the active frontier', () => {
  assert.deepEqual(getOperationGroupProgress([
    {
      type: 'tools',
      segmentIndex: 0,
      segment: {
        commentaryBlockIndex: null,
        labels: ['读取人物资料', '读取场景资料'],
        durationMs: 1200,
      },
    },
    {
      type: 'tools',
      segmentIndex: 1,
      isLive: true,
      segment: {
        commentaryBlockIndex: null,
        labels: ['检查人物弧光', '检查结构节奏', '检查对白', '写入候选稿'],
        completedToolCount: 0,
      },
    },
  ]), {
    total: 6,
    completed: 2,
    current: 3,
    active: true,
    parallel: false,
  })

  assert.deepEqual(getOperationGroupProgress([{
    type: 'tools',
    segmentIndex: 0,
    segment: {
      commentaryBlockIndex: null,
      labels: ['缓存读取', '真实读取'],
      cachedFlags: [true, false],
      completedToolCount: 2,
    },
  }]), {
    total: 1,
    completed: 1,
    current: 1,
    active: false,
    parallel: false,
  })
})

test('consecutive legacy operations remain direct rows under one panel', () => {
  const grouped = groupConsecutiveWorkSteps([
    {
      type: 'commentary',
      md: '先检查正文。',
      regionKey: 'commentary-0',
    },
    {
      type: 'tools',
      segmentIndex: 0,
      segment: {
        commentaryBlockIndex: 0,
        labels: ['写入剧本候选稿', '检查剧本候选稿'],
      },
    },
    {
      type: 'tools',
      segmentIndex: 1,
      segment: {
        commentaryBlockIndex: null,
        labels: ['发布候选稿'],
      },
    },
  ], 'turn-6')

  assert.deepEqual(grouped.map((item) => item.type), [
    'commentary',
    'tools',
    'tools',
  ])
  assert.deepEqual(
    grouped
      .filter((part) => part.type === 'tools')
      .flatMap((part) => part.segment.labels),
    ['写入剧本候选稿', '检查剧本候选稿', '发布候选稿'],
  )
})

test('a persisted single-operation batch keeps its recorded row timing', () => {
  const timeline = buildAssistantTimeline({
    role: 'assistant',
    content: '模型原文',
    toolCallSegments: [{
      labels: ['查看章节内容'],
      commentaryBlockIndex: null,
      durationMs: 8598,
    }],
  }, {
    messageIndex: 0,
    isStreaming: false,
  })

  const toolPart = timeline.find((part) => part.type === 'tools')
  assert.deepEqual(toolPart?.segment.itemDurationsMs, [8598])
})

test('runtime metadata never becomes host-authored assistant copy', () => {
  const states: AgentConversationMessage[] = [
    { role: 'assistant', content: '' },
    {
      role: 'assistant',
      content: '',
      contextCompaction: { status: 'running' },
    },
    { role: 'assistant', content: '', commentary: '正在核对范围' },
    { role: 'assistant', content: '', longTaskId: 'task-1' },
    {
      role: 'assistant',
      content: '',
      toolCallSegments: [{ labels: ['tool'], commentaryBlockIndex: null }],
    },
  ]

  for (const state of states) {
    assert.equal(getAssistantProcessingLabel(state), '')
  }
  assert.deepEqual(
    buildAssistantTimeline(states[3], { messageIndex: 0 }),
    [],
  )
})
