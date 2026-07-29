'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, 'streamOptions.ts'))
const { handleDelta, handleThinkingDelta } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/streaming.ts'),
)
const { handleToolCallsInProgress } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/toolStart.ts'),
)
const { handleAgentDelegation } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/agentRun.ts'),
)
const { handleContextBudget, handleContextCompaction } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/context.ts'),
)
const {
  EMPTY_RESPONSE_MESSAGE,
  handleDone,
  MANUAL_ABORT_MESSAGE,
} = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/terminal.ts'),
)
const {
  countQueuedForSession,
  getSessionActivityLabel,
  getSettledSessionActivity,
} = loadTypeScriptModule(
  path.join(__dirname, 'chatQueue.ts'),
)
const {
  buildAssistantTimeline,
  getAssistantProcessingLabel,
} = loadTypeScriptModule(
  path.join(__dirname, '../components/ChatMessageList/assistantTimeline.ts'),
)
const {
  clearChatRuntime,
  getChatRuntimeQueue,
  getChatSessionRuntime,
  replaceChatRuntimeMessages,
  replaceChatRuntimeQueue,
  setChatRuntimeActivity,
  setChatRuntimeLoading,
  setChatRuntimeStreamId,
  updateChatRuntimeMessages,
} = loadTypeScriptModule(
  path.join(__dirname, 'chatRuntimeStore.ts'),
)
const {
  calculateContextUsage,
} = loadTypeScriptModule(
  path.join(__dirname, '../contextUsage.ts'),
)
const {
  getActiveTaskPlan,
  getVisibleTaskPlanSteps,
  shouldShowTaskPlan,
} = loadTypeScriptModule(
  path.join(__dirname, '../taskPlanSelection.ts'),
)
const { isSynthesizedToolOnlyResponse } = loadTypeScriptModule(
  path.join(__dirname, 'chatHistory.ts'),
)

test('built-in selection sends its model profile while custom models stay generic', () => {
  const builtIn = buildStreamOptions({
    cfg: {
      id: 'minimax',
      presetId: 'minimax:MiniMax-M3',
      name: 'MiniMax-M3',
      apiKey: 'secret',
      baseUrl: 'https://api.minimaxi.com/v1',
      supportsThinking: true,
      thinkingOnly: true,
      thinkingEnabled: true,
      customizeTemperature: false,
      contextWindow: '256k',
    },
    modelConfigs: { minimax: { max_tokens: 4096 } },
    selectedModel: 'minimax',
  })
  assert.equal(builtIn.options.model_profile, 'minimax:MiniMax-M3')
  assert.deepEqual(builtIn.options.thinking, { type: 'enabled' })
  assert.equal(builtIn.options.context_window, '256k')

  const custom = buildStreamOptions({
    cfg: {
      id: 'custom',
      name: 'custom-model',
      apiKey: 'secret',
      baseUrl: 'https://proxy.example/v1',
      supportsThinking: false,
      thinkingOnly: false,
      thinkingEnabled: false,
      customizeTemperature: false,
    },
    modelConfigs: {},
    selectedModel: 'custom',
  })
  assert.equal(Object.hasOwn(custom.options, 'model_profile'), false)
})

test('thinking SSE deltas become visible thinking blocks before answer text', () => {
  let conversations = [{ role: 'assistant', content: '', thinking: '' }]
  const acc = {
    thinking: '',
    response: '',
    thinkingBlocks: [],
    thinkingDurationsMs: [],
    toolCallSegments: [],
  }
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleThinkingDelta({ thinkingDelta: '模型思考内容' }, ctx)
  assert.equal(acc.thinking, '模型思考内容')
  assert.equal(conversations[0].thinking, '模型思考内容')

  handleDelta({ delta: '最终答案' }, ctx)
  assert.equal(conversations[0].content, '最终答案')
  assert.deepEqual(conversations[0].thinkingBlocks, ['模型思考内容'])
})

