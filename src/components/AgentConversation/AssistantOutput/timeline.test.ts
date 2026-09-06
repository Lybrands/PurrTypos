import assert from 'node:assert/strict'
import test from 'node:test'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts.ts'
import { initialCanonicalOutputState, reduceCanonicalOutput, type CanonicalOutputEvent } from '../../../agent-runtime/canonicalOutput.ts'
import { getAgentProcessingLabel } from '../../../agent-runtime/outputPresentation.ts'
import {
  buildAssistantTimeline,
  executionPanelHasTerminalError,
  getCanonicalOperationStatusText,
  getActiveOperationLabel,
  getExecutionPanelLogKey,
  getExecutionPanelPresentation,
  getOperationGroupProgress,
  groupConsecutiveWorkSteps,
  type AssistantTimelinePart,
} from './timeline.ts'

test('native Provider progress grows one stable live region from real chunks', () => {
  const operation: CanonicalOutputEvent = {
    eventId: 'model-start-1', runId: 'run-1', turnId: 'turn-1',
    invocationId: 'invocation-1', outputStreamId: null, sequence: 1,
    source: 'runtime', kind: 'operation.started', channel: 'operation', visibility: 'public',
    occurredAt: '2026-09-02T08:00:00Z', emittedAt: '2026-09-02T08:00:00Z',
    payload: {
      operationId: 'model-operation-1',
      kind: 'model',
      display: { labelKey: 'agent.operation.model', labelParams: {} },
    },
  }
  const progress: CanonicalOutputEvent = {
    eventId: 'agent-progress-1', runId: 'run-1', turnId: 'turn-1',
    invocationId: 'invocation-1', outputStreamId: 'stream-1', sequence: 2,
    source: 'provider', kind: 'agent.progress', channel: 'commentary', visibility: 'public',
    occurredAt: '2026-09-02T08:00:01Z', emittedAt: '2026-09-02T08:00:01Z',
    payload: {
      schemaVersion: 'purra.agent-progress/v1',
      sourceChunkIndex: 1,
      text: '正在核对人物动机',
    },
  }
  let output = reduceCanonicalOutput(
    reduceCanonicalOutput(initialCanonicalOutputState(), operation),
    progress,
  )
  const message: AgentConversationMessage = {
    role: 'assistant', content: '', canonicalOutput: output,
  }
  const firstTimeline = buildAssistantTimeline(message, {
    messageIndex: 0,
    isStreaming: true,
  })

  assert.equal(getAgentProcessingLabel(message), '正在思考')
  assert.deepEqual(firstTimeline, [{
    type: 'commentary',
    md: '正在核对人物动机',
    startedAt: Date.parse('2026-09-02T08:00:01Z'),
    regionKey: '0-agent-progress-stream-stream-1',
  }])
  output = reduceCanonicalOutput(output, {
    ...progress,
    eventId: 'agent-progress-2',
    sequence: 3,
    payload: {
      ...progress.payload,
      sourceChunkIndex: 2,
      text: '正在核对人物动机与关系',
    },
  })
  const secondTimeline = buildAssistantTimeline({
    ...message,
    canonicalOutput: output,
  }, {
    messageIndex: 0,
    isStreaming: true,
  })
  assert.equal(secondTimeline[0]?.type, 'commentary')
  assert.equal(
    secondTimeline[0]?.type === 'commentary' ? secondTimeline[0].md : '',
    '正在核对人物动机与关系',
  )
  assert.equal(
    secondTimeline[0]?.type === 'commentary' ? secondTimeline[0].regionKey : '',
    firstTimeline[0]?.type === 'commentary' ? firstTimeline[0].regionKey : '',
  )
  assert.equal(output.finalText, '')
})

