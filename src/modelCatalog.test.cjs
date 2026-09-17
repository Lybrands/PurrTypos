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
  getModelProfileMaxGenerationTokens,
  getModelContextWindowOptions,
  getModelReasoningEffort,
  getModelReasoningEffortOptions,
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

test('Z.ai vendor entry is the first catalog entry and carries no fixed model name', () => {
  const provider = getBuiltinProvider('zai')
  const preset = getModelPreset('zai')

  assert.deepEqual(provider, {
    id: 'zai',
    name: '智谱 GLM',
    apiProvider: 'zai',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4/',
    keyPlaceholder: '请输入智谱 API Key',
  })
  assert.deepEqual(
    {
      id: preset.id,
      namePlaceholder: preset.namePlaceholder,
      contextWindow: preset.contextWindow,
      supportsThinking: preset.supportsThinking,
      thinkingOnly: preset.thinkingOnly,
      thinkingEnabled: preset.thinkingEnabled,
      temperatureThinking: preset.temperatureThinking,
      temperatureNonThinking: preset.temperatureNonThinking,
    },
    {
      id: 'zai',
      namePlaceholder: '如 glm-5.3-flash',
      contextWindow: '1m',
      supportsThinking: true,
      thinkingOnly: false,
      thinkingEnabled: true,
      temperatureThinking: 1,
      temperatureNonThinking: 1,
    },
  )
  assert.equal(AI_MODEL_PRESETS[0].id, 'zai')
})

test('each vendor exposes exactly one provider-level preset', () => {
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
      namePlaceholder: preset.namePlaceholder,
      contextWindow: preset.contextWindow,
      maxGenerationTokens: preset.maxGenerationTokens,
      supportsThinking: preset.supportsThinking,
      thinkingEnabled: preset.thinkingEnabled,
      reasoningEffortOptions: preset.reasoningEffortOptions,
    })),
    [
      {
        id: 'deepseek',
        namePlaceholder: '如 deepseek-v4-flash',
        contextWindow: '1m',
        maxGenerationTokens: 393_216,
        supportsThinking: true,
        thinkingEnabled: true,
        reasoningEffortOptions: ['low', 'high', 'max'],
      },
    ],
  )
})

test('API provider normalization preserves Z.ai and rejects unknown legacy values', () => {
  assert.equal(normalizeApiProvider('zai'), 'zai')
  assert.equal(normalizeApiProvider('anthropic'), 'anthropic')
  assert.equal(normalizeApiProvider('openai'), 'openai')
  assert.throws(() => normalizeApiProvider('unknown-provider'), /不支持的模型协议/)
  assert.equal(normalizeApiProvider(undefined), 'openai')
})

test('vendor preset ids are unique and defaults stay inside their options', () => {
  const presetIds = AI_MODEL_PRESETS.map((preset) => preset.id)
  assert.equal(new Set(presetIds).size, presetIds.length)
  assert.deepEqual(presetIds, ['zai', 'deepseek', 'moonshot', 'minimax', 'mimo'])
  for (const preset of AI_MODEL_PRESETS) {
    assert.ok(preset.contextWindowOptions.includes(preset.contextWindow))
  }
})

test('model catalog exposes capability ceilings instead of task budgets', () => {
  assert.equal(
    getModelProfileMaxGenerationTokens({
      presetId: 'mimo',
      contextWindow: '32k',
    }),
    131_072,
  )
  assert.equal(
    getModelProfileMaxGenerationTokens({ presetId: 'deepseek' }),
    393_216,
  )
  assert.equal(
    getModelProfileMaxGenerationTokens({ presetId: 'minimax' }),
    524_288,
  )
  assert.equal(getModelProfileMaxGenerationTokens({ presetId: 'moonshot' }), 1_048_576)
  // 用户显式覆盖优先于服务商登记默认。
  assert.equal(
    getModelProfileMaxGenerationTokens({ presetId: 'moonshot', profileMaxGenerationTokens: 262_144 }),
    262_144,
  )
  assert.equal(getModelProfileMaxGenerationTokens({ profileMaxGenerationTokens: 65_536 }), 65_536)
})

test('the catalog contains the five vendor presets with per-vendor defaults', () => {
  assert.deepEqual(
    Object.fromEntries(AI_MODEL_PRESETS.map((preset) => [preset.id, preset.contextWindow])),
    {
      zai: '1m',
      deepseek: '1m',
      moonshot: '256k',
      minimax: '1m',
      mimo: '1m',
    },
  )
  // 模型名由用户填写，思考不再被任何服务商强制锁定。
  assert.deepEqual(
    Object.fromEntries(
      AI_MODEL_PRESETS.map((preset) => [preset.id, preset.thinkingOnly]),
    ),
    {
      zai: false,
      deepseek: false,
      moonshot: false,
      minimax: false,
      mimo: false,
    },
  )
  assert.deepEqual(
    Object.fromEntries(
      AI_MODEL_PRESETS.map((preset) => [preset.id, [...preset.contextWindowOptions]]),
    ),
    {
      zai: ['32k', '256k', '1m'],
      deepseek: ['32k', '256k', '1m'],
      moonshot: ['32k', '128k', '256k', '1m'],
      minimax: ['32k', '256k', '1m'],
      mimo: ['32k', '256k', '1m'],
    },
  )
})

test('context choices follow each vendor default without dense legacy tiers', () => {
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'zai' })],
    ['32k', '256k', '1m'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ presetId: 'moonshot' })],
    ['32k', '128k', '256k', '1m'],
  )
  assert.deepEqual(
    [...getModelContextWindowOptions({ contextWindow: '200k' })],
    ['32k', '128k', '200k', '256k', '1m'],
  )
  assert.equal(
    getDefaultModelContextWindow({
      presetId: 'moonshot',
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

  const lowEffort = applyModelRuntimeConfigPatch(
    {
      ...config,
      presetId: 'deepseek',
    },
    { reasoningEffort: 'low' },
  )
  assert.equal(getModelReasoningEffort(lowEffort), 'low')
  assert.deepEqual(
    [...getModelReasoningEffortOptions(lowEffort)],
    ['low', 'high', 'max'],
  )
  assert.equal(getModelReasoningEffort(config), undefined)
})

test('legacy model-level presets migrate to vendor presets', () => {
  const { migrateLegacyModelConfigs } = loadTypeScriptModule(
    path.join(__dirname, 'models', 'migration.ts'),
  )
  const { configs, changed } = migrateLegacyModelConfigs([
    { id: 'a', name: '', presetId: 'zai:glm-5.3-flash' },
    { id: 'b', name: 'my-k2', presetId: 'moonshot:kimi-k2.6' },
    { id: 'c', name: 'custom' },
  ])
  assert.equal(changed, true)
  assert.deepEqual(configs[0], {
    id: 'a',
    name: 'glm-5.3-flash',
    presetId: 'zai',
    providerId: 'zai',
    profileMaxGenerationTokens: 131_072,
  })
  assert.deepEqual(configs[1], {
    id: 'b',
    name: 'my-k2',
    presetId: 'moonshot',
    providerId: 'moonshot',
    profileMaxGenerationTokens: 262_144,
  })
  assert.deepEqual(configs[2], { id: 'c', name: 'custom' })
  assert.equal(migrateLegacyModelConfigs(configs).changed, false)
})