test('thinking and tool calls keep their actual interleaved order', () => {
  let conversations = [{ role: 'assistant', content: '', thinking: '' }]
  const acc = {
    thinking: '',
    response: '',
    thinkingBlocks: [],
    thinkingDurationsMs: [],
    toolCallSegments: [],
    contentAfterToolCalls: '',
  }
  const ctx = {
    acc,
    writingChapters: [],
    availableOutlines: [],
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }
  const toolChunk = (name) => ({
    toolCalls: [{
      id: `${name}-call`,
      function: { name, arguments: '{}' },
    }],
    toolCallsInProgress: true,
    partialContent: '',
    partialThinking: '',
  })

  handleToolCallsInProgress(toolChunk('listBookCharacters'), ctx)
  handleThinkingDelta({ thinkingDelta: '读取列表后继续判断' }, ctx)
  handleToolCallsInProgress(toolChunk('getBookCharacters'), ctx)

  assert.equal(
    conversations[0].toolCallSegments[0].thinkingBlockIndex,
    null,
  )
  assert.equal(
    conversations[0].toolCallSegments[1].thinkingBlockIndex,
    0,
  )
  assert.deepEqual(
    buildAssistantTimeline(conversations[0], { messageIndex: 0 })
      .map((part) => part.type),
    ['tools', 'thinking', 'tools'],
  )
})

test('legacy missing tool-round thinking stays after all recorded tools', () => {
  const timeline = buildAssistantTimeline({
    role: 'assistant',
    content: '最终答复',
    contentAfterToolCalls: '最终答复',
    thinkingBlocks: ['两个工具完成后的最终思考'],
    toolCallSegments: [
      { textBefore: '', labels: ['查看人物列表'] },
      { textBefore: '', labels: ['查看人物信息'] },
    ],
  }, { messageIndex: 0 })

  assert.deepEqual(
    timeline.map((part) => part.type),
    ['tools', 'tools', 'thinking', 'text'],
  )
})

test('delegation lifecycle chunks update the visible assistant work log', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleAgentDelegation({
    agentDelegationCreated: {
      runId: 'parent-1',
      delegationId: 'delegation-1',
      parentRunId: 'parent-1',
      rootRunId: 'parent-1',
      childRunId: null,
      agentRole: 'researcher',
      agentTitle: '资料核验 Agent',
      objective: 'collect evidence',
      status: 'queued',
      required: true,
      priority: 1,
    },
  }, ctx)
  handleAgentDelegation({
    agentDelegationUpdated: {
      runId: 'parent-1',
      delegationId: 'delegation-1',
      parentRunId: 'parent-1',
      rootRunId: 'parent-1',
      childRunId: 'child-1',
      agentRole: 'researcher',
      agentTitle: '资料核验 Agent',
      objective: 'collect evidence',
      status: 'done',
      required: true,
      priority: 1,
      resultSummary: 'three verified facts',
    },
  }, ctx)

  assert.equal(acc.agentRunId, 'parent-1')
  assert.equal(acc.delegations.length, 1)
  assert.equal(acc.delegations[0].status, 'done')
  assert.equal(acc.delegations[0].childRunId, 'child-1')
  assert.equal(acc.delegations[0].agentTitle, '资料核验 Agent')
  assert.equal(conversations[0].delegations.length, 1)
  assert.equal(conversations[0].delegations[0].resultSummary, 'three verified facts')
})

