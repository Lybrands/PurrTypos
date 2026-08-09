'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, 'streamOptions.ts'))
const {
  handleDelta,
  handleThinkingDelta,
  handleThinkingSnapshot,
} = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/streaming.ts'),
)
const { handleToolCallsInProgress } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/toolStart.ts'),
)
const {
  handleAgentDelegation,
  handleAgentRunTerminal,
  handleLongTaskDispatched,
} = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/agentRun.ts'),
)
const { handleContextBudget, handleContextCompaction } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/context.ts'),
)
const { handleAgentSubRunEvent } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/subAgent.ts'),
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
  getTaskPlanProgress,
  getVisibleTaskPlanSteps,
  shouldShowTaskPlan,
} = loadTypeScriptModule(
  path.join(__dirname, '../taskPlanSelection.ts'),
)
const { isSynthesizedToolOnlyResponse } = loadTypeScriptModule(
  path.join(__dirname, 'chatHistory.ts'),
)
const {
  KNOWN_TOOL_CALL_LABELS,
  resolveLocalizedToolDisplayName,
  toolCallDisplayRow,
} = loadTypeScriptModule(path.join(__dirname, 'toolCallLabels.ts'))

test('legacy tool label fallback remains localized for persisted sessions', () => {
  for (const name of Object.keys(KNOWN_TOOL_CALL_LABELS)) {
    const row = toolCallDisplayRow(name, {}, [], [])
    assert.notEqual(row.label, name)
    assert.match(row.label, /[\u3400-\u9fff]/)
  }
})

test('backend display names resolve by locale and override legacy fallback', () => {
  const displayNames = {
    'zh-CN': '读取原作人物',
    'en-US': 'Read Source Characters',
  }
  assert.equal(
    resolveLocalizedToolDisplayName(displayNames, 'zh-Hans-CN'),
    '读取原作人物',
  )
  assert.equal(
    resolveLocalizedToolDisplayName(displayNames, 'en-GB'),
    'Read Source Characters',
  )
  assert.equal(
    toolCallDisplayRow('newBackendTool', {}, [], [], '新的后端工具').label,
    '新的后端工具',
  )
})

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
    selectedModel: 'minimax',
  })
  assert.equal(builtIn.options.model_profile, 'minimax:MiniMax-M3')
  assert.deepEqual(builtIn.options.thinking, { type: 'enabled' })
  assert.equal(builtIn.options.context_window, '256k')
  assert.equal(Object.hasOwn(builtIn.options, 'max_tokens'), false)

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
    selectedModel: 'custom',
  })
  assert.equal(Object.hasOwn(custom.options, 'model_profile'), false)
  assert.equal(Object.hasOwn(custom.options, 'max_tokens'), false)
})

