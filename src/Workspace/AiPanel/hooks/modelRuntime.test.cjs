'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../scripts/load-typescript-module.cjs')

const { buildStreamOptions } = loadTypeScriptModule(path.join(__dirname, 'streamOptions.ts'))
const { handleDelta, handleThinkingDelta } = loadTypeScriptModule(
  path.join(__dirname, 'chunkHandlers/streaming.ts'),
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