test('context lifecycle chunks update the visible assistant work log', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleContextCompaction({
    contextCompaction: {
      status: 'running',
      selectedTurnCount: 4,
    },
  }, ctx)
  handleContextBudget({
    contextBudget: {
      windowTokens: 200000,
      estimatedInputTokens: 12000,
      toolSchemaTokens: 1000,
      outputReserveTokens: 8000,
      safetyReserveTokens: 1000,
      runtimeReserveTokens: 1000,
      droppedMessages: 0,
      projectedTotalTokens: 23000,
      overflowTokens: 0,
    },
  }, ctx)
  handleContextBudget({
    contextBudget: {
      actualInputTokens: 12500,
      actualOutputTokens: 500,
      actualTotalTokens: 13000,
      actualUsageRound: 1,
      usageSource: 'provider',
    },
  }, ctx)

  assert.equal(acc.contextCompaction.status, 'running')
  assert.equal(conversations[0].contextCompaction.selectedTurnCount, 4)
  assert.equal(acc.contextBudget.estimatedInputTokens, 12000)
  assert.equal(acc.contextBudget.actualInputTokens, 12500)
  assert.equal(acc.contextBudget.windowTokens, 200000)
  assert.equal(conversations[0].contextBudget.toolSchemaTokens, 1000)
})

test('context indicator hides backend estimates when actual usage is unavailable', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'last answer',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
      },
    }],
    windowTokens: 200000,
  })

  assert.equal(usage, null)
})

test('context indicator uses provider input exactly and excludes local text', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'last answer',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        actualInputTokens: 1234,
      },
    }],
    windowTokens: 200000,
  })

  assert.equal(usage.usedTokens, 1234)
})

test('context indicator keeps the latest actual value until a new one arrives', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        actualInputTokens: 1234,
      },
    },
    { role: 'user', content: 'new question' },
    {
      role: 'assistant',
      content: '',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 50000,
        toolSchemaTokens: 10000,
      },
    }],
    windowTokens: 200000,
  })

  assert.equal(usage.usedTokens, 1234)
})

test('first request stays hidden until provider usage is available', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'first question' },
      {
        role: 'assistant',
        content: '',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
        },
      },
    ],
    windowTokens: 200000,
  })

  assert.equal(usage, null)
})

test('active request switches to actual usage once the provider reports it', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'new question' },
      {
        role: 'assistant',
        content: 'partial answer',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
          actualInputTokens: 1234,
        },
      },
    ],
    windowTokens: 200000,
  })

  assert.equal(usage.usedTokens, 1234)
})

test('task header does not reuse a completed plan from the previous turn', () => {
  const completedPlan = {
    title: 'previous task',
    status: 'done',
    steps: [
      { id: '1', title: 'first', type: 'analyze', status: 'done' },
      { id: '2', title: 'second', type: 'review', status: 'done' },
    ],
  }
  const conversations = [
    { role: 'user', content: 'previous question' },
    { role: 'assistant', content: 'previous answer', taskPlan: completedPlan },
    { role: 'user', content: 'new question' },
    { role: 'assistant', content: '' },
  ]

  assert.equal(getActiveTaskPlan(conversations, true), undefined)

  const currentPlan = {
    title: 'current task',
    status: 'running',
    steps: [
      {
        id: 'read',
        title: 'read context',
        type: 'read',
        executor: 'tool',
        status: 'done',
      },
      {
        id: 'analyze',
        title: 'analyze evidence',
        type: 'analyze',
        executor: 'model',
        status: 'running',
      },
      {
        id: 'review',
        title: 'review result',
        type: 'review',
        executor: 'model',
        status: 'pending',
      },
      {
        id: 'respond',
        title: 'Respond',
        type: 'review',
        status: 'pending',
      },
    ],
  }
  conversations[3] = { ...conversations[3], taskPlan: currentPlan }
  assert.equal(getActiveTaskPlan(conversations, true), currentPlan)
  assert.deepEqual(
    getVisibleTaskPlanSteps(currentPlan).map((step) => step.id),
    ['read', 'analyze', 'review'],
  )
  assert.equal(shouldShowTaskPlan(currentPlan), true)
  assert.equal(getActiveTaskPlan(conversations, false), undefined)

  const shortPlan = {
    ...currentPlan,
    steps: currentPlan.steps.slice(1),
  }
  conversations[3] = { ...conversations[3], taskPlan: shortPlan }
  assert.equal(getVisibleTaskPlanSteps(shortPlan).length, 2)
  assert.equal(shouldShowTaskPlan(shortPlan), false)
  assert.equal(getActiveTaskPlan(conversations, true), undefined)
})