test('renderer never sends its legacy output budget to Agent Core', () => {
  const configured = buildStreamOptions({
    cfg: {
      id: 'mimo',
      presetId: 'mimo:mimo-v2.5-pro',
      name: 'mimo-v2.5-pro',
      apiKey: 'secret',
      baseUrl: 'https://api.xiaomimimo.com/v1',
      supportsThinking: true,
      thinkingOnly: false,
      outputTokenBudget: 999_999,
    },
    selectedModel: 'mimo',
  })

  assert.equal(Object.hasOwn(configured.options, 'max_tokens'), false)
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

test('a replay snapshot replaces live thinking instead of duplicating it', () => {
  let conversations = [{ role: 'assistant', content: '', thinking: '部分思考' }]
  const acc = {
    thinking: '部分思考',
    response: '',
    thinkingBlocks: [],
    thinkingDurationsMs: [],
  }
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleThinkingSnapshot({ thinkingSnapshot: '完整思考内容' }, ctx)

  assert.equal(acc.thinking, '完整思考内容')
  assert.equal(conversations[0].thinking, '完整思考内容')
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

test('interleaved child run deltas stay isolated by delegation', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {
    response: '',
    thinking: '',
    sessionId: 1,
    needsTitle: false,
    userText: 'coordinate',
    model: 'test-model',
    turnStartedAt: performance.now(),
  }
  const setConversations = (next) => {
    conversations = typeof next === 'function' ? next(conversations) : next
  }
  const ctx = {
    acc,
    sessionId: 1,
    cfg: {},
    apiModelName: 'test-model',
    writingChapters: [],
    availableOutlines: [],
    setConversations,
    scheduleCommit: setConversations,
    flushCommits: () => {},
    setLoading: () => {},
    setSessions: () => {},
    appMessage: {},
    isVisibleSession: () => true,
    cleanup: () => {},
  }
  const dispatchNested = (chunk, childCtx) => {
    handleDelta(chunk, childCtx)
  }
  const emit = (delegationId, childRunId, delta) => {
    handleAgentSubRunEvent({
      agentSubRunEvent: {
        runId: 'parent-1',
        parentRunId: 'parent-1',
        rootRunId: 'parent-1',
        delegationId,
        childRunId,
        agentRole: 'screenplay-writer',
        agentTitle: `Writer ${delegationId}`,
        objective: `write ${delegationId}`,
        chunk: { delta },
      },
    }, ctx, dispatchNested)
  }

  emit('a', 'child-a', 'A1')
  emit('b', 'child-b', 'B1')
  emit('a', 'child-a', 'A2')

  const activities = conversations[0].subAgentActivities
  assert.equal(activities.length, 2)
  assert.equal(
    activities.find((item) => item.delegationId === 'a').message.content,
    'A1A2',
  )
  assert.equal(
    activities.find((item) => item.delegationId === 'b').message.content,
    'B1',
  )
  assert.equal(
    acc.subAgentActivities.find((item) => item.delegationId === 'a').message.content,
    'A1A2',
  )
  assert.equal(acc.response, '')
})

test('context lifecycle chunks update the visible assistant work log', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = {
    acc,
    cfg: { id: 'model-test' },
    apiModelName: 'test-model',
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
  assert.equal(acc.contextBudget.modelConfigId, 'model-test')
  assert.equal(acc.contextBudget.modelName, 'test-model')
  assert.equal(conversations[0].contextBudget.toolSchemaTokens, 1000)
})

test('a newly prepared request clears the previous provider usage snapshot', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = {
    acc,
    cfg: { id: 'model-test' },
    apiModelName: 'test-model',
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  const preparedBudget = (estimatedInputTokens) => ({
    windowTokens: 200000,
    estimatedInputTokens,
    toolSchemaTokens: 1000,
    outputReserveTokens: 8000,
    safetyReserveTokens: 1000,
    runtimeReserveTokens: 1000,
    droppedMessages: 0,
    projectedTotalTokens: estimatedInputTokens + 11000,
    overflowTokens: 0,
  })

  handleContextBudget({
    contextBudget: preparedBudget(12000),
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
  handleContextBudget({
    contextBudget: preparedBudget(18000),
  }, ctx)

  assert.equal(acc.contextBudget.estimatedInputTokens, 18000)
  assert.equal(acc.contextBudget.actualInputTokens, undefined)
  assert.equal(conversations[0].contextBudget.actualInputTokens, undefined)
})

test('context indicator uses the final prepared input estimate including tool schemas', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.deepEqual(usage, {
    usedTokens: 13000,
    windowTokens: 200000,
    inputCapacityTokens: 192000,
    outputReserveTokens: 8000,
    ratio: 13000 / 200000,
    source: 'estimate',
  })
})

test('context indicator uses provider input as the current-context baseline', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        actualInputTokens: 1234,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.equal(usage.usedTokens, 1234)
  assert.equal(usage.inputCapacityTokens, 192000)
  assert.equal(usage.source, 'provider')
})

test('a newly prepared request replaces the previous provider usage', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'test-model',
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
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 50000,
        toolSchemaTokens: 10000,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.equal(usage.usedTokens, 60000)
  assert.equal(usage.source, 'estimate')
})

test('first request exposes its prepared input estimate before provider usage', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'first question' },
      {
        role: 'assistant',
        content: '',
        model: 'test-model',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
          outputReserveTokens: 8000,
        },
      },
    ],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.deepEqual(usage, {
    usedTokens: 60000,
    windowTokens: 200000,
    inputCapacityTokens: 192000,
    outputReserveTokens: 8000,
    ratio: 60000 / 200000,
    source: 'estimate',
  })
})