test('planning uses public narration instead of an executable operation row', () => {
  for (const phase of ['planning', 'replanning']) {
    for (const status of ['succeeded', 'failed', 'canceled']) {
      const started: CanonicalOutputEvent = {
        eventId: 'planning-start', runId: 'run-1', turnId: null,
        invocationId: 'planning-invocation', outputStreamId: null, sequence: 1,
        source: 'runtime', kind: 'operation.started', channel: 'operation', visibility: 'public',
        occurredAt: '2026-08-31T08:34:20Z', emittedAt: '2026-08-31T08:34:20Z',
        payload: {
          operationId: 'planning-1', kind: 'planning', startedAt: '2026-08-31T08:34:20Z',
          display: {
            labelKey: 'agent.operation.planning',
            labelParams: { revision: phase === 'planning' ? 0 : 1 },
          },
        },
      }
      const progress: CanonicalOutputEvent = {
        ...started,
        eventId: 'planning-progress',
        outputStreamId: 'planning-public',
        sequence: 2,
        source: 'provider',
        kind: 'planning.progress',
        channel: 'commentary',
        payload: {
          schemaVersion: 'purra.planning-stream/v1',
          operationId: 'planning-1',
          revision: phase === 'planning' ? 0 : 1,
          attempt: 0,
          recordIndex: 1,
          text: '正在梳理任务目标与执行顺序',
        },
      }
      const privateJson: CanonicalOutputEvent = {
        ...started, eventId: 'private-json', sequence: 3, source: 'provider',
        kind: 'provider.content_delta', channel: 'final', visibility: 'private',
        payload: { delta: '{"privatePlan": "PRIVATE_MARKER"}' },
      }
      const finished: CanonicalOutputEvent = {
        ...started, eventId: 'planning-end', sequence: 4, kind: 'operation.finished',
        occurredAt: '2026-08-31T08:36:07Z', emittedAt: '2026-08-31T08:36:07Z',
        payload: {
          operationId: 'planning-1', status, finishedAt: '2026-08-31T08:36:07Z', durationMs: 106486,
          ...(status === 'succeeded' ? {} : { errorCode: 'planning_failed' }),
        },
      }
      let output = reduceCanonicalOutput(initialCanonicalOutputState(), started)
      const message: AgentConversationMessage = { role: 'assistant', content: '', canonicalOutput: output }
      const live = buildAssistantTimeline(message, { messageIndex: 0, isStreaming: true })
      assert.deepEqual(live, [])
      const label = phase === 'planning' ? '制定计划' : '调整计划'
      assert.equal(getAgentProcessingLabel(message), `正在${label}`)
      output = reduceCanonicalOutput(output, progress)
      assert.equal(getAgentProcessingLabel({ ...message, canonicalOutput: output }), `正在${label}`)
      output = reduceCanonicalOutput(output, privateJson)
      output = reduceCanonicalOutput(output, finished)
      output = reduceCanonicalOutput(output, finished)
      const restored = [started, progress, privateJson, finished].reduce(
        reduceCanonicalOutput,
        initialCanonicalOutputState(),
      )
      assert.deepEqual(output, restored)
      const timeline = buildAssistantTimeline({ ...message, canonicalOutput: restored }, { messageIndex: 0 })
      assert.equal(timeline.length, 1)
      const row = timeline[0]
      assert.equal(row.type, 'commentary')
      if (row.type !== 'commentary') assert.fail('planning must use public narration')
      assert.equal(row.md, '正在梳理任务目标与执行顺序')
      assert.equal(timeline.some((part) => part.type === 'operation'), false)
      assert.equal(getExecutionPanelPresentation(timeline, { isStreaming: false }).stepCount, 0)
      assert.equal(restored.operations['planning-1'].status, status)
      assert.equal(restored.operations['planning-1'].durationMs, 106486)
      assert.equal(JSON.stringify(timeline).includes('PRIVATE_MARKER'), false)
      assert.equal(output.finalText, '')
      assert.equal(output.commentaryText, '')
    }
  }
})