test('manual abort replaces an empty response with an explicit notice', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  let loading = true
  let cleanedUp = false
  const acc = {
    response: '',
    thinking: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'stop this response',
    model: '',
    turnStartedAt: performance.now(),
    thinkingBlocks: [],
    thinkingDurationsMs: [],
  }
  const ctx = {
    acc,
    cfg: {},
    apiModelName: 'test-model',
    isVisibleSession: () => true,
    flushCommits: () => {},
    setConversations: (updater) => {
      conversations = updater(conversations)
    },
    setLoading: (next) => {
      loading = next
    },
    cleanup: () => {
      cleanedUp = true
    },
  }

  assert.equal(handleDone({ done: true, aborted: true }, ctx), true)
  assert.equal(conversations[0].content, MANUAL_ABORT_MESSAGE)
  assert.equal(acc.response, MANUAL_ABORT_MESSAGE)
  assert.equal(loading, false)
  assert.equal(cleanedUp, true)
})

test('completed stream without visible model content becomes an explicit failure', () => {
  let conversations = [{
    role: 'assistant',
    content: '',
    toolCallSegments: [{
      textBefore: '',
      labels: ['查看人物列表'],
    }],
  }]
  let loading = true
  let outcome
  const acc = {
    response: '',
    thinking: 'internal reasoning only',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'analyze the characters',
    model: '',
    turnStartedAt: performance.now(),
    toolCallSegments: conversations[0].toolCallSegments,
    contentAfterToolCalls: '',
    thinkingBlocks: [],
    thinkingDurationsMs: [],
  }
  const ctx = {
    acc,
    cfg: {},
    apiModelName: 'test-model',
    isVisibleSession: () => true,
    flushCommits: () => {},
    setConversations: (updater) => {
      conversations = updater(conversations)
    },
    setLoading: (next) => {
      loading = next
    },
    cleanup: (nextOutcome) => {
      outcome = nextOutcome
    },
  }

  assert.equal(handleDone({ done: true }, ctx), true)
  assert.equal(conversations[0].content, EMPTY_RESPONSE_MESSAGE)
  assert.equal(conversations[0].isError, true)
  assert.equal(acc.response, EMPTY_RESPONSE_MESSAGE)
  assert.equal(loading, false)
  assert.equal(outcome, 'failed')
})

test('persisted tool-only placeholder is distinguishable from a real answer', () => {
  const toolCallSegments = [{
    textBefore: '',
    labels: ['查看人物列表'],
  }]
  assert.equal(isSynthesizedToolOnlyResponse({
    role: 'assistant',
    content: '[已调用工具] 查看人物列表',
    toolCallSegments,
  }), true)
  assert.equal(isSynthesizedToolOnlyResponse({
    role: 'assistant',
    content: '人物设定确实存在层级过多的问题。',
    toolCallSegments,
  }), false)
})

test('queued chat activity stays pending until the final queued turn completes', () => {
  const queue = [
    {
      content: 'second question',
      sessionId: 7,
      selectedModel: 'test',
      selectedModelConfig: {},
      agentEnabled: false,
      associatedChapterIds: [],
      associatedOutlineIds: [],
      selectedMemoryIds: [],
      selectedForeshadowingIds: [],
    },
    {
      content: 'third question',
      sessionId: 7,
      selectedModel: 'test',
      selectedModelConfig: {},
      agentEnabled: false,
      associatedChapterIds: [],
      associatedOutlineIds: [],
      selectedMemoryIds: [],
      selectedForeshadowingIds: [],
    },
    {
      content: 'another session',
      sessionId: 8,
      selectedModel: 'test',
      selectedModelConfig: {},
      agentEnabled: false,
      associatedChapterIds: [],
      associatedOutlineIds: [],
      selectedMemoryIds: [],
      selectedForeshadowingIds: [],
    },
  ]

  const queuedCount = countQueuedForSession(queue, 7)
  assert.equal(queuedCount, 2)
  assert.deepEqual(
    getSettledSessionActivity('completed', queuedCount),
    { state: 'queued', queuedCount: 2 },
  )
  assert.equal(
    getSessionActivityLabel({ state: 'running', queuedCount }),
    '生成中 · 2 条排队',
  )
  assert.deepEqual(
    getSettledSessionActivity('completed', 0),
    { state: 'completed', queuedCount: 0 },
  )
  assert.equal(
    getSessionActivityLabel({ state: 'completed', queuedCount: 0 }),
    '已完成',
  )
})

