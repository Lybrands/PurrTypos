import type { AiModelConfig } from '../types'

/**
 * 模型级旧 preset 到服务商级 preset 的迁移映射。
 * 旧目录已冻结：只用于把存量配置迁到服务商条目，不再产生新配置。
 */
const LEGACY_PRESET_MIGRATIONS: Record<string, {
  presetId: string
  providerId: AiModelConfig['providerId']
  name: string
  profileMaxGenerationTokens: number
}> = {
  'zai:glm-5.3-flash': {
    presetId: 'zai',
    providerId: 'zai',
    name: 'glm-5.3-flash',
    profileMaxGenerationTokens: 131_072,
  },
  'deepseek:deepseek-v4-flash': {
    presetId: 'deepseek',
    providerId: 'deepseek',
    name: 'deepseek-v4-flash',
    profileMaxGenerationTokens: 393_216,
  },
  'moonshot:kimi-k3': {
    presetId: 'moonshot',
    providerId: 'moonshot',
    name: 'kimi-k3',
    profileMaxGenerationTokens: 1_048_576,
  },
  'moonshot:kimi-k2.6': {
    presetId: 'moonshot',
    providerId: 'moonshot',
    name: 'kimi-k2.6',
    profileMaxGenerationTokens: 262_144,
  },
  'minimax:MiniMax-M3': {
    presetId: 'minimax',
    providerId: 'minimax',
    name: 'MiniMax-M3',
    profileMaxGenerationTokens: 524_288,
  },
  'mimo:mimo-v2.5-pro': {
    presetId: 'mimo',
    providerId: 'mimo',
    name: 'mimo-v2.5-pro',
    profileMaxGenerationTokens: 131_072,
  },
}

/**
 * 把模型级 presetId 的存量配置迁移到服务商级条目：
 * presetId 换为服务商 id，模型名写入 name，原登记能力上限写入显式覆盖，
 * 保证迁移后请求行为不变。返回是否发生了变更（用于一次性回存）。
 */
export function migrateLegacyModelConfigs(configs: AiModelConfig[]): {
  configs: AiModelConfig[]
  changed: boolean
} {
  let changed = false
  const migrated = configs.map((config) => {
    const target = LEGACY_PRESET_MIGRATIONS[config.presetId ?? '']
    if (!target) return config
    changed = true
    return {
      ...config,
      presetId: target.presetId,
      providerId: target.providerId,
      name: config.name?.trim() || target.name,
      profileMaxGenerationTokens: config.profileMaxGenerationTokens
        ?? target.profileMaxGenerationTokens,
    }
  })
  return { configs: migrated, changed }
}