test('canonical timeline filters model operations, localizes tools, and groups consecutive work once', () => {
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
    'validation-1': operation('validation-1', 'validation', 'succeeded'),
    'tool-1': {
      ...operation('tool-1', 'tool', 'succeeded'),
      toolName: 'inspectScreenplayProject',
      display: {
        labelParams: {
          toolName: 'inspectScreenplayProject',
          displayNames: { 'zh-CN': '查看剧本项目' },
        },
      },
    },
    'tool-2': {
      ...operation('tool-2', 'tool', 'running'),
      toolName: 'readScreenplayDeliverable',
      display: {
        labelParams: {
          toolName: 'readScreenplayDeliverable',
          retryOfToolCallId: 'call-read-screenplay-invalid',
          displayNames: { 'zh-CN': '读取剧本交付物' },
        },
      },
    },
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
  assert.deepEqual(visible.map((part) => part.label), [
    '查看剧本项目',
    '读取剧本交付物',
  ])
  assert.deepEqual(visible.map((part) => part.isRetry), [false, true])
  assert.equal(
    getCanonicalOperationStatusText(operations['tool-2'], '读取剧本交付物', true),
    '正在重试 读取剧本交付物',
  )
  assert.equal(
    getCanonicalOperationStatusText(
      { ...operations['tool-2'], status: 'succeeded' },
      '读取剧本交付物',
      true,
    ),
    '重试成功 读取剧本交付物',
  )
  const grouped = groupConsecutiveWorkSteps(visible, 'turn-1')
  assert.equal(grouped.length, 1)
  assert.equal(grouped[0].type, 'stepGroup')
  if (grouped[0].type === 'stepGroup') {
    assert.equal(grouped[0].parts.length, 2)
  }
  assert.equal(getExecutionPanelPresentation(visible, {
    isStreaming: true,
  }).title, '正在进行')
  assert.equal(
    getActiveOperationLabel(visible),
    '重试 读取剧本交付物',
  )
})

test('parameterized backend tool labels survive live and restored canonical timelines', () => {
  for (const [toolName, label] of [
    ['readScreenplayDeliverable', '读取第 2 集场景表'],
    ['readScreenplayDeliverable', '为第 1 集读取创作简报'],
    ['readScreenplayDeliverable', '读取原作分析'],
    ['readScreenplayTaskDependencies', '读取第 1 集第 1 场已完成剧本'],
    ['readScreenplayTaskDependencies', '读取第 1 集第 2 场已完成剧本'],
    ['searchSourceText', '为第 1 集在原文中查找“母亲 庆功宴 起床”'],
    ['readSourceChapters', '为第 1 集读取第1章 清河桥的原文'],
    ['getChapterContent', '查看《第一章 雨夜》正文'],
    ['searchMemories', '检索与“红门 钥匙”相关的长期记忆'],
    ['readNovelAnalysisInput', '读取《第一章 雨夜》第 0–1200 字符作为分析材料'],
  ]) {
    const event: CanonicalOutputEvent = {
      eventId: 'read-start', runId: 'run-1', turnId: null,
      invocationId: null, outputStreamId: null, sequence: 1,
      source: 'runtime', kind: 'operation.started', channel: 'operation', visibility: 'public',
      occurredAt: '2026-09-03T00:00:00Z', emittedAt: '2026-09-03T00:00:00Z',
      payload: {
        operationId: 'read-1', kind: 'tool',
        display: { labelParams: {
          toolName, displayNames: { 'zh-CN': label },
        } },
      },
    }
    let output = reduceCanonicalOutput(initialCanonicalOutputState(), event)
    for (const isStreaming of [true, false]) {
      if (!isStreaming) {
        output = reduceCanonicalOutput(output, {
          ...event, eventId: 'read-finish', sequence: 2, kind: 'operation.finished',
          payload: { operationId: 'read-1', status: 'succeeded' },
        })
      }
      const parts = buildAssistantTimeline({
        role: 'assistant', content: '', canonicalOutput: output,
      }, { messageIndex: 0, isStreaming })
      const operations = parts.filter((part) => part.type === 'operation')
      assert.deepEqual(operations.map((part) => part.label), [label])
      assert.equal(getExecutionPanelPresentation(operations, { isStreaming }).title,
        isStreaming ? '正在进行' : '已完成')
    }
  }
})

test('canonical timeline retains compaction and sequenced delegation without duplicating operations', () => {
  const base = initialCanonicalOutputState()
  const delegation = {
    delegationId: 'delegation-1',
    runId: 'run-1',
    agentName: 'reviewer',
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
          runId: 'run-1',
          agentName: 'reviewer',
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
    ['contextCompaction', 'commentary', 'stepGroup'],
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
      autoOpen: true,
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
      title: '已完成',
    },
  )
  assert.deepEqual(
    getExecutionPanelPresentation([], { isStreaming: false }),
    {
      visible: false,
      active: false,
      autoOpen: false,
      stepCount: 0,
      title: '已完成',
    },
  )
})