test('switching to a different model window keeps the current conversation estimate', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'previous-model',
      contextBudget: {
        windowTokens: 256000,
        actualInputTokens: 18921,
      },
    }],
    windowTokens: 1000000,
    modelName: 'deepseek-v4-pro',
  })

  assert.ok(usage.usedTokens > 18921)
  assert.equal(usage.windowTokens, 1000000)
  assert.equal(usage.inputCapacityTokens, 1000000)
  assert.equal(usage.outputReserveTokens, 0)
  assert.equal(usage.source, 'estimate')
})

test('models with the same window keep usage but do not claim provider calibration', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'glm-5.2',
      contextBudget: {
        windowTokens: 1000000,
        estimatedInputTokens: 20000,
        toolSchemaTokens: 5000,
        actualInputTokens: 24000,
      },
    }],
    windowTokens: 1000000,
    modelConfigId: 'builtin_deepseek_deepseek_v4_pro',
    modelName: 'deepseek-v4-pro',
  })

  assert.ok(usage.usedTokens > 24000)
  assert.equal(usage.windowTokens, 1000000)
  assert.equal(usage.inputCapacityTokens, 1000000)
  assert.equal(usage.outputReserveTokens, 0)
  assert.equal(usage.source, 'estimate')
})

test('active request switches to actual usage once the provider reports it', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'new question' },
      {
        role: 'assistant',
        content: 'partial answer',
        model: 'test-model',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
          actualInputTokens: 1234,
          outputReserveTokens: 8000,
        },
      },
    ],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.ok(usage.usedTokens > 1234)
  assert.equal(usage.source, 'provider')
})

test('current input draft is included before the request is sent', () => {
  const baseParams = {
    messages: [
      { role: 'user', content: '已有问题' },
      { role: 'assistant', content: '已有回答' },
    ],
    windowTokens: 1000000,
    modelName: 'deepseek-v4-pro',
  }
  const withoutDraft = calculateContextUsage(baseParams)
  const withDraft = calculateContextUsage({
    ...baseParams,
    draft: '这是输入框里尚未发送的新问题',
  })

  assert.ok(withDraft.usedTokens > withoutDraft.usedTokens)
  assert.equal(withDraft.source, 'estimate')
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
        id: 'private-begin',
        title: 'begin private artifact',
        type: 'write',
        executor: 'tool',
        status: 'done',
        protocolPrivate: true,
      },
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
    steps: currentPlan.steps.slice(2),
  }
  conversations[3] = { ...conversations[3], taskPlan: shortPlan }
  assert.equal(getVisibleTaskPlanSteps(shortPlan).length, 2)
  assert.equal(shouldShowTaskPlan(shortPlan), false)
  assert.equal(getActiveTaskPlan(conversations, true), undefined)
})

test('task progress distinguishes active step number from completed count', () => {
  const progress = getTaskPlanProgress({
    title: 'execute plan',
    status: 'running',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'done' },
      { id: 'two', title: 'two', type: 'analyze', status: 'done' },
      { id: 'three', title: 'three', type: 'write', status: 'running' },
      { id: 'four', title: 'four', type: 'review', status: 'pending' },
      { id: 'respond', title: 'Respond', type: 'review', status: 'pending' },
    ],
  })

  assert.equal(progress.completed, 2)
  assert.equal(progress.total, 4)
  assert.equal(progress.currentStep.id, 'three')
  assert.equal(progress.currentStepNumber, 3)
  assert.deepEqual(progress.runningSteps.map((step) => step.id), ['three'])
  assert.equal(progress.percent, 50)
})

