'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, 'streamOptions.ts'))
const {
  handleLongTaskDispatched,
  handleLongTaskProgress,
} = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/durableTask.ts'),
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
  getExecutionPanelLogKey,
  getExecutionPanelPresentation,
  getAssistantProcessingLabel,
  getOperationGroupProgress,
  groupConsecutiveWorkSteps,
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
const { projectContextBudget } = loadTypeScriptModule(
  path.join(__dirname, '../../../agent-runtime/contextBudgetProjection.ts'),
)
const {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
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

const loadWorkLogState = () => loadTypeScriptModule(
  path.join(__dirname, '../components/WorkLog/state.ts'),
)

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

test('renderer never sends its legacy output budget to PurrA', () => {
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

test('unassigned commentary stays after all recorded tools', () => {
  const message = {
    role: 'assistant',
    content: '最终答复',
    commentaryBlocks: ['两个工具完成后的公开说明'],
    toolCallSegments: [
      { commentaryBlockIndex: null, labels: ['查看人物列表'] },
      { commentaryBlockIndex: null, labels: ['查看人物信息'] },
    ],
  }
  const timeline = buildAssistantTimeline(message, { messageIndex: 0 })

  assert.deepEqual(
    timeline.map((part) => part.type),
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

test('active child timelines stream ordinary text without exposing the root answer', () => {
  const message = {
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

test('execution panel exists before the first operation and remains before a final answer', () => {
  assert.deepEqual(
    getExecutionPanelPresentation([], {
      isStreaming: true,
    }),
    {
      visible: true,
      active: true,
      autoOpen: false,
      stepCount: 0,
      title: '正在进行',
    },
  )

  const completedParts = [{
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
})

test('an empty historical Assistant turn does not invent an execution panel', () => {
  assert.deepEqual(
    getExecutionPanelPresentation([], {
      isStreaming: false,
    }),
    {
      visible: false,
      active: false,
      autoOpen: false,
      stepCount: 0,
      title: '用时',
    },
  )
})

test('execution panel keys stay turn-specific when separate sessions reuse an index', () => {
  const firstSessionTurn = {
    conversationId: 1201,
    clientTurnId: 'turn-session-a',
  }
  const secondSessionTurn = {
    conversationId: 1202,
    clientTurnId: 'turn-session-b',
  }

  assert.notEqual(
    getExecutionPanelLogKey(firstSessionTurn),
    getExecutionPanelLogKey(secondSessionTurn),
  )
  assert.equal(
    getExecutionPanelLogKey(firstSessionTurn),
    'client-turn-turn-session-a-work-log',
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

test('work log follows auto-open transitions before a manual choice', () => {
  const {
    applyWorkLogAutoOpen,
    getInitialWorkLogOpenState,
  } = loadWorkLogState()
  const initial = getInitialWorkLogOpenState(undefined, true)

  assert.deepEqual(initial, { open: true, manuallySet: false })
  assert.deepEqual(
    applyWorkLogAutoOpen(initial, true, false),
    { open: false, manuallySet: false },
  )
})

test('work log keeps a manual choice across later auto-open transitions', () => {
  const {
    applyWorkLogAutoOpen,
    toggleWorkLogOpenState,
  } = loadWorkLogState()
  const manuallyCollapsed = toggleWorkLogOpenState({
    open: true,
    manuallySet: false,
  })

  assert.deepEqual(manuallyCollapsed, { open: false, manuallySet: true })
  assert.deepEqual(
    applyWorkLogAutoOpen(manuallyCollapsed, false, true),
    { open: false, manuallySet: true },
  )
})

test('work log initializes each turn from its own stored or auto-open state', () => {
  const { getInitialWorkLogOpenState } = loadWorkLogState()

  assert.deepEqual(
    getInitialWorkLogOpenState(
      { open: false, manuallySet: true },
      true,
    ),
    { open: false, manuallySet: true },
  )
  assert.deepEqual(
    getInitialWorkLogOpenState(undefined, true),
    { open: true, manuallySet: false },
  )
  assert.deepEqual(
    getInitialWorkLogOpenState(undefined, false),
    { open: false, manuallySet: false },
  )
})

test('work log recent-state cache stays bounded and retains recently read turns', () => {
  const {
    readWorkLogOpenState,
    writeWorkLogOpenState,
  } = loadWorkLogState()
  const cache = new Map()

  writeWorkLogOpenState(cache, 'turn-a', { open: true, manuallySet: true }, 2)
  writeWorkLogOpenState(cache, 'turn-b', { open: false, manuallySet: true }, 2)
  assert.deepEqual(
    readWorkLogOpenState(cache, 'turn-a'),
    { open: true, manuallySet: true },
  )
  writeWorkLogOpenState(cache, 'turn-c', { open: true, manuallySet: false }, 2)

  assert.deepEqual([...cache.keys()], ['turn-a', 'turn-c'])
  assert.equal(readWorkLogOpenState(cache, 'turn-b'), undefined)
})

test('execution-panel progress reports the active visible step frontier', () => {
  const progress = getOperationGroupProgress([
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
  ])

  assert.deepEqual(progress, {
    total: 6,
    completed: 2,
    current: 3,
    active: true,
    parallel: false,
  })
})

test('execution-panel progress excludes cached tool rows', () => {
  const progress = getOperationGroupProgress([{
    type: 'tools',
    segmentIndex: 0,
    segment: {
      commentaryBlockIndex: null,
      labels: ['缓存读取', '真实读取'],
      cachedFlags: [true, false],
      completedToolCount: 2,
    },
  }])

  assert.deepEqual(progress, {
    total: 1,
    completed: 1,
    current: 1,
    active: false,
    parallel: false,
  })
})

test('consecutive operations stay as direct rows under the single execution panel', () => {
  assert.equal(typeof groupConsecutiveWorkSteps, 'function')
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

test('durable task progress stays out of the work log', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = {
    acc,
    isVisibleSession: () => true,
    scheduleCommit: (updater) => {
      conversations = updater(conversations)
    },
  }

  handleLongTaskProgress({
    longTaskProgress: {
      runId: 'turn-1',
      taskId: 'task-1',
      status: 'running',
      revision: 2,
      totalUnits: 2,
      completedUnits: 0,
      failedUnits: 0,
      units: [{
        id: 'generate',
        position: 0,
        title: '生成审阅报告',
        status: 'claimed',
        attempt: 1,
        maxAttempts: 2,
      }, {
        id: 'publish',
        position: 1,
        title: '整理并发布候选稿',
        status: 'pending',
        attempt: 0,
        maxAttempts: 1,
      }],
    },
  }, ctx)

  assert.equal(acc.longTaskId, 'task-1')
  assert.equal(conversations[0].longTaskId, 'task-1')
  assert.equal(conversations[0].longTaskProgress, undefined)
  assert.deepEqual(
    buildAssistantTimeline(conversations[0], { messageIndex: 0 })
      .map((part) => part.type),
    [],
  )
})

test('a newly prepared request clears the previous provider usage snapshot', () => {
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

  let budget = projectContextBudget(undefined, preparedBudget(12000), {
    configId: 'model-test',
    name: 'test-model',
  })
  budget = projectContextBudget(budget, {
      actualInputTokens: 12500,
      actualOutputTokens: 500,
      actualTotalTokens: 13000,
      actualUsageRound: 1,
      usageSource: 'provider',
  })
  budget = projectContextBudget(budget, preparedBudget(18000))

  assert.equal(budget.estimatedInputTokens, 18000)
  assert.equal(budget.actualInputTokens, undefined)
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

test('context indicator grows with the assistant content already streamed', () => {
  const baseParams = {
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        actualInputTokens: 1234,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  }
  const beforeStreaming = calculateContextUsage(baseParams)
  const afterStreaming = calculateContextUsage({
    ...baseParams,
    messages: [{
      ...baseParams.messages[0],
      streamingContent: '这是已经接收的流式回答',
    }],
  })

  assert.ok(afterStreaming.usedTokens > beforeStreaming.usedTokens)
  assert.equal(afterStreaming.source, 'provider')
})

test('current input draft is excluded until the request is sent', () => {
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

  assert.equal(withDraft.usedTokens, withoutDraft.usedTokens)
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
  assert.equal(getActiveTaskPlan(conversations, false), currentPlan)

  const shortPlan = {
    ...currentPlan,
    steps: currentPlan.steps.slice(2),
  }
  conversations[3] = { ...conversations[3], taskPlan: shortPlan }
  assert.equal(getVisibleTaskPlanSteps(shortPlan).length, 2)
  assert.equal(shouldShowTaskPlan(shortPlan), false)
  assert.equal(getActiveTaskPlan(conversations, true), undefined)
})

test('sequential task progress presents the current visible step ordinal', () => {
  const plan = {
    title: 'execute plan',
    status: 'running',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'done' },
      { id: 'two', title: 'two', type: 'analyze', status: 'pending' },
      { id: 'three', title: 'three', type: 'write', status: 'running' },
      { id: 'four', title: 'four', type: 'review', status: 'pending' },
      { id: 'respond', title: 'Respond', type: 'review', status: 'pending' },
    ],
  }
  const progress = getTaskPlanProgress(plan)

  assert.equal(progress.completed, 1)
  assert.equal(progress.total, 4)
  assert.equal(progress.currentStep.id, 'three')
  assert.equal(progress.currentStepIndex, 3)
  assert.deepEqual(progress.runningSteps.map((step) => step.id), ['three'])
  assert.equal(progress.percent, 25)
  assert.equal(getTaskPlanCountLabel(plan), '第 3/4 步')
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

test('parallel task progress presents concurrency and completed count', () => {
  const plan = {
    title: 'parallel plan',
    status: 'running',
    steps: [
      { id: 'read-1', title: 'read 1', type: 'read', status: 'done' },
      { id: 'read-2', title: 'read 2', type: 'read', status: 'done' },
      { id: 'write-3', title: 'write 3', type: 'write', status: 'running' },
      { id: 'write-4', title: 'write 4', type: 'write', status: 'running' },
      { id: 'write-5', title: 'write 5', type: 'write', status: 'running' },
      { id: 'submit', title: 'submit', type: 'write', status: 'pending' },
    ],
  }

  assert.equal(getTaskPlanCountLabel(plan), '并行 3 项 · 已完成 2/6')
})

test('planned task progress points at the next sequential step', () => {
  const plan = {
    title: 'planned task',
    status: 'planned',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'pending' },
      { id: 'two', title: 'two', type: 'analyze', status: 'pending' },
      { id: 'three', title: 'three', type: 'write', status: 'pending' },
      { id: 'four', title: 'four', type: 'review', status: 'pending' },
    ],
  }

  assert.equal(getTaskPlanCountLabel(plan), '第 1/4 步')
})

test('completed task capsule exists only while the final answer is streaming', () => {
  const plan = {
    title: 'completed task',
    status: 'done',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'done' },
      { id: 'two', title: 'two', type: 'analyze', status: 'done' },
      { id: 'three', title: 'three', type: 'write', status: 'done' },
      { id: 'four', title: 'four', type: 'review', status: 'done' },
    ],
  }
  const conversations = [{ role: 'assistant', content: '总结输出中', taskPlan: plan }]

  assert.equal(getTaskPlanCountLabel(plan), '已完成 4/4')
  assert.equal(getActiveTaskPlan(conversations, true), plan)
  assert.equal(getActiveTaskPlan(conversations, false), undefined)
  assert.equal(getActiveTaskPlan([
    ...conversations,
    { role: 'assistant', content: '新的历史消息' },
  ], true), undefined)
})

test('manual abort replaces an empty response with an explicit notice', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  let loading = true
  let cleanedUp = false
  const acc = {
    response: '',
    commentary: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'stop this response',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
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
  assert.equal(conversations[0].content, '')
  assert.equal(conversations[0].termination, MANUAL_ABORT_MESSAGE)
  assert.equal(acc.response, '')
  assert.equal(loading, false)
  assert.equal(cleanedUp, true)
})

test('manual abort preserves partial output and exposes a separate terminal status', () => {
  let conversations = [{ role: 'assistant', content: '已经完成一部分' }]
  const acc = {
    response: '已经完成一部分',
    commentary: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'stop this response',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
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
      commentaryBlockIndex: null,
      labels: ['查看人物列表'],
    }],
  }]
  let loading = true
  let outcome
  const acc = {
    response: '',
    commentary: 'internal reasoning only',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'analyze the characters',
    model: '',
    turnStartedAt: performance.now(),
    toolCallSegments: conversations[0].toolCallSegments,
    commentaryBlocks: [],
    commentaryDurationsMs: [],
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
  assert.equal(conversations[0].content, '')
  assert.equal(conversations[0].error, EMPTY_RESPONSE_MESSAGE)
  assert.equal(conversations[0].isError, false)
  assert.equal(acc.response, '')
  assert.equal(loading, false)
  assert.equal(outcome, 'failed')
})

test('durable task metadata does not replace Provider-authored final text', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  let outcome
  const acc = {
    response: '',
    commentary: '',
    bookId: 1,
    sessionId: 0,
    chapterId: 1,
    needsTitle: false,
    userText: 'write all remaining scenes',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
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
  acc.response = '已恢复原有长篇正文任务，将从上次检查点继续。'
  assert.equal(conversations[0].content, '')
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
    commentaryBlockIndex: null,
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

test('runtime state never becomes host-authored assistant copy', () => {
  const states = [
    { role: 'assistant', content: '' },
    {
      role: 'assistant',
      content: '',
      contextCompaction: { status: 'running' },
    },
    {
      role: 'assistant',
      content: '',
      commentary: '正在核对范围',
    },
    {
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
    },
    {
      role: 'assistant',
      content: 'tool commentary',
      toolCalling: true,
    },
    {
      role: 'assistant',
      content: 'final answer',
    },
    {
      role: 'assistant',
      content: '',
      toolApprovals: [{ status: 'pending' }],
    },
    {
      role: 'assistant',
      content: '',
      delegations: [{ status: 'running' }],
    },
    {
      role: 'assistant',
      content: '',
      taskPlan: { title: 'plan', status: 'planned', steps: [] },
    },
    {
      role: 'assistant',
      content: '',
      toolCallSegments: [{ labels: ['tool'], commentaryBlockIndex: null }],
    },
    {
      role: 'assistant',
      content: '',
      contextBudget: {},
    },
  ]

  for (const state of states) {
    assert.equal(getAssistantProcessingLabel(state), '')
  }
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