test('assistant processing label follows the actual runtime phase', () => {
  assert.equal(
    getAssistantProcessingLabel({ role: 'assistant', content: '' }),
    '正在理解请求并准备处理',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      contextCompaction: { status: 'running' },
    }),
    '正在整理对话上下文',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      thinking: 'reasoning',
    }),
    '正在推演处理方案',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      taskPlan: {
        title: 'plan',
        status: 'running',
        steps: [{
          id: '1',
          title: 'step',
          type: 'analyze',
          status: 'running',
        }],
      },
    }),
    '正在推进任务步骤',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: 'tool commentary',
      toolCalling: true,
    }),
    '正在执行必要操作',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: 'draft',
      contentAfterToolCalls: 'final answer',
    }),
    '正在组织回复内容',
  )
})

test('chat runtime keeps concurrent sessions isolated across panel lifecycles', () => {
  const firstSessionId = 91001
  const secondSessionId = 91002
  replaceChatRuntimeMessages(firstSessionId, [
    { role: 'user', content: 'first request' },
    { role: 'assistant', content: '' },
  ])
  replaceChatRuntimeMessages(secondSessionId, [
    { role: 'user', content: 'second request' },
    { role: 'assistant', content: '' },
  ])
  setChatRuntimeLoading(firstSessionId, true)
  setChatRuntimeLoading(secondSessionId, true)
  setChatRuntimeStreamId(firstSessionId, 'stream-first')
  setChatRuntimeStreamId(secondSessionId, 'stream-second')

  updateChatRuntimeMessages(firstSessionId, (messages) => [
    messages[0],
    { ...messages[1], content: 'first response' },
  ])
  setChatRuntimeLoading(firstSessionId, false)
  setChatRuntimeActivity(firstSessionId, {
    state: 'completed',
    queuedCount: 0,
  })

  assert.equal(
    getChatSessionRuntime(firstSessionId).messages[1].content,
    'first response',
  )
  assert.equal(getChatSessionRuntime(firstSessionId).loading, false)
  assert.equal(getChatSessionRuntime(firstSessionId).streamId, 'stream-first')
  assert.equal(getChatSessionRuntime(secondSessionId).messages[1].content, '')
  assert.equal(getChatSessionRuntime(secondSessionId).loading, true)
  assert.equal(getChatSessionRuntime(secondSessionId).streamId, 'stream-second')

  replaceChatRuntimeQueue([
    {
      content: 'queued first follow-up',
      sessionId: firstSessionId,
      selectedModel: 'test',
      selectedModelConfig: {},
      agentEnabled: false,
      associatedChapterIds: [],
      associatedOutlineIds: [],
      selectedMemoryIds: [],
      selectedForeshadowingIds: [],
    },
    {
      content: 'queued second follow-up',
      sessionId: secondSessionId,
      selectedModel: 'test',
      selectedModelConfig: {},
      agentEnabled: false,
      associatedChapterIds: [],
      associatedOutlineIds: [],
      selectedMemoryIds: [],
      selectedForeshadowingIds: [],
    },
  ])
  clearChatRuntime(firstSessionId)
  assert.equal(getChatSessionRuntime(firstSessionId), undefined)
  assert.equal(getChatSessionRuntime(secondSessionId).loading, true)
  assert.deepEqual(
    getChatRuntimeQueue().map((item) => item.sessionId),
    [secondSessionId],
  )

  clearChatRuntime(secondSessionId)
})