test('task progress exposes concurrent Planner Agent steps as one frontier', () => {
  const progress = getTaskPlanProgress({
    title: 'parallel plan',
    status: 'running',
    steps: [
      { id: 'write-5', title: 'write 5', type: 'write', executor: 'agent', status: 'running' },
      { id: 'write-6', title: 'write 6', type: 'write', executor: 'agent', status: 'running' },
      { id: 'write-7', title: 'write 7', type: 'write', executor: 'agent', status: 'running' },
      { id: 'submit', title: 'submit', type: 'write', executor: 'tool', status: 'pending' },
    ],
  })

  assert.deepEqual(
    progress.runningSteps.map((step) => step.id),
    ['write-5', 'write-6', 'write-7'],
  )
  assert.equal(progress.currentStep.id, 'write-5')
  assert.equal(progress.completed, 0)
  assert.equal(progress.total, 4)
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

test('manual abort preserves partial output and exposes a separate terminal status', () => {
  let conversations = [{ role: 'assistant', content: '已经完成一部分' }]
  const acc = {
    response: '已经完成一部分',
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
    setLoading: () => {},
    cleanup: () => {},
  }

  assert.equal(handleDone({ done: true, aborted: true }, ctx), true)
  assert.equal(conversations[0].content, '已经完成一部分')
  assert.equal(conversations[0].termination, MANUAL_ABORT_MESSAGE)
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

test('deterministic run completion text becomes the visible assistant answer', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {
    response: '',
    thinking: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'continue screenplay',
    model: '',
    turnStartedAt: performance.now(),
    thinkingBlocks: [],
    thinkingDurationsMs: [],
  }
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleAgentRunTerminal({
    agentRunCompleted: {
      runId: 'run-host-result',
      status: 'done',
      finalResponse: '当前阶段已经变化，请刷新后重试。',
    },
  }, ctx)

  assert.equal(acc.response, '当前阶段已经变化，请刷新后重试。')
  assert.equal(conversations[0].content, '当前阶段已经变化，请刷新后重试。')
})

test('durable root terminal uses the same final-answer path as an ordinary run', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  let outcome
  const acc = {
    response: '',
    thinking: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'write all remaining scenes',
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
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
    setConversations: (updater) => {
      conversations = updater(conversations)
    },
    setLoading: () => {},
    cleanup: (nextOutcome) => {
      outcome = nextOutcome
    },
  }

  handleLongTaskDispatched({
    longTaskDispatched: {
      runId: 'run-1',
      taskId: 'task-1',
      status: 'pending',
      totalUnits: 3,
      completedUnits: 0,
    },
  }, ctx)
  handleAgentRunTerminal({
    agentRunCompleted: {
      runId: 'run-1',
      status: 'done',
      finalResponse: '已恢复原有长篇正文任务，将从上次检查点继续。',
    },
  }, ctx)
  assert.equal(handleDone({ done: true }, ctx), true)
  assert.equal(acc.longTaskId, 'task-1')
  assert.equal(conversations[0].longTaskId, 'task-1')
  assert.equal(
    conversations[0].content,
    '已恢复原有长篇正文任务，将从上次检查点继续。',
  )
  assert.equal(conversations[0].isError, undefined)
  assert.equal(outcome, 'completed')
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
    '理解请求',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      contextCompaction: { status: 'running' },
    }),
    '整理上下文',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      thinking: 'reasoning',
    }),
    '推演方案',
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
    '推进任务',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: 'tool commentary',
      toolCalling: true,
    }),
    '执行操作',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: 'draft',
      contentAfterToolCalls: 'final answer',
    }),
    '组织回复',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      toolApprovals: [{ status: 'pending' }],
    }),
    '等待确认',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      delegations: [{ status: 'running' }],
    }),
    '协调任务',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      taskPlan: { title: 'plan', status: 'planned', steps: [] },
    }),
    '拆解任务',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      toolCallSegments: [{ labels: ['tool'], textBefore: '' }],
    }),
    '核对结果',
  )
  assert.equal(
    getAssistantProcessingLabel({
      role: 'assistant',
      content: '',
      contextBudget: {},
    }),
    '准备上下文',
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
