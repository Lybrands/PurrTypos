'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, 'streamOptions.ts'))
const { handleDelta, handleThinkingDelta } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/streaming.ts'),
)
const { handleAgentDelegation } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/agentRun.ts'),
)
const { handleContextBudget, handleContextCompaction } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/context.ts'),
)
const { calculateContextUsage } = loadTypeScriptModule(
  path.join(__dirname, '../contextUsage.ts'),
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

  assert.equal(acc.contextCompaction.status, 'running')
  assert.equal(conversations[0].contextCompaction.selectedTurnCount, 4)
  assert.equal(acc.contextBudget.estimatedInputTokens, 12000)
  assert.equal(conversations[0].contextBudget.toolSchemaTokens, 1000)
})

test('context indicator prefers backend budget and estimates new text', () => {
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
    prompt: 'new question',
    windowTokens: 200000,
    loading: false,
  })

  assert.equal(usage.source, 'backend')
  assert.ok(usage.usedTokens > 13000)
  assert.equal(usage.windowTokens, 200000)
})
