'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../scripts/load-typescript-module.cjs')

const {
  AI_BUILTIN_PROVIDERS,
  AI_MODEL_PRESETS,
  applyModelRuntimeConfigPatch,
  getBuiltinProvider,
  getModelPreset,
  getDefaultModelContextWindow,
  getModelMaxOutputTokens,
  getModelContextWindowOptions,
  isModelThinkingEnabled,
  normalizeApiProvider,
} = loadTypeScriptModule(path.join(__dirname, 'modelCatalog.ts'))

test('every built-in provider has presets', () => {
  for (const provider of AI_BUILTIN_PROVIDERS) {
    const presets = AI_MODEL_PRESETS.filter(preset => preset.providerId === provider.id)
    assert.ok(presets.length > 0, `${provider.id} should expose presets`)
    assert.ok(presets.every((preset) => preset.providerId === provider.id))
  }
})

test('Z.ai GLM-5.3-Flash is the first catalog entry', () => {
  const provider = getBuiltinProvider('zai')
  const preset = getModelPreset('zai:glm-5.3-flash')

  assert.deepEqual(provider, {
    id: 'zai',
    name: '智谱 AI',
    apiProvider: 'zai',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4/',
    keyPlaceholder: '请输入智谱 API Key',
  })
  assert.deepEqual(
    {
      id: preset.id,
      name: preset.name,
      contextWindow: preset.contextWindow,
      supportsThinking: preset.supportsThinking,
      thinkingOnly: preset.thinkingOnly,
      thinkingEnabled: preset.thinkingEnabled,
      temperatureThinking: preset.temperatureThinking,
      temperatureNonThinking: preset.temperatureNonThinking,
    },
    {
      id: 'zai:glm-5.3-flash',
      name: 'glm-5.3-flash',
      contextWindow: '1m',
      supportsThinking: true,
      thinkingOnly: true,
      thinkingEnabled: true,
      temperatureThinking: 1,
      temperatureNonThinking: 1,
    },
  )
  assert.equal(AI_MODEL_PRESETS[0].id, 'zai:glm-5.3-flash')
})

test('DeepSeek exposes only V4 Flash as its built-in default', () => {
  const provider = getBuiltinProvider('deepseek')
  const presets = AI_MODEL_PRESETS.filter(preset => preset.providerId === 'deepseek')

  assert.deepEqual(provider, {
    id: 'deepseek',
    name: 'DeepSeek',
    apiProvider: 'openai',
    baseUrl: 'https://api.deepseek.com',
    keyPlaceholder: '请输入 DeepSeek API Key',
  })
  assert.deepEqual(
    presets.map((preset) => ({
      id: preset.id,
      name: preset.name,
      contextWindow: preset.contextWindow,
      maxOutputTokens: preset.maxOutputTokens,
      supportsThinking: preset.supportsThinking,
      thinkingEnabled: preset.thinkingEnabled,
    })),
    [
      {
        id: 'deepseek:deepseek-v4-flash',
        name: 'deepseek-v4-flash',
        contextWindow: '1m',
        maxOutputTokens: 393_216,
        supportsThinking: true,
        thinkingEnabled: true,
      },
    ],
  )
})

test('API provider normalization preserves Z.ai and rejects unknown legacy values', () => {
  assert.equal(normalizeApiProvider('zai'), 'zai')
  assert.equal(normalizeApiProvider('anthropic'), 'anthropic')
  assert.equal(normalizeApiProvider('openai'), 'openai')
  assert.equal(normalizeApiProvider('unknown-provider'), 'openai')
  assert.equal(normalizeApiProvider(undefined), 'openai')
})

test('preset ids and provider/model pairs are unique', () => {
  const presetIds = AI_MODEL_PRESETS.map((preset) => preset.id)
  const pairs = AI_MODEL_PRESETS.map((preset) => `${preset.providerId}:${preset.name}`)
  assert.equal(new Set(presetIds).size, presetIds.length)
  assert.equal(new Set(pairs).size, pairs.length)
  for (const preset of AI_MODEL_PRESETS) {
    assert.ok(preset.contextWindowOptions.length <= 3)
    assert.equal(preset.contextWindowOptions.at(-1), preset.contextWindow)
  }
})

