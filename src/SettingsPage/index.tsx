import React from 'react'
import AppHeader from '../components/AppHeader'
import { CheckIcon, CopyIcon, DeleteIcon, EditIcon, ExportIcon, ImportIcon, PlusIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrForm, PurrInput, PurrInputNumber, PurrModal, PurrRadio, PurrSelect, PurrSlider, PurrSwitch, PurrTag, PurrTooltip } from '@/purr-components'
import type { AiModelConfig, AiProviderCapacityPolicy, MemoryEmbeddingConfig } from '../types'
import {
  AI_BUILTIN_MODEL_PROFILES,
  AI_CONTEXT_WINDOW_LABELS,
  AI_REASONING_EFFORT_LABELS,
  getBuiltinProvider,
  getModelProfileMaxGenerationTokens,
  getModelPreset,
  getModelContextWindowOptions,
  getModelReasoningEffortOptions,
  normalizeApiProvider,
} from '../modelCatalog'
import { useAppFeedback } from '../hooks/useAppFeedback'
import {
  getShortcutKeyLabel,
  isApplePlatform,
  KEYBOARD_SHORTCUT_GROUPS,
} from '../keyboardShortcuts'
import { shortUuid } from '../utils/common'
import { useDatabaseActions } from './useDatabaseActions'
import { useMemoryAutosave, type MemoryConfigurationPatch } from './useMemoryAutosave'
import './index.scss'
import {
  AGENT_OPERATION_MODE_OPTIONS,
  type AgentOperationMode,
} from '../agentOperationMode'

type SettingsTab = 'general' | 'models' | 'shortcuts' | 'data'
const NAV_ITEMS: { key: SettingsTab; label: string }[] = [
  { key: 'general', label: '通用' },
  { key: 'models', label: '模型配置' },
  { key: 'shortcuts', label: '快捷键' },
  { key: 'data', label: '数据' },
]

const normalizePolicyEndpoint = (endpoint: string | undefined): string => (
  endpoint?.trim().replace(/\/+$/, '') || ''
)

interface SettingsPageProps {
  modelConfigs: AiModelConfig[]
  onSaveModelConfigs: (configs: AiModelConfig[]) => void
  providerCapacityPolicies: AiProviderCapacityPolicy[]
  onSaveProviderCapacityPolicies: (policies: AiProviderCapacityPolicy[]) => Promise<void>
  memoryModelId: string
  memoryEmbeddingConfig: MemoryEmbeddingConfig | null
  onSaveMemoryConfiguration: (
    patch: MemoryConfigurationPatch,
  ) => Promise<void>
  onClose: () => void
  onHome: () => void
  syncOutlineChapter: boolean
  onSyncOutlineChapterChange: (value: boolean) => void
  agentPreventSystemSleep: boolean
  onAgentPreventSystemSleepChange: (value: boolean) => void
  agentOperationMode: AgentOperationMode
  onAgentOperationModeChange: (value: AgentOperationMode) => void
}

