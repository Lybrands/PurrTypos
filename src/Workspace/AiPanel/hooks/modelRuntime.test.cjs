'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, '../../../agent-runtime/streamOptions.ts'))
const {
  handleLongTaskDispatched,
  handleLongTaskProgress,
} = loadTypeScriptModule(
  path.join(__dirname, '../../../agent-runtime/chunkHandlers/durableTask.ts'),
)
const {
  EMPTY_RESPONSE_MESSAGE,
  handleDone,
  MANUAL_ABORT_MESSAGE,
} = loadTypeScriptModule(
  path.join(__dirname, '../../../agent-runtime/chunkHandlers/terminal.ts'),
)
const {
  countQueuedForSession,
  getSessionActivityLabel,
  getSettledSessionActivity,
} = loadTypeScriptModule(
  path.join(__dirname, 'chatQueue.ts'),
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
const { projectContextBudget } = loadTypeScriptModule(
  path.join(__dirname, '../../../agent-runtime/contextBudgetProjection.ts'),
)
const {
  KNOWN_TOOL_CALL_LABELS,
  resolveLocalizedToolDisplayName,
  toolCallDisplayRow,
} = loadTypeScriptModule(
  path.join(__dirname, '../../../components/AgentConversation/toolCallLabels.ts'),
)

function createAgentChunkContext({
  acc,
  readMessages,
  replaceMessages,
  setRunning = () => {},
  onSettled = () => {},
}) {
  return {
    acc,
    sessionId: acc.sessionId ?? 0,
    modelIdentity: { name: 'test-model' },
    now: () => performance.now(),
    host: {
      readMessages,
      replaceMessages,
      scheduleCommit: (updater) => replaceMessages(updater(readMessages())),
      flushCommits: () => {},
      setRunning,
      isVisible: () => true,
      onSettled,
    },
  }
}

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

test('durable task progress stays out of the work log', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  const acc = {}
  const ctx = createAgentChunkContext({
    acc,
    readMessages: () => conversations,
    replaceMessages: (messages) => { conversations = messages },
  })

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

test('manual abort replaces an empty response with an explicit notice', () => {
  let conversations = [{ role: 'assistant', content: '' }]
  let loading = true
  let cleanedUp = false
  const acc = {
    response: '',
    commentary: '',
    sessionId: 0,
    userText: 'stop this response',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  }
  const ctx = createAgentChunkContext({
    acc,
    readMessages: () => conversations,
    replaceMessages: (messages) => { conversations = messages },
    setRunning: (next) => { loading = next },
    onSettled: () => { cleanedUp = true },
  })

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
    sessionId: 0,
    userText: 'stop this response',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  }
  const ctx = createAgentChunkContext({
    acc,
    readMessages: () => conversations,
    replaceMessages: (messages) => { conversations = messages },
  })

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
    sessionId: 0,
    userText: 'analyze the characters',
    model: '',
    turnStartedAt: performance.now(),
    toolCallSegments: conversations[0].toolCallSegments,
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  }
  const ctx = createAgentChunkContext({
    acc,
    readMessages: () => conversations,
    replaceMessages: (messages) => { conversations = messages },
    setRunning: (next) => { loading = next },
    onSettled: (nextOutcome) => { outcome = nextOutcome },
  })

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
    sessionId: 0,
    userText: 'write all remaining scenes',
    model: '',
    turnStartedAt: performance.now(),
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  }
  const ctx = createAgentChunkContext({
    acc,
    readMessages: () => conversations,
    replaceMessages: (messages) => { conversations = messages },
    onSettled: (nextOutcome) => { outcome = nextOutcome },
  })

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