test('execution panel marks only terminal failures, not recovered attempts', () => {
  assert.equal(executionPanelHasTerminalError({
    canonicalOutput: {
      ...initialCanonicalOutputState(),
      runStatus: 'done',
      runTerminal: true,
      operations: {
        failedAttempt: {
          operationId: 'failedAttempt',
          runId: 'run-1',
          invocationId: null,
          kind: 'tool',
          firstSequence: 1,
          status: 'failed',
          startedAt: '2026-08-26T00:00:00Z',
          display: { labelParams: {} },
        },
      },
      operationOrder: ['failedAttempt'],
    },
  }), false)
  assert.equal(executionPanelHasTerminalError({
    canonicalOutput: {
      ...initialCanonicalOutputState(),
      runStatus: 'failed',
      runTerminal: true,
    },
  }), true)
  assert.equal(executionPanelHasTerminalError({
    taskPlan: {
      title: '创作剧本',
      status: 'blocked',
      steps: [],
    },
  }), true)
})

test('completed canonical runs retain failed tool attempts in execution history', () => {
  const base = initialCanonicalOutputState()
  const failedTool = {
    operationId: 'failed-write',
    runId: 'run-1',
    invocationId: 'invocation-1',
    kind: 'tool',
    firstSequence: 1,
    status: 'failed' as const,
    startedAt: '2026-08-26T00:00:00Z',
    toolName: 'writeScreenplayCandidatePart',
    display: { labelParams: { toolName: 'writeScreenplayCandidatePart' } },
  }
  const succeededTool = {
    ...failedTool,
    operationId: 'succeeded-write',
    invocationId: 'invocation-2',
    firstSequence: 2,
    status: 'succeeded' as const,
  }
  const message = (runStatus: 'done' | 'failed'): AgentConversationMessage => ({
    role: 'assistant',
    content: runStatus === 'done' ? '已完成' : '',
    canonicalOutput: {
      ...base,
      runStatus,
      runTerminal: true,
      operations: {
        [failedTool.operationId]: failedTool,
        [succeededTool.operationId]: succeededTool,
      },
      operationOrder: [failedTool.operationId, succeededTool.operationId],
    },
  })

  assert.deepEqual(
    buildAssistantTimeline(message('done'), { messageIndex: 0 })
      .filter((part) => part.type === 'operation')
      .map((part) => part.operation.status),
    ['failed', 'succeeded'],
  )
  assert.deepEqual(
    buildAssistantTimeline(message('failed'), { messageIndex: 0 })
      .filter((part) => part.type === 'operation')
      .map((part) => part.operation.status),
    ['failed', 'succeeded'],
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

test('execution panel title stays a status while tool rows retain their labels', () => {
  const parts: AssistantTimelinePart[] = [{
    type: 'tools',
    segmentIndex: 0,
    isLive: true,
    segment: {
      commentaryBlockIndex: null,
      labels: ['读取人物资料', '检查人物弧光', '写入候选稿'],
      completedToolCount: 1,
    },
  }]

  assert.equal(
    getActiveOperationLabel(parts.filter(
      (part): part is Extract<AssistantTimelinePart, { type: 'tools' }> =>
        part.type === 'tools',
    )),
    '检查人物弧光',
  )
  assert.equal(
    getExecutionPanelPresentation(parts, { isStreaming: true }).title,
    '正在进行',
  )
  assert.equal(
    getExecutionPanelPresentation(parts, { isStreaming: false }).title,
    '已完成',
  )
})

test('execution panel does not describe failed or interrupted runs as completed', () => {
  for (const [status, title] of [
    ['failed', '执行失败'],
    ['blocked', '执行失败'],
    ['canceled', '已取消'],
    ['paused', '已暂停'],
  ]) {
    assert.equal(getExecutionPanelPresentation([], {
      isStreaming: false,
      durationMs: 1000,
      status,
    }).title, title)
  }
  assert.equal(getExecutionPanelPresentation([], {
    isStreaming: false,
    hasError: true,
  }).title, '执行失败')
})

test('consecutive legacy operations collapse into one step group', () => {
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
    'stepGroup',
  ])
  const stepGroup = grouped[1]
  assert.equal(stepGroup.type, 'stepGroup')
  if (stepGroup.type === 'stepGroup') {
    assert.deepEqual(
      stepGroup.parts
        .filter((part) => part.type === 'tools')
        .flatMap((part) => part.segment.labels),
      ['写入剧本候选稿', '检查剧本候选稿', '发布候选稿'],
    )
  }
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

test('a persisted multi-operation batch does not invent row timings from its total', () => {
  const timeline = buildAssistantTimeline({
    role: 'assistant',
    content: '模型原文',
    toolCallSegments: [{
      labels: ['读取章节', '读取大纲'],
      commentaryBlockIndex: null,
      durationMs: 8598,
    }],
  }, {
    messageIndex: 0,
    isStreaming: false,
  })

  const toolPart = timeline.find((part) => part.type === 'tools')
  assert.equal(toolPart?.segment.itemDurationsMs, undefined)
})

test('an explicit empty row-timing array disables the legacy fallback', () => {
  const timeline = buildAssistantTimeline({
    role: 'assistant',
    content: '模型原文',
    toolCallSegments: [{
      labels: ['查看章节内容'],
      commentaryBlockIndex: null,
      durationMs: 8598,
      itemDurationsMs: [],
    }],
  }, {
    messageIndex: 0,
    isStreaming: false,
  })

  const toolPart = timeline.find((part) => part.type === 'tools')
  assert.deepEqual(toolPart?.segment.itemDurationsMs, [])
})

test('runtime metadata falls back to model-neutral thinking copy', () => {
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
    assert.equal(getAgentProcessingLabel(state), '正在思考')
  }
  assert.deepEqual(
    buildAssistantTimeline(states[3], { messageIndex: 0 }),
    [],
  )
})

test('commentary stays in the transcript and never becomes the standby title', () => {
  const base = initialCanonicalOutputState()
  const canonicalOutput = {
    ...base,
    commentaryBlocks: [{
      outputStreamId: 'commentary-safe',
      invocationId: 'invocation-1',
      firstSequence: 1,
      lastSequence: 1,
      startedAt: '2026-08-12T00:00:00Z',
      text: '**正在梳理人物关系与关键冲突**',
      committed: true,
      aborted: false,
    }],
    operationOrder: ['tool-1'],
    operations: {
      'tool-1': {
        operationId: 'tool-1',
        runId: 'run-1',
        invocationId: 'invocation-1',
        kind: 'tool',
        firstSequence: 2,
        status: 'running' as const,
        startedAt: '2026-08-12T00:00:00Z',
        display: { labelParams: { toolName: 'inspectScreenplayProject' } },
      },
    },
  }
  const safe: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    canonicalOutput,
    taskPlan: {
      title: '审阅剧本',
      status: 'running',
      steps: [{
        id: 'review-characters',
        title: '检查人物关系与关键冲突',
        type: 'review',
        status: 'running',
      }],
    },
  }
  assert.equal(
    getAgentProcessingLabel(safe),
    '正在执行工具',
  )
  assert.deepEqual(
    buildAssistantTimeline(safe, { messageIndex: 0 })
      .filter((part) => part.type === 'commentary')
      .map((part) => part.md),
    ['**正在梳理人物关系与关键冲突**'],
  )

  const unsafe: AgentConversationMessage = {
    ...safe,
    canonicalOutput: {
      ...canonicalOutput,
      commentaryBlocks: [
        canonicalOutput.commentaryBlocks[0],
        {
          ...canonicalOutput.commentaryBlocks[0],
          outputStreamId: 'commentary-unsafe',
          lastSequence: 3,
          text: '调用 inspectScreenplayProject，处理 run_abcd1234。',
        },
      ],
    },
  }
  assert.equal(getAgentProcessingLabel(unsafe), '正在执行工具')
  assert.deepEqual(
    buildAssistantTimeline(unsafe, { messageIndex: 0 })
      .filter((part) => part.type === 'commentary')
      .map((part) => part.md),
    ['**正在梳理人物关系与关键冲突**', '调用 inspectScreenplayProject，处理 run_abcd1234。'],
  )
})

test('standby ignores live, committed, aborted and replayed prose from the active invocation', () => {
  for (const text of ['已读取材料，现提交分析候选。', '说明正文'.repeat(100)]) {
    for (const committed of [false, true]) {
      for (const aborted of [false, true]) {
        const canonicalOutput = {
          ...initialCanonicalOutputState(),
          commentaryBlocks: [{ outputStreamId: 'public-description', invocationId: 'current',
            firstSequence: 2, lastSequence: 3, startedAt: '2026-09-06T09:00:00Z',
            text, committed, aborted }],
          agentProgress: [{ eventId: 'public-progress', outputStreamId: 'progress', invocationId: 'current',
            text, sequence: 4, sourceChunkIndex: 1, occurredAt: '2026-09-06T09:00:00Z' }],
          operationOrder: ['current-model'],
          operations: { 'current-model': { operationId: 'current-model', runId: 'child', invocationId: 'current',
            kind: 'model', firstSequence: 1, status: 'running' as const,
            startedAt: '2026-09-06T09:00:00Z', display: { labelParams: {} } } },
        }
        const message: AgentConversationMessage = { role: 'assistant', content: text, streamingContent: text,
          commentary: text, canonicalOutput }
        assert.equal(getAgentProcessingLabel(message), '正在思考')
        assert.equal(getAgentProcessingLabel({ ...message, canonicalOutput: {
          ...canonicalOutput, finalStreamStatus: 'open',
        } }), '正在生成回复')
        assert.equal(getAgentProcessingLabel({ ...message, canonicalOutput: {
          ...canonicalOutput, runTerminal: true,
        } }), '')
      }
    }
  }
})

test('standby does not reuse a plan, previous invocation, or tool row title', () => {
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    toolCalling: true,
    canonicalOutput: {
      ...initialCanonicalOutputState(),
      commentaryBlocks: [{
        outputStreamId: 'previous-commentary',
        invocationId: 'previous-invocation',
        firstSequence: 1,
        lastSequence: 1,
        startedAt: '2026-08-12T00:00:00Z',
        text: '核对上一步资料',
        committed: true,
        aborted: false,
      }],
      agentProgress: [{
        eventId: 'previous-progress',
        outputStreamId: 'previous-progress-stream',
        invocationId: 'previous-invocation',
        sourceChunkIndex: 1,
        text: '核对上一步资料',
        sequence: 1,
        occurredAt: '2026-08-12T00:00:00Z',
      }],
      operationOrder: ['current-model'],
      operations: {
        'current-model': {
          operationId: 'current-model',
          runId: 'run-1',
          invocationId: 'current-invocation',
          kind: 'model',
          firstSequence: 2,
          status: 'running',
          startedAt: '2026-08-12T00:00:01Z',
          display: { labelParams: {} },
        },
      },
    },
    taskPlan: {
      title: '审阅剧本',
      status: 'running',
      steps: [{
        id: 'review',
        title: '检查人物关系',
        type: 'review',
        status: 'running',
      }],
    },
    toolCallSegments: [{
      labels: ['读取人物资料', '读取场景资料'],
      commentaryBlockIndex: null,
      completedToolCount: 1,
    }],
  }

  assert.equal(getAgentProcessingLabel(message), '正在思考')
})