test('model catalog exposes capability ceilings instead of task budgets', () => {
  assert.equal(
    getModelMaxOutputTokens({
      presetId: 'mimo:mimo-v2.5-pro',
      contextWindow: '32k',
    }),
    131_072,
  )
  assert.equal(
    getModelMaxOutputTokens({ presetId: 'deepseek:deepseek-v4-flash' }),
    393_216,
  )
  assert.equal(
    getModelMaxOutputTokens({ presetId: 'minimax:MiniMax-M3' }),
    524_288,
  )
  assert.equal(getModelMaxOutputTokens({ presetId: 'moonshot:kimi-k3' }), 1_048_576)
  assert.equal(getModelMaxOutputTokens({ presetId: 'moonshot:kimi-k2.6' }), 262_144)
  assert.equal(getModelMaxOutputTokens({ contextWindow: '1m' }), undefined)
})

test('the catalog contains GLM-5.3-Flash and the existing models', () => {
  assert.deepEqual(
    AI_MODEL_PRESETS.map((preset) => preset.name),
    [
      'glm-5.3-flash',
      'deepseek-v4-flash',
      'kimi-k3',
      'kimi-k2.6',
      'MiniMax-M3',
      'mimo-v2.5-pro',
    ],
  )
  assert.deepEqual(
    Object.fromEntries(AI_MODEL_PRESETS.map((preset) => [preset.name, preset.contextWindow])),
    {
      'glm-5.3-flash': '1m',
      'deepseek-v4-flash': '1m',
      'kimi-k3': '1m',
      'kimi-k2.6': '256k',
      'MiniMax-M3': '1m',
      'mimo-v2.5-pro': '1m',
    },
  )
  assert.deepEqual(
    Object.fromEntries(
      AI_MODEL_PRESETS.map((preset) => [preset.name, preset.thinkingOnly]),
    ),
    {
      'glm-5.3-flash': true,
      'deepseek-v4-flash': false,
      'kimi-k3': true,
      'kimi-k2.6': false,
      'MiniMax-M3': false,
      'mimo-v2.5-pro': false,
    },
  )
  assert.deepEqual(
    Object.fromEntries(
      AI_MODEL_PRESETS.map((preset) => [preset.name, [...preset.contextWindowOptions]]),
    ),
    {
      'glm-5.3-flash': ['32k', '256k', '1m'],
      'deepseek-v4-flash': ['32k', '256k', '1m'],
      'kimi-k3': ['32k', '256k', '1m'],
      'kimi-k2.6': ['32k', '128k', '256k'],
      'MiniMax-M3': ['32k', '256k', '1m'],
      'mimo-v2.5-pro': ['32k', '256k', '1m'],
    },
  )
})

test('context choices follow each built-in model maximum without dense legacy tiers', () => {
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'zai:glm-5.3-flash' })],
    ['32k', '256k', '1m'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'moonshot:kimi-k3' })],
    ['32k', '256k', '1m'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'moonshot:kimi-k2.6' })],
    ['32k', '128k', '256k'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'minimax:MiniMax-M3' })],
    ['32k', '256k', '1m'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ contextWindow: '200k' })],
    ['32k', '128k', '200k', '256k', '1m'],
  )
  assert.equal(
    getDefaultModelContextWindow({
      presetId: 'moonshot:kimi-k2.6',
      contextWindow: '32k',
    }),
    '32k',
  )
})

test('runtime thinking selection changes only the user-owned mode', () => {
  const config = {
    id: 'thinking-model',
    name: 'thinking-model',
    apiKey: 'secret',
    baseUrl: 'https://example.test/v1',
    supportsThinking: true,
    thinkingOnly: false,
    thinkingEnabled: true,
  }

  const disabled = applyModelRuntimeConfigPatch(config, { thinkingEnabled: false })
  assert.equal(disabled.thinkingEnabled, false)
  assert.equal(disabled.supportsThinking, true)

  const enabled = applyModelRuntimeConfigPatch(
    { ...config, supportsThinking: false, thinkingEnabled: false },
    { thinkingEnabled: true },
  )
  assert.equal(enabled.thinkingEnabled, true)
  assert.equal(enabled.supportsThinking, false)

  const fixedThinking = applyModelRuntimeConfigPatch(
    { ...config, thinkingOnly: true, thinkingEnabled: true },
    { thinkingEnabled: false },
  )
  assert.equal(fixedThinking.thinkingEnabled, false)
  assert.equal(fixedThinking.thinkingOnly, true)
  assert.equal(
    isModelThinkingEnabled({ thinkingOnly: true, thinkingEnabled: false }),
    false,
  )
})