export default function SettingsPage({
  modelConfigs,
  onSaveModelConfigs,
  providerCapacityPolicies,
  onSaveProviderCapacityPolicies,
  memoryModelId,
  memoryEmbeddingConfig,
  onSaveMemoryConfiguration,
  onClose,
  onHome,
  syncOutlineChapter,
  onSyncOutlineChapterChange,
  agentPreventSystemSleep,
  onAgentPreventSystemSleepChange,
  agentOperationMode,
  onAgentOperationModeChange,
}: SettingsPageProps) {
  const { message } = useAppFeedback()
  const [activeTab, setActiveTab] = React.useState<SettingsTab>('general')
  const [modelConfigList, setModelConfigList] = React.useState<AiModelConfig[]>(modelConfigs)
  const [modelModalOpen, setModelModalOpen] = React.useState(false)
  const [editingConfig, setEditingConfig] = React.useState<AiModelConfig | null>(null)
  /** 新建内置服务商条目时选中的服务商级 preset id。 */
  const [creatingPresetId, setCreatingPresetId] = React.useState<string | null>(null)
  const memory = useMemoryAutosave(memoryModelId, memoryEmbeddingConfig, onSaveMemoryConfiguration)
  const { model: memoryModelDraft, enabled: configureEmbedding, draft: memoryEmbeddingDraft } = memory
  const [form] = PurrForm.useForm<Omit<AiModelConfig, 'id' | 'reasoningEffort'> & { reasoningEffort?: AiModelConfig['reasoningEffort'] | 'inherit' }>()
  const apiProviderWatch = PurrForm.useWatch('apiProvider', form)
  const thinkingEnabledWatch = PurrForm.useWatch('thinkingEnabled', form)
  const customizeTemperatureWatch = PurrForm.useWatch('customizeTemperature', form)
  const applePlatform = React.useMemo(() => isApplePlatform(), [])

  /** 列表/弹窗中展示用：昵称优先，否则模型名称 */
  const displayName = (c: AiModelConfig) => (c.nickname?.trim() || c.name) || '未命名'
  /** 弹窗当前命中的内置服务商 preset（编辑存量条目或新建内置条目时） */
  const modalPreset = getModelPreset(editingConfig?.presetId ?? creatingPresetId ?? undefined)
  const displayModelOutputCapability = (c: AiModelConfig) => {
    const maximum = getModelProfileMaxGenerationTokens(c)
    return maximum ? `${Math.round(maximum / 1024)}K` : '未登记'
  }
  const providerEndpointGroups = React.useMemo(() => {
    const groups = new Map<string, {
      provider: AiProviderCapacityPolicy['provider']
      endpoint: string
      modelNames: string[]
    }>()
    for (const config of modelConfigList) {
      const provider = config.apiProvider ?? 'openai'
      const endpoint = normalizePolicyEndpoint(
        config.baseUrl || getBuiltinProvider(config.providerId)?.baseUrl,
      )
      if (!endpoint) continue
      const modelName = (config.nickname?.trim() || config.name) || '未命名'
      const key = `${provider}\u001f${endpoint}`
      const existing = groups.get(key)
      if (existing) {
        existing.modelNames.push(modelName)
      } else {
        groups.set(key, { provider, endpoint, modelNames: [modelName] })
      }
    }
    for (const policy of providerCapacityPolicies) {
      const endpoint = normalizePolicyEndpoint(policy.endpoint)
      if (!endpoint) continue
      const key = `${policy.provider}\u001f${endpoint}`
      if (!groups.has(key)) {
        groups.set(key, {
          provider: policy.provider,
          endpoint,
          modelNames: [],
        })
      }
    }
    return [...groups.values()]
  }, [modelConfigList, providerCapacityPolicies])

  const capacityForEndpoint = (provider: AiProviderCapacityPolicy['provider'], endpoint: string) => (
    providerCapacityPolicies.find((policy) => (
      policy.provider === provider && policy.endpoint === endpoint
    ))?.maxConcurrentCalls ?? 2
  )

  const updateEndpointCapacity = (
    provider: AiProviderCapacityPolicy['provider'],
    endpoint: string,
    maxConcurrentCalls: number,
  ) => {
    if (!Number.isInteger(maxConcurrentCalls) || maxConcurrentCalls < 1 || maxConcurrentCalls > 16) {
      message.warning('端点并发上限必须是 1 到 16 的整数')
      return
    }
    const remaining = providerCapacityPolicies.filter((policy) => (
      policy.provider !== provider || policy.endpoint !== endpoint
    ))
    void onSaveProviderCapacityPolicies([
      ...remaining,
      { provider, endpoint, maxConcurrentCalls },
    ]).catch(() => message.error('端点并发策略未保存，请检查连接后重试'))
  }

  const resetEndpointCapacity = (
    provider: AiProviderCapacityPolicy['provider'],
    endpoint: string,
  ) => {
    void onSaveProviderCapacityPolicies(providerCapacityPolicies.filter((policy) => (
      policy.provider !== provider || policy.endpoint !== endpoint
    ))).catch(() => message.error('端点并发策略未保存，请检查连接后重试'))
  }

  React.useEffect(() => {
    setModelConfigList(modelConfigs)
  }, [modelConfigs])


  /** 开启自定义 Temperature 且启用 Thinking 时，若尚未有思考温度则补默认值 */
  React.useEffect(() => {
    if (!modelModalOpen || customizeTemperatureWatch !== true || thinkingEnabledWatch !== true) return
    const t = form.getFieldValue('temperatureThinking')
    if (t === undefined || t === null) {
      form.setFieldValue('temperatureThinking', 0.6)
    }
  }, [modelModalOpen, customizeTemperatureWatch, thinkingEnabledWatch, form])

  const customModelDefaults = React.useCallback((): Omit<AiModelConfig, 'id'> => ({
    apiProvider: 'openai',
    name: '',
    nickname: '',
    supportsThinking: false,
    thinkingOnly: false,
    thinkingEnabled: false,
    contextWindow: '128k',
    customizeTemperature: false,
    temperatureThinking: 0.6,
    temperatureNonThinking: 0.6,
    apiKey: '',
    baseUrl: '',
  }), [])

  const openAddModel = () => {
    setEditingConfig(null)
    setCreatingPresetId(null)
    form.resetFields()
    form.setFieldsValue(customModelDefaults())
    setModelModalOpen(true)
  }

  const openAddBuiltinModel = (presetId: string) => {
    const profile = AI_BUILTIN_MODEL_PROFILES.find((entry) => entry.preset.id === presetId)
    if (!profile) return
    setEditingConfig(null)
    setCreatingPresetId(presetId)
    form.resetFields()
    form.setFieldsValue({
      apiProvider: profile.provider.apiProvider,
      name: '',
      nickname: '',
      thinkingEnabled: profile.preset.thinkingEnabled,
      contextWindow: profile.preset.contextWindow,
      profileMaxGenerationTokens: undefined,
      maxGenerationTokens: undefined,
      customizeTemperature: profile.preset.customizeTemperature,
      temperatureThinking: profile.preset.temperatureThinking,
      temperatureNonThinking: profile.preset.temperatureNonThinking,
      apiKey: '',
      baseUrl: profile.provider.baseUrl,
    })
    setModelModalOpen(true)
  }

  const openEditModel = (config: AiModelConfig) => {
    setEditingConfig(config)
    setCreatingPresetId(null)
    form.resetFields()
    form.setFieldsValue({
      apiProvider: config.apiProvider ?? 'openai',
      name: config.name,
      nickname: config.nickname ?? '',
      supportsThinking: config.supportsThinking,
      thinkingOnly: config.thinkingOnly,
      thinkingEnabled: config.thinkingEnabled,
      reasoningEffort: config.modelPreferences?.reasoning_effort?.state === 'inherit' ? 'inherit' : config.reasoningEffort,
      thinkingBudgetTokens: config.thinkingBudgetTokens,
      contextWindow: config.contextWindow ?? '128k',
      profileMaxGenerationTokens: config.profileMaxGenerationTokens,
      maxGenerationTokens: config.maxGenerationTokens,
      customizeTemperature: config.customizeTemperature ?? true,
      temperatureThinking: config.temperatureThinking ?? 0.6,
      temperatureNonThinking: config.temperatureNonThinking ?? 0.6,
      apiKey: config.apiKey,
      baseUrl: config.baseUrl ?? '',
    })
    setModelModalOpen(true)
  }

  const handleModelModalOk = () => {
    form.validateFields().then((values) => {
      const editingPreset = getModelPreset(editingConfig?.presetId ?? creatingPresetId ?? undefined)
      const editingPresetProvider = getBuiltinProvider(editingPreset?.providerId)
      const name = (values.name ?? '').trim()
      const nickname = (values.nickname ?? '').trim()
      const apiKey = (values.apiKey ?? '').trim()
      const baseUrl = (editingPresetProvider?.baseUrl ?? values.baseUrl ?? '').trim()
      const thinkingEnabled = values.thinkingEnabled === undefined
        ? editingConfig?.thinkingEnabled
        : values.thinkingEnabled === true
      const reasoningEffortOptions = editingPreset?.reasoningEffortOptions ?? []
      const requestedReasoningEffort = values.reasoningEffort
      if (requestedReasoningEffort && requestedReasoningEffort !== 'inherit' && !reasoningEffortOptions.includes(requestedReasoningEffort)) {
        message.warning('该模型不支持所选思考强度，请重新选择')
        return
      }
      const reasoningEffort = requestedReasoningEffort === 'inherit' ? undefined : requestedReasoningEffort
      if (thinkingEnabled === false && reasoningEffort) {
        message.warning('普通模式不能同时指定思考强度')
        return
      }
      const thinkingBudgetTokens = values.thinkingBudgetTokens == null
        ? undefined
        : Number(values.thinkingBudgetTokens)
      const contextWindow = values.contextWindow ?? '128k'
      const profileMaxGenerationTokens = values.profileMaxGenerationTokens == null
        ? undefined
        : Number(values.profileMaxGenerationTokens)
      const maxGenerationTokens = values.maxGenerationTokens == null
        ? undefined
        : Number(values.maxGenerationTokens)
      const customizeTemperature = !!values.customizeTemperature
      let temperatureNonThinking = editingConfig?.temperatureNonThinking ?? 0.6
      let temperatureThinking = editingConfig?.temperatureThinking ?? 0.6
      if (customizeTemperature) {
        temperatureNonThinking =
          values.temperatureNonThinking != null ? Number(values.temperatureNonThinking) : 0.6
        temperatureThinking = thinkingEnabled === true
          ? (values.temperatureThinking != null ? Number(values.temperatureThinking) : 0.6)
          : (editingConfig?.temperatureThinking ?? 0.6)
      } else {
        temperatureNonThinking = 0.6
        temperatureThinking = 0.6
      }
      if (!name) {
        message.warning('请填写模型名称')
        return
      }
      if (!apiKey) {
        message.warning('请填写 API Key')
        return
      }
      const prov = editingPresetProvider?.apiProvider
        ?? normalizeApiProvider(values.apiProvider)
      if (!baseUrl) {
        message.warning('请填写接口地址')
        return
      }
      if (
        !editingPreset
        && (
          !Number.isInteger(profileMaxGenerationTokens)
          || profileMaxGenerationTokens! <= 0
        )
      ) {
        message.warning('请填写服务商确认的模型能力上限')
        return
      }
      if (
        profileMaxGenerationTokens != null
        && (!Number.isInteger(profileMaxGenerationTokens) || profileMaxGenerationTokens <= 0)
      ) {
        message.warning('模型能力上限必须是正整数')
        return
      }
      if (
        maxGenerationTokens != null
        && (!Number.isInteger(maxGenerationTokens) || maxGenerationTokens <= 0)
      ) {
        message.warning('用户单次生成上限必须是正整数')
        return
      }
      // 内置条目的能力上限默认取服务商登记值，用户显式覆盖时以覆盖值为准。
      const registeredProfileLimit = editingPreset
        ? (profileMaxGenerationTokens ?? editingPreset.maxGenerationTokens)
        : profileMaxGenerationTokens
      if (
        registeredProfileLimit
        && maxGenerationTokens
        && maxGenerationTokens > registeredProfileLimit
      ) {
        message.warning('用户单次生成上限不能超过模型能力上限')
        return
      }
      if (
        prov === 'anthropic'
        && thinkingEnabled === true
        && (
          !Number.isInteger(thinkingBudgetTokens)
          || thinkingBudgetTokens! < 1_024
          || (
            registeredProfileLimit != null
            && thinkingBudgetTokens! >= (maxGenerationTokens ?? registeredProfileLimit)
          )
        )
      ) {
        message.warning('Anthropic 思考预算必须至少为 1024，且小于单次总生成上限')
        return
      }
      if (editingConfig) {
        const next = modelConfigList.map((c) => {
          if (editingPreset && c.providerId === editingPreset.providerId && c.id !== editingConfig.id) {
            return { ...c, apiKey }
          }
          return c.id === editingConfig.id
            ? {
                ...c,
                apiProvider: prov,
                name,
                nickname: nickname || undefined,
                supportsThinking: editingPreset?.supportsThinking
                  ?? editingConfig.supportsThinking,
                thinkingOnly: editingPreset?.thinkingOnly ?? editingConfig.thinkingOnly,
                thinkingEnabled,
                reasoningEffort,
                modelSettingsVersion: 1 as const,
                modelPreferences: {
                  reasoning_mode: thinkingEnabled === undefined
                    ? editingConfig.modelPreferences?.reasoning_mode ?? { state: 'provider_default' as const }
                    : { state: 'explicit' as const, value: thinkingEnabled ? 'enabled' as const : 'disabled' as const },
                  reasoning_effort: requestedReasoningEffort === 'inherit' ? { state: 'inherit' as const }
                    : reasoningEffort ? { state: 'explicit' as const, value: reasoningEffort } : { state: 'provider_default' as const },
                  temperature: customizeTemperature && thinkingEnabled !== undefined
                    ? { state: 'explicit' as const, value: thinkingEnabled ? temperatureThinking : temperatureNonThinking }
                    : { state: 'provider_default' as const },
                },
                thinkingBudgetTokens,
                contextWindow,
                profileMaxGenerationTokens,
                maxGenerationTokens,
                customizeTemperature,
                temperatureThinking,
                temperatureNonThinking,
                apiKey,
                baseUrl,
              }
            : c
        })
        setModelConfigList(next)
        onSaveModelConfigs(next)
        message.success('已更新')
      } else {
        const newConfig: AiModelConfig = {
          modelSettingsVersion: 1,
          modelPreferences: { reasoning_effort: { state: 'inherit' } },
          id: `model_${shortUuid()}`,
          presetId: editingPreset?.id,
          providerId: editingPreset?.providerId,
          apiProvider: prov,
          name,
          nickname: nickname || undefined,
          supportsThinking: editingPreset?.supportsThinking ?? thinkingEnabled === true,
          thinkingOnly: editingPreset?.thinkingOnly ?? false,
          thinkingEnabled,
          thinkingBudgetTokens,
          contextWindow,
          profileMaxGenerationTokens,
          maxGenerationTokens,
          customizeTemperature,
          temperatureThinking,
          temperatureNonThinking,
          apiKey,
          baseUrl,
        }
        const next = [...modelConfigList, newConfig]
        setModelConfigList(next)
        onSaveModelConfigs(next)
        message.success('已添加')
      }
      setCreatingPresetId(null)
      setModelModalOpen(false)
    }).catch(() => {})
  }

  const handleDeleteModel = (id: string) => {
    const next = modelConfigList.filter((c) => c.id !== id)
    setModelConfigList(next)
    onSaveModelConfigs(next)
    message.success('已删除')
  }

  /** 在列表中新增一条相同配置（新 id），昵称或展示名加「(副本)」便于区分 */
  const handleDuplicateModel = (c: AiModelConfig) => {
    const suffix = ' (副本)'
    const dupNickname = c.nickname?.trim()
      ? `${c.nickname.trim()}${suffix}`
      : `${c.name}${suffix}`
    const duplicated: AiModelConfig = {
      ...c,
      id: `model_${shortUuid()}`,
      nickname: dupNickname,
    }
    const next = [...modelConfigList, duplicated]
    setModelConfigList(next)
    onSaveModelConfigs(next)
    message.success('已复制配置')
  }

  const {
    dbInfo,
    dbInfoLoading,
    exportingDb,
    importingDb,
    refreshDbInfo,
    handleExportDatabase,
    handleImportDatabase,
    handleOpenDbDir,
    openingDbDir,
    canOpenDbDir,
  } = useDatabaseActions(activeTab === 'data')

  return (
    <div className="settings-page">
      <AppHeader
        title="设置"
        navigation={{
          home: { label: '返回首页', onClick: onHome },
          back: { label: '返回上一页', onClick: onClose },
        }}
        showActions
      />

      <div className="settings-body">
        <nav className="settings-nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`settings-nav-item ${activeTab === item.key ? 'active' : ''}`}
              onClick={() => setActiveTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          {activeTab === 'general' && (
            <div className="settings-section">
              <h2 className="settings-section-title">通用</h2>
              <p className="settings-section-desc">通用相关配置将在此展示。</p>
              <PurrCheckbox
                checked={syncOutlineChapter}
                onChange={(e) => onSyncOutlineChapterChange(e.target.checked)}
              >
                点击章节大纲或章节列表时，同步切换另一侧选中项
              </PurrCheckbox>
              <div className="settings-preference-row">
                <div className="settings-preference-copy">
                  <strong>Agent 运行时防止系统休眠</strong>
                  <p>允许屏幕正常关闭；任务运行期间保持电脑和本地服务唤醒，任务结束或暂停后恢复系统休眠。</p>
                </div>
                <PurrSwitch
                  checked={agentPreventSystemSleep}
                  onChange={onAgentPreventSystemSleepChange}
                  aria-label="Agent 运行时防止系统休眠"
                />
              </div>
              <div className="settings-preference-row">
                <div className="settings-preference-copy">
                  <strong>默认操作类型</strong>
                  <p>请求批准会逐次确认；帮我批准只自动执行普通写入；完全访问也会自动执行高风险操作。每轮仍可在输入框旁单独切换。</p>
                </div>
                <PurrSelect<AgentOperationMode>
                  aria-label="默认操作类型"
                  value={agentOperationMode}
                  options={AGENT_OPERATION_MODE_OPTIONS}
                  onChange={(value) => onAgentOperationModeChange(value as AgentOperationMode)}
                  style={{ width: 132 }}
                />
              </div>
            </div>
          )}
          {activeTab === 'models' && (
            <div className="settings-section">
              <h2 className="settings-section-title">模型配置</h2>
              <p className="settings-section-desc settings-model-section-desc">
                内置服务商由系统统一提供接入，只需填写模型名、凭据和运行参数；代理、自建服务和目录外模型可继续使用高级自定义接入。
              </p>
              <div className="settings-models-actions" style={{ marginBottom: 12 }}>
                <PurrButton type="primary" icon={<PlusIcon />} onClick={openAddModel}>
                  新增自定义模型
                </PurrButton>
              </div>
              <div className="settings-model-vendors" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
                {AI_BUILTIN_MODEL_PROFILES.map(({ provider, preset }) => {
                  const configured = modelConfigList.some((c) => c.presetId === preset.id)
                  return (
                    <PurrButton
                      key={preset.id}
                      size="small"
                      disabled={configured}
                      onClick={() => openAddBuiltinModel(preset.id)}
                    >
                      {provider.name}{configured ? ' · 已配置' : ' · 添加'}
                    </PurrButton>
                  )
                })}
              </div>
              {modelConfigList.length === 0 ? (
                <p className="settings-field-desc">暂无模型，请点击「新增模型」添加后，在 AI 对话中选择使用。</p>
              ) : (
                <ul className="settings-model-list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {modelConfigList.map((c) => {
                    const preset = getModelPreset(c.presetId)
                    const builtinProvider = getBuiltinProvider(c.providerId)
                    return (
                      <li key={c.id} className="settings-model-item">
                        <div className="settings-model-item-main">
                          <span style={{ fontWeight: 500 }}>{displayName(c)}</span>
                          {preset ? <PurrTag color="blue" className="settings-model-builtin-tag">内置</PurrTag> : null}
                          {c.nickname?.trim() ? (
                            <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>{c.name}</span>
                          ) : null}
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                            Context {(c.contextWindow ?? '128k').toUpperCase()}
                          </span>
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                            模型输出上限 {displayModelOutputCapability(c)}
                          </span>
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                            {c.thinkingEnabled === undefined
                              ? 'Thinking: Provider default'
                              : c.thinkingEnabled ? 'Thinking' : 'Non-thinking'}
                          </span>
                          {c.thinkingEnabled !== false && getModelReasoningEffortOptions(c).length > 0 ? (
                            <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                              思考强度 {c.reasoningEffort
                                ? AI_REASONING_EFFORT_LABELS[c.reasoningEffort]
                                : 'Provider default'}
                            </span>
                          ) : null}
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                            {builtinProvider?.name ?? (c.apiProvider === 'anthropic' ? 'Anthropic 兼容' : 'OpenAI 兼容')}
                          </span>
                          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                            {c.baseUrl?.trim() || (c.apiProvider === 'anthropic' ? '默认 api.anthropic.com' : '未填写接口地址')}
                            {c.apiKey ? (
                              <span style={{ marginLeft: 8 }}>
                                <CheckIcon style={{ fontSize: 12 }} /> 已配置 Key
                              </span>
                            ) : <span style={{ marginLeft: 8 }}>未配置 Key</span>}
                          </div>
                        </div>
                        <div className="settings-model-item-actions">
                          <PurrTooltip title={preset ? '配置' : '编辑'}>
                            <PurrButton type="text" size="small" icon={<EditIcon />} onClick={() => openEditModel(c)} />
                          </PurrTooltip>
                          {!preset ? (
                            <>
                              <PurrTooltip title="复制一项">
                                <PurrButton type="text" size="small" icon={<CopyIcon />} onClick={() => handleDuplicateModel(c)} />
                              </PurrTooltip>
                              <PurrTooltip title="删除">
                                <PurrButton
                                  type="text"
                                  size="small"
                                  icon={<DeleteIcon />}
                                  onClick={() => {
                                    if (window.confirm(`确定删除模型「${displayName(c)}」？`)) handleDeleteModel(c.id)
                                  }}
                                />
                              </PurrTooltip>
                            </>
                          ) : null}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
              {providerEndpointGroups.length > 0 ? (
                <section className="settings-provider-capacity">
                  <h3>Provider 端点并发</h3>
                  <p className="settings-field-desc">
                    同一端点上的模型共享这个上限。默认同时调用 2 个；限流或熔断恢复期间，系统仍会临时降为 1。未被当前模型使用的已存策略仍会保留在此，可随时恢复默认。
                  </p>
                  <div className="settings-provider-capacity-list">
                    {providerEndpointGroups.map((group) => {
                      const isCustomized = providerCapacityPolicies.some((policy) => (
                        policy.provider === group.provider && policy.endpoint === group.endpoint
                      ))
                      return (
                        <div key={`${group.provider}\u001f${group.endpoint}`} className="settings-provider-capacity-item">
                          <div>
                            <strong>{group.provider}</strong>
                            <code>{group.endpoint}</code>
                            <span>{group.modelNames.length > 0 ? group.modelNames.join('、') : '当前没有模型使用这个端点'}</span>
                          </div>
                          <div className="settings-provider-capacity-control">
                            <span>并发</span>
                            <PurrInputNumber
                              min={1}
                              max={16}
                              step={1}
                              value={capacityForEndpoint(group.provider, group.endpoint)}
                              onChange={(value) => {
                                if (typeof value === 'number') {
                                  updateEndpointCapacity(group.provider, group.endpoint, value)
                                }
                              }}
                            />
                            <PurrButton
                              type="text"
                              size="small"
                              disabled={!isCustomized}
                              onClick={() => resetEndpointCapacity(group.provider, group.endpoint)}
                            >
                              恢复默认
                            </PurrButton>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </section>
              ) : null}
              <PurrModal
                title={modalPreset
                  ? (editingConfig ? '配置内置服务商' : '添加内置服务商')
                  : (editingConfig ? '编辑自定义模型' : '新增自定义模型')}
                open={modelModalOpen}
                onOk={handleModelModalOk}
                onCancel={() => {
                  setCreatingPresetId(null)
                  setModelModalOpen(false)
                }}
                okText="保存"
                cancelText="取消"
                destroyOnHidden
                width={520}
                styles={{
                  container: {
                    maxHeight: '70vh',
                    display: 'flex',
                    flexDirection: 'column',
                    overflow: 'hidden',
                  },
                  header: { flexShrink: 0 },
                  body: { flex: 1, minHeight: 0, overflowY: 'auto' },
                  footer: { flexShrink: 0 },
                }}
              >
                <PurrForm form={form} layout="vertical" style={{ marginTop: 16 }}>
                  {modalPreset ? (
                    <>
                      <div className="settings-model-preset-summary">
                        <div>
                          <strong>{modalPreset.label}</strong>
                          <span>{modalPreset.summary}</span>
                        </div>
                        <div className="settings-model-preset-meta">
                          <PurrTag>系统内置</PurrTag>
                          <PurrTag>默认能力上限 {Math.round((modalPreset.maxGenerationTokens ?? 0) / 1024)}K</PurrTag>
                        </div>
                      </div>
                      <PurrForm.Item
                        name="name"
                        label="模型名称"
                        rules={[{ required: true, message: '请填写模型名称' }]}
                      >
                        <PurrInput placeholder={modalPreset.namePlaceholder} />
                      </PurrForm.Item>
                      <PurrForm.Item name="nickname" label="昵称">
                        <PurrInput placeholder="选填，AI 对话中优先显示昵称" />
                      </PurrForm.Item>
                      <PurrForm.Item
                        name="contextWindow"
                        label="Context"
                        rules={[{ required: true, message: '请选择 Context' }]}
                      >
                        <PurrRadio.Group optionType="button" buttonStyle="solid">
                          {modalPreset.contextWindowOptions.map((value) => (
                            <PurrRadio.Button key={value} value={value}>
                              {AI_CONTEXT_WINDOW_LABELS[value]}
                            </PurrRadio.Button>
                          ))}
                        </PurrRadio.Group>
                      </PurrForm.Item>
                      <PurrForm.Item
                        name="profileMaxGenerationTokens"
                        label="模型能力上限（含思考）"
                        extra={`选填；未设置时使用服务商默认 ${Math.round((modalPreset.maxGenerationTokens ?? 0) / 1024)}K。填写服务商确认的真实上限。`}
                        rules={[{ type: 'number', min: 1, message: '必须是正整数' }]}
                      >
                        <PurrInputNumber min={1} className="settings-input-full" />
                      </PurrForm.Item>
                      <PurrForm.Item
                        name="maxGenerationTokens"
                        label="单次总生成上限（含思考）"
                        extra="选填；未设置时使用上方模型能力上限。它不是正文长度目标。"
                        rules={[{ type: 'number', min: 1, message: '必须是正整数' }]}
                      >
                        <PurrInputNumber min={1} className="settings-input-full" />
                      </PurrForm.Item>
                      <PurrForm.Item
                        name="thinkingEnabled"
                        valuePropName="checked"
                        label="模型支持思考"
                        extra="按所填模型的实际能力声明：开启后以思考模式请求，关闭后按普通模式请求。对话报思考配置错误时回到这里调整。"
                      >
                        <PurrSwitch size="small" />
                      </PurrForm.Item>
                      {(modalPreset.reasoningEffortOptions ?? []).length > 0 ? (
                        <PurrForm.Item
                          name="reasoningEffort"
                          label="思考强度"
                          extra="选择本模型支持的思考强度；未选择时使用服务商默认值。"
                        >
                          <PurrSelect
                            allowClear
                            placeholder="服务商默认"
                            options={[{ value: 'inherit', label: '继承任务默认' }, ...(modalPreset.reasoningEffortOptions ?? []).map((value) => ({
                              value,
                              label: AI_REASONING_EFFORT_LABELS[value],
                            }))]}
                          />
                        </PurrForm.Item>
                      ) : null}
                      <PurrForm.Item name="customizeTemperature" valuePropName="checked" label="自定义 Temperature">
                        <PurrSwitch size="small" />
                      </PurrForm.Item>
                      {customizeTemperatureWatch === true ? (
                        <div className="settings-model-temperature-panel">
                          <div className="settings-model-temperature-panel-title">Temperature</div>
                          {thinkingEnabledWatch === true ? (
                            <PurrForm.Item
                              name="temperatureThinking"
                              label="Thinking 请求"
                              rules={[
                                { required: true, message: '请设置 temperature' },
                                { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                              ]}
                            >
                              <PurrSlider
                                min={0}
                                max={1}
                                step={0.1}
                                showValue
                                valueLabel="Thinking 请求 Temperature"
                                tooltip={{ formatter: (v) => (v != null ? v.toFixed(1) : '') }}
                              />
                            </PurrForm.Item>
                          ) : null}
                          <PurrForm.Item
                            name="temperatureNonThinking"
                            label={thinkingEnabledWatch === true ? '普通请求（备用）' : '普通请求'}
                            rules={[
                              { required: true, message: '请设置 temperature' },
                              { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                            ]}
                          >
                            <PurrSlider
                              min={0}
                              max={1}
                              step={0.1}
                              showValue
                              valueLabel="普通请求 Temperature"
                              tooltip={{ formatter: (v) => (v != null ? v.toFixed(1) : '') }}
                            />
                          </PurrForm.Item>
                        </div>
                      ) : null}
                      <PurrForm.Item name="apiKey" label="API Key" rules={[{ required: true, message: '请填写 API Key' }]}>
                        <PurrInput.Password
                          placeholder={getBuiltinProvider(modalPreset.providerId)?.keyPlaceholder ?? 'sk-xxxxxxxxxxxxxxxx'}
                        />
                      </PurrForm.Item>
                      <div className="settings-model-preset-endpoint">
                        <span>接口地址</span>
                        <code>{getBuiltinProvider(modalPreset.providerId)?.baseUrl}</code>
                      </div>
                    </>
                  ) : (
                    <>
                  <PurrForm.Item name="apiProvider" label="API 类型" rules={[{ required: true }]}>
                    <PurrRadio.Group>
                      <PurrRadio value="openai">OpenAI</PurrRadio>
                      <PurrRadio value="anthropic">Anthropic</PurrRadio>
                    </PurrRadio.Group>
                  </PurrForm.Item>
                  <PurrForm.Item name="name" label="模型名称" rules={[{ required: true, message: '请填写模型名称' }]}>
                    <PurrInput placeholder="如 gpt-4、moonshot-v1-32k 等 API 模型名" />
                  </PurrForm.Item>
                  <PurrForm.Item name="nickname" label="昵称">
                    <PurrInput placeholder="选填，AI 对话中优先显示昵称" />
                  </PurrForm.Item>
                  <PurrForm.Item
                    name="contextWindow"
                    label="Context"
                    extra="必须与模型服务商公布的真实上下文窗口一致；设置过大会导致上游拒绝请求。"
                    rules={[{ required: true, message: '请选择 Context' }]}
                  >
                    <PurrRadio.Group optionType="button" buttonStyle="solid">
                      {getModelContextWindowOptions(editingConfig).map((value) => (
                        <PurrRadio.Button key={value} value={value}>
                          {AI_CONTEXT_WINDOW_LABELS[value]}
                        </PurrRadio.Button>
                      ))}
                    </PurrRadio.Group>
                  </PurrForm.Item>
                  <PurrForm.Item
                    name="profileMaxGenerationTokens"
                    label="模型能力上限（含思考）"
                    extra="必须填写服务商确认的真实能力上限；它描述模型能力，不会作为本次正文长度目标。"
                    rules={[
                      { required: true, message: '请填写模型能力上限' },
                      { type: 'number', min: 1, message: '必须是正整数' },
                    ]}
                  >
                    <PurrInputNumber min={1} className="settings-input-full" />
                  </PurrForm.Item>
                  <PurrForm.Item
                    name="maxGenerationTokens"
                    label="用户单次生成上限（含思考）"
                    extra="选填；未设置时使用上方模型能力上限。该上限包含思考和正文生成，不是正文长度目标。"
                    rules={[{ type: 'number', min: 1, message: '必须是正整数' }]}
                  >
                    <PurrInputNumber min={1} className="settings-input-full" />
                  </PurrForm.Item>
                  <PurrForm.Item
                    name="thinkingEnabled"
                    valuePropName="checked"
                    label="Thinking"
                    extra={editingConfig?.thinkingOnly ? '该模型服务端不支持关闭；不兼容的配置会明确报错，不会被自动改写。' : undefined}
                  >
                    <PurrSwitch size='small' />
                  </PurrForm.Item>
                  {apiProviderWatch === 'anthropic' && thinkingEnabledWatch === true ? (
                    <PurrForm.Item
                      name="thinkingBudgetTokens"
                      label="Anthropic 思考预算"
                      extra="开启思考时必填；该预算计入单次总生成上限。"
                      rules={[
                        { required: true, message: '请填写 Anthropic 思考预算' },
                        { type: 'number', min: 1_024, message: '至少为 1024' },
                      ]}
                    >
                      <PurrInputNumber min={1_024} className="settings-input-full" />
                    </PurrForm.Item>
                  ) : null}
                  {getModelReasoningEffortOptions(editingConfig).length > 0 ? (
                    <PurrForm.Item
                      name="reasoningEffort"
                      label="思考强度"
                      extra="选择本模型支持的思考强度；未选择时使用服务商默认值。"
                    >
                      <PurrSelect
                        allowClear
                        placeholder="服务商默认"
                        options={[{ value: 'inherit', label: '继承任务默认' }, ...getModelReasoningEffortOptions(editingConfig).map((value) => ({
                          value,
                          label: AI_REASONING_EFFORT_LABELS[value],
                        }))]}
                      />
                    </PurrForm.Item>
                  ) : null}
                  <PurrForm.Item name="customizeTemperature" valuePropName="checked" label="自定义 Temperature">
                    <PurrSwitch size='small' />
                  </PurrForm.Item>
                  {customizeTemperatureWatch === true ? (
                  <div className="settings-model-temperature-panel">
                    <div className="settings-model-temperature-panel-title">Temperature</div>
                    {thinkingEnabledWatch === true ? (
                      <PurrForm.Item
                        name="temperatureThinking"
                        label="Thinking 请求"
                        rules={[
                          { required: true, message: '请设置 temperature' },
                          { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                        ]}
                      >
                        <PurrSlider
                          min={0}
                          max={1}
                          step={0.1}
                          showValue
                          valueLabel="Thinking 请求 Temperature"
                          tooltip={{ formatter: (v) => (v != null ? v.toFixed(1) : '') }}
                        />
                      </PurrForm.Item>
                    ) : null}
                    <PurrForm.Item
                      name="temperatureNonThinking"
                      label={thinkingEnabledWatch === true ? '普通请求（备用）' : '普通请求'}
                      rules={[
                        { required: true, message: '请设置 temperature' },
                        { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                      ]}
                    >
                      <PurrSlider
                        min={0}
                        max={1}
                        step={0.1}
                        showValue
                        valueLabel="普通请求 Temperature"
                        tooltip={{ formatter: (v) => (v != null ? v.toFixed(1) : '') }}
                      />
                    </PurrForm.Item>
                  </div>
                  ) : null}
                  <PurrForm.Item name="apiKey" label="API Key" rules={[{ required: true, message: '请填写 API Key' }]}>
                    <PurrInput.Password placeholder="填写服务密钥；无鉴权的本地服务可填占位值" />
                  </PurrForm.Item>
                  <PurrForm.Item
                    name="baseUrl"
                    label="接口地址"
                    rules={[{ required: true, message: '请填写接口地址' }]}
                  >
                    <PurrInput
                      placeholder={
                        apiProviderWatch === 'anthropic'
                          ? '建议 https://api.anthropic.com（或填代理 / 自建反代地址）'
                          : 'https://api.example.com/v1'
                      }
                    />
                  </PurrForm.Item>
                    </>
                  )}
                </PurrForm>
              </PurrModal>
              <section className="settings-memory-models">
                <h3>Embedding 检索增强</h3>
                <p className="settings-field-desc">
                  连接自备的 Embedding 服务以启用语义检索；未配置时使用普通检索。
                </p>
                <div className="settings-field">
                  <span className="settings-field-label">配置 Embedding 服务</span>
                  <PurrSwitch checked={configureEmbedding} onChange={memory.toggle} aria-label="配置 Embedding 服务" />
                </div>
                {configureEmbedding && <>
                <p className="settings-field-desc">配置完成后在作品资料库开启；检索文本会发送至该服务，可能产生费用。</p>

                <div className="settings-memory-embedding-grid" onBlur={memory.flush}>
                  <label className="settings-field">
                    <span className="settings-field-label">Embedding 模型</span>
                    <PurrInput
                      value={memoryEmbeddingDraft.model}
                      placeholder="填写服务提供的 Embedding 模型标识"
                      onChange={(event) => memory.changeEmbedding({ ...memoryEmbeddingDraft, model: event.target.value })}
                    />
                  </label>
                  <label className="settings-field">
                    <span className="settings-field-label">向量维度</span>
                    <PurrInputNumber
                      value={memoryEmbeddingDraft.dimensions || undefined}
                      placeholder="填写模型输出维度"
                      min={1}
                      max={65536}
                      step={1}
                      onChange={(value) => memory.changeEmbedding({ ...memoryEmbeddingDraft, dimensions: Number(value) })}
                    />
                  </label>
                  <label className="settings-field settings-memory-embedding-wide">
                    <span className="settings-field-label">Embedding 接口地址</span>
                    <PurrInput
                      value={memoryEmbeddingDraft.baseUrl}
                      placeholder="https://api.example.com/v1"
                      onChange={(event) => memory.changeEmbedding({ ...memoryEmbeddingDraft, baseUrl: event.target.value })}
                    />
                  </label>
                  <label className="settings-field settings-memory-embedding-wide">
                    <span className="settings-field-label">Embedding API Key</span>
                    <PurrInput.Password
                      value={memoryEmbeddingDraft.apiKey}
                      placeholder="填写服务密钥；无鉴权的本地服务可填占位值"
                      onChange={(event) => memory.changeEmbedding({ ...memoryEmbeddingDraft, apiKey: event.target.value })}
                    />
                  </label>
                </div>
                <p className="settings-field-desc">语义记忆组件的配置变更需重启生效；修改向量维度需要重建对应存储。</p>
                </>}
                <section className="settings-memory-review">
                  <h3>语义记忆的提炼与评审</h3>
                  <p className="settings-field-desc">默认使用当前作品的模型，也可指定其他模型。</p>
                  <PurrSelect
                    aria-label="提炼与评审模型"
                    value={memoryModelDraft}
                    className="settings-memory-review-select"
                    options={[
                      { value: '', label: '使用当前模型（默认）' },
                      ...modelConfigList
                        .filter((config) => config.apiKey?.trim())
                        .map((config) => ({ value: config.id, label: displayName(config) })),
                    ]}
                    onChange={(value) => memory.changeModel(String(value || ''))}
                  />
                </section>
                {memory.status && <p className="settings-field-desc" role="status">{memory.status}</p>}
              </section>
            </div>
          )}
          {activeTab === 'shortcuts' && (
            <div className="settings-section">
              <h2 className="settings-section-title">快捷键</h2>
              <div className="settings-shortcut-groups">
                {KEYBOARD_SHORTCUT_GROUPS.map((group) => (
                  <section className="settings-shortcut-group" key={group.title}>
                    <h3>{group.title}</h3>
                    <div className="settings-shortcut-list">
                      {group.shortcuts.map((shortcut) => (
                        <div className="settings-shortcut-row" key={`${group.title}-${shortcut.label}`}>
                          <div className="settings-shortcut-copy">
                            <span>{shortcut.label}</span>
                            {shortcut.detail ? <small>{shortcut.detail}</small> : null}
                          </div>
                          <div className="settings-shortcut-keys" aria-label={shortcut.keys.map((key) => getShortcutKeyLabel(key, applePlatform)).join(' + ')}>
                            {shortcut.keys.map((key, index) => (
                              <React.Fragment key={`${shortcut.label}-${key}-${index}`}>
                                {index > 0 ? <span className="settings-shortcut-plus">+</span> : null}
                                <kbd>{getShortcutKeyLabel(key, applePlatform)}</kbd>
                              </React.Fragment>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </div>
          )}
          {activeTab === 'data' && (
            <div className="settings-section">
              <h2 className="settings-section-title">备份与恢复</h2>
              <p className="settings-section-desc">导出包含作品数据库与本地记忆组件的完整备份，或成套恢复数据。API 密钥不会写入备份；恢复会覆盖当前数据并要求重启后端。</p>
              <div className="settings-field" style={{ maxWidth: 820, marginBottom: 16 }}>
                <div className="settings-field-label">当前数据库</div>
                <div className="settings-database-location">
                  <div className="settings-database-path">
                    {dbInfoLoading ? '读取中…' : (dbInfo?.dbPath || '读取失败')}
                  </div>
                  {canOpenDbDir && (
                    <PurrButton
                      onClick={handleOpenDbDir}
                      loading={openingDbDir}
                      disabled={dbInfoLoading || !dbInfo?.dbPath}
                    >
                      打开所在文件夹
                    </PurrButton>
                  )}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {dbInfo
                    ? `书籍 ${dbInfo.books} 本，章节 ${dbInfo.outlineChapters} 条，正文 ${dbInfo.articles} 篇`
                    : '—'}
                </div>
                <div className="settings-field-actions">
                  <PurrButton onClick={refreshDbInfo} loading={dbInfoLoading}>刷新统计</PurrButton>
                </div>
              </div>
              <div className="settings-data-actions">
                <PurrButton
                  type="default"
                  icon={<ExportIcon />}
                  onClick={handleExportDatabase}
                  loading={exportingDb}
                >
                  导出完整备份
                </PurrButton>
                <PurrButton
                  type="default"
                  icon={<ImportIcon />}
                  onClick={handleImportDatabase}
                  loading={importingDb}
                  danger
                >
                  恢复完整备份
                </PurrButton>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