test('public commentary streams paragraphs without the status-title length filter', () => {
  const body = '先检查证据。\n\n- ' + '保留公开分析说明。'.repeat(45)
  let state = initialCanonicalOutputState()
  const event: CanonicalOutputEvent = {
    eventId: 'paragraph-1', runId: 'run-1', turnId: 'turn-1', invocationId: 'invocation-1',
    outputStreamId: 'output-1', sequence: 1, source: 'provider', kind: 'provider.content_delta',
    channel: 'commentary', visibility: 'public', payload: { delta: body },
    occurredAt: '2026-09-06T00:00:00Z', emittedAt: '2026-09-06T00:00:00Z',
  }
  state = reduceCanonicalOutput(state, event)
  const message = { role: 'assistant' as const, content: '', canonicalOutput: state }
  const parts = buildAssistantTimeline(message, { messageIndex: 0, isStreaming: true })
  assert.equal(parts[0]?.type, 'commentary')
  assert.equal(parts[0]?.type === 'commentary' ? parts[0].md : '', body)
  assert.deepEqual(reduceCanonicalOutput(state, event), state)
  const next = reduceCanonicalOutput(state, { ...event, eventId: 'paragraph-2', sequence: 2, payload: { delta: '\n继续核对。' } })
  const growing = buildAssistantTimeline({ ...message, canonicalOutput: next }, { messageIndex: 0, isStreaming: true })
  assert.equal(growing[0]?.type === 'commentary' ? growing[0].md : '', body + '\n继续核对。')
  assert.equal(growing[0]?.type === 'commentary' ? growing[0].regionKey : '', parts[0]?.type === 'commentary' ? parts[0].regionKey : '')
})
