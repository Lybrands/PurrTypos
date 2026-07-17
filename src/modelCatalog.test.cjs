'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../scripts/load-typescript-module.cjs')

const {
  AI_BUILTIN_PROVIDERS,
  AI_MODEL_PRESETS,
  applyModelRuntimeConfigPatch,
  createConfigFromPreset,
  getBuiltinProvider,
  getDefaultModelContextWindow,
  getDefaultPreset,
  getModelContextWindowOptions,
  getProviderPresets,
  isModelThinkingEnabled,
  migrateKnownModelConfigs,
} = loadTypeScriptModule(path.join(__dirname, 'modelCatalog.ts'))

test('every built-in provider has presets and one default', () => {
  for (const provider of AI_BUILTIN_PROVIDERS) {
    const presets = getProviderPresets(provider.id)
    assert.ok(presets.length > 0, `${provider.id} should expose presets`)
    assert.ok(getDefaultPreset(provider.id))
    assert.ok(presets.every((preset) => preset.providerId === provider.id))
  }
  assert.equal(getDefaultPreset('moonshot').id, 'moonshot:kimi-k3')
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

test('createConfigFromPreset keeps the legacy runtime config shape', () => {
  const preset = getDefaultPreset('minimax')
  const provider = getBuiltinProvider('minimax')
  const config = createConfigFromPreset({
    id: 'model_test',
    preset,
    apiKey: '  secret-key  ',
    nickname: '  小说主模型  ',
  })

  assert.deepEqual(config, {
    id: 'model_test',
    presetId: preset.id,
    providerId: 'minimax',
    apiProvider: 'openai',
    name: preset.name,
    nickname: '小说主模型',
    supportsThinking: true,
    thinkingOnly: true,
    thinkingEnabled: true,
    contextWindow: preset.contextWindow,
    customizeTemperature: false,
    temperatureThinking: 0.6,
    temperatureNonThinking: 0.6,
    apiKey: 'secret-key',
    baseUrl: provider.baseUrl,
  })
})

test('the catalog contains the existing models plus Kimi K3', () => {
  assert.deepEqual(
    AI_MODEL_PRESETS.map((preset) => preset.name),
    ['kimi-k3', 'kimi-k2.6', 'MiniMax-M3', 'mimo-v2.5-pro'],
  )
  assert.deepEqual(
    Object.fromEntries(AI_MODEL_PRESETS.map((preset) => [preset.name, preset.contextWindow])),
    {
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
      'kimi-k3': true,
      'kimi-k2.6': false,
      'MiniMax-M3': true,
      'mimo-v2.5-pro': false,
    },
  )
  assert.deepEqual(
    Object.fromEntries(
      AI_MODEL_PRESETS.map((preset) => [preset.name, [...preset.contextWindowOptions]]),
    ),
    {
      'kimi-k3': ['32k', '256k', '1m'],
      'kimi-k2.6': ['32k', '128k', '256k'],
      'MiniMax-M3': ['32k', '256k', '1m'],
      'mimo-v2.5-pro': ['32k', '256k', '1m'],
    },
  )
})

test('context choices follow each built-in model maximum without dense legacy tiers', () => {
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

test('runtime thinking selection preserves capability and updates the current mode', () => {
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
  assert.equal(enabled.supportsThinking, true)

  const fixedThinking = applyModelRuntimeConfigPatch(
    { ...config, thinkingOnly: true, thinkingEnabled: true },
    { thinkingEnabled: false },
  )
  assert.equal(fixedThinking.thinkingEnabled, true)
  assert.equal(fixedThinking.thinkingOnly, true)
  assert.equal(
    isModelThinkingEnabled({ thinkingOnly: true, thinkingEnabled: false }),
    true,
  )
})

test('known config migration updates only exact legacy provider models', () => {
  const legacyConfigs = [
    {
      id: 'kimi-k3',
      name: 'kimi-k3',
      nickname: 'Kimi K3',
      apiKey: 'kimi-k3-secret',
      baseUrl: 'https://api.moonshot.cn/v1/',
      supportsThinking: false,
      thinkingOnly: false,
      thinkingEnabled: false,
      contextWindow: '300k',
      customizeTemperature: true,
    },
    {
      id: 'kimi',
      name: 'kimi-k2.6',
      nickname: 'Kimi K2.6',
      apiKey: 'kimi-secret',
      baseUrl: 'https://api.moonshot.cn/v1/',
      supportsThinking: true,
      thinkingOnly: false,
      contextWindow: '300k',
    },
    {
      id: 'minimax',
      name: 'MiniMax-M3.0',
      nickname: 'MiniMax M3.0',
      apiKey: 'minimax-secret',
      baseUrl: 'https://api.minimaxi.com/anthropic',
      supportsThinking: true,
      thinkingOnly: false,
    },
    {
      id: 'mimo',
      name: 'mimo-v2.5-pro',
      nickname: '我的 MiMo',
      apiKey: 'mimo-secret',
      baseUrl: 'https://api.xiaomimimo.com/v1',
      supportsThinking: true,
      thinkingOnly: false,
    },
    {
      id: 'custom',
      name: 'kimi-k2.6',
      apiKey: 'custom-secret',
      baseUrl: 'https://proxy.example/v1',
      supportsThinking: false,
      thinkingOnly: false,
      contextWindow: '300k',
    },
  ]

  const result = migrateKnownModelConfigs(legacyConfigs)

  assert.equal(result.changed, true)
  assert.deepEqual(
    result.configs.map(({ name, nickname, contextWindow, presetId, providerId, apiKey }) => ({
      name,
      nickname,
      contextWindow,
      presetId,
      providerId,
      apiKey,
    })),
    [
      {
        name: 'kimi-k3',
        nickname: 'Kimi K3',
        contextWindow: '1m',
        presetId: 'moonshot:kimi-k3',
        providerId: 'moonshot',
        apiKey: 'kimi-k3-secret',
      },
      {
        name: 'kimi-k2.6',
        nickname: 'Kimi K2.6',
        contextWindow: '256k',
        presetId: 'moonshot:kimi-k2.6',
        providerId: 'moonshot',
        apiKey: 'kimi-secret',
      },
      {
        name: 'MiniMax-M3',
        nickname: 'MiniMax M3',
        contextWindow: '1m',
        presetId: 'minimax:MiniMax-M3',
        providerId: 'minimax',
        apiKey: 'minimax-secret',
      },
      {
        name: 'mimo-v2.5-pro',
        nickname: '我的 MiMo',
        contextWindow: '1m',
        presetId: 'mimo:mimo-v2.5-pro',
        providerId: 'mimo',
        apiKey: 'mimo-secret',
      },
      {
        name: 'kimi-k2.6',
        nickname: undefined,
        contextWindow: '300k',
        presetId: undefined,
        providerId: undefined,
        apiKey: 'custom-secret',
      },
    ],
  )
  assert.equal(result.configs[4], legacyConfigs[4])
  assert.equal(result.configs[0].supportsThinking, true)
  assert.equal(result.configs[0].thinkingOnly, true)
  assert.equal(result.configs[0].thinkingEnabled, true)
  assert.equal(result.configs[0].customizeTemperature, false)
  assert.equal(result.configs[2].supportsThinking, true)
  assert.equal(result.configs[2].thinkingOnly, true)
  assert.equal(result.configs[2].thinkingEnabled, true)
  assert.equal(result.configs[2].apiProvider, 'openai')
  assert.equal(result.configs[2].baseUrl, 'https://api.minimaxi.com/v1')
})

test('system built-ins are materialized without user add actions', () => {
  const result = migrateKnownModelConfigs([
    {
      id: 'existing-kimi',
      name: 'kimi-k2.6',
      nickname: 'Kimi K2.6',
      apiKey: 'shared-moonshot-key',
      baseUrl: 'https://api.moonshot.cn/v1',
      supportsThinking: true,
      thinkingOnly: false,
    },
  ])

  assert.equal(result.changed, true)
  assert.deepEqual(
    result.configs.slice(0, 4).map((config) => config.presetId),
    [
      'moonshot:kimi-k3',
      'moonshot:kimi-k2.6',
      'minimax:MiniMax-M3',
      'mimo:mimo-v2.5-pro',
    ],
  )
  assert.equal(result.configs[0].apiKey, 'shared-moonshot-key')
  assert.equal(result.configs[2].apiKey, '')
  assert.equal(result.configs[3].apiKey, '')
})
