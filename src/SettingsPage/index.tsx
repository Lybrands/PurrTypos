import React from 'react'
import { ArrowLeftIcon, CheckIcon, CopyIcon, DeleteIcon, EditIcon, ExportIcon, ImportIcon, PlusIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrForm, PurrInput, PurrModal, PurrRadio, PurrSlider, PurrSwitch, PurrTag, PurrTooltip } from '@/purr-components'
import type { AiModelConfig } from '../types'
import {
  AI_CONTEXT_WINDOW_LABELS,
  getBuiltinProvider,
  getModelMaxOutputTokens,
  getModelPreset,
  getModelContextWindowOptions,
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
import './index.scss'

type SettingsTab = 'general' | 'models' | 'shortcuts' | 'data'
const NAV_ITEMS: { key: SettingsTab; label: string }[] = [
  { key: 'general', label: '通用' },
  { key: 'models', label: '模型配置' },
  { key: 'shortcuts', label: '快捷键' },
  { key: 'data', label: '数据' },
]

interface SettingsPageProps {
  modelConfigs: AiModelConfig[]
  onSaveModelConfigs: (configs: AiModelConfig[]) => void
  onClose: () => void
  syncOutlineChapter: boolean
  onSyncOutlineChapterChange: (value: boolean) => void
}

export default function SettingsPage({
  modelConfigs,
  onSaveModelConfigs,
  onClose,
  syncOutlineChapter,
  onSyncOutlineChapterChange,
}: SettingsPageProps) {
  const { message } = useAppFeedback()
  const [activeTab, setActiveTab] = React.useState<SettingsTab>('general')
  const [modelConfigList, setModelConfigList] = React.useState<AiModelConfig[]>(modelConfigs)
  const [modelModalOpen, setModelModalOpen] = React.useState(false)
  const [editingConfig, setEditingConfig] = React.useState<AiModelConfig | null>(null)
  const [form] = PurrForm.useForm<Omit<AiModelConfig, 'id'>>()
  const apiProviderWatch = PurrForm.useWatch('apiProvider', form)
  const thinkingEnabledWatch = PurrForm.useWatch('thinkingEnabled', form)
  const customizeTemperatureWatch = PurrForm.useWatch('customizeTemperature', form)
  const applePlatform = React.useMemo(() => isApplePlatform(), [])

  /** 列表/弹窗中展示用：昵称优先，否则模型名称 */
  const displayName = (c: AiModelConfig) => (c.nickname?.trim() || c.name) || '未命名'
  const displayModelOutputCapability = (c: AiModelConfig) => {
    const maximum = getModelMaxOutputTokens(c)
    return maximum ? `${Math.round(maximum / 1024)}K` : '未登记'
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
    form.resetFields()
    form.setFieldsValue(customModelDefaults())
    setModelModalOpen(true)
  }

  const openEditModel = (config: AiModelConfig) => {
    setEditingConfig(config)
    form.resetFields()
    form.setFieldsValue({
      apiProvider: config.apiProvider ?? 'openai',
      name: config.name,
      nickname: config.nickname ?? '',
      supportsThinking: config.thinkingEnabled ?? config.thinkingOnly ?? false,
      thinkingOnly: config.thinkingOnly,
      thinkingEnabled: config.thinkingEnabled ?? config.thinkingOnly ?? false,
      contextWindow: config.contextWindow ?? '128k',
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
      const editingPreset = getModelPreset(editingConfig?.presetId)
      const editingPresetProvider = getBuiltinProvider(editingPreset?.providerId)
      const name = (editingPreset?.name ?? values.name ?? '').trim()
      const nickname = (values.nickname ?? '').trim()
      const apiKey = (values.apiKey ?? '').trim()
      const baseUrl = (editingPresetProvider?.baseUrl ?? values.baseUrl ?? '').trim()
      const thinkingEnabled = editingPreset?.thinkingOnly ? true : !!values.thinkingEnabled
      const contextWindow = values.contextWindow ?? '128k'
      const customizeTemperature = !!values.customizeTemperature
      let temperatureNonThinking = editingConfig?.temperatureNonThinking ?? 0.6
      let temperatureThinking = editingConfig?.temperatureThinking ?? 0.6
      if (customizeTemperature) {
        temperatureNonThinking =
          values.temperatureNonThinking != null ? Number(values.temperatureNonThinking) : 0.6
        temperatureThinking = thinkingEnabled
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
                  ?? (thinkingEnabled || editingConfig.supportsThinking),
                thinkingOnly: editingPreset?.thinkingOnly ?? editingConfig.thinkingOnly,
                thinkingEnabled,
                contextWindow,
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
          id: `model_${shortUuid()}`,
          apiProvider: prov,
          name,
          nickname: nickname || undefined,
          supportsThinking: thinkingEnabled,
          thinkingOnly: false,
          thinkingEnabled,
          contextWindow,
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
    canOpenDbDir,
  } = useDatabaseActions(activeTab === 'data')

  return (
    <div className="settings-page">
      <header className="settings-header">
        <PurrTooltip title="返回">
          <PurrButton
            type="text"
            size="small"
            icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
            onClick={onClose}
            style={{ marginRight: 4 }}
          />
        </PurrTooltip>
        <span className="settings-title">设置</span>
      </header>

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
            </div>
          )}
          {activeTab === 'models' && (
            <div className="settings-section">
              <h2 className="settings-section-title">模型配置</h2>
              <p className="settings-section-desc settings-model-section-desc">
                内置模型由系统统一提供，只需配置凭据和运行参数；代理、自建服务和目录外模型可继续使用高级自定义接入。
              </p>
              <div className="settings-models-actions" style={{ marginBottom: 12 }}>
                <PurrButton type="primary" icon={<PlusIcon />} onClick={openAddModel}>
                  新增自定义模型
                </PurrButton>
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
                            {(c.thinkingEnabled ?? c.thinkingOnly ?? false) ? 'Thinking' : 'Non-thinking'}
                          </span>
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
              <PurrModal
                title={getModelPreset(editingConfig?.presetId)
                  ? '配置内置模型'
                  : (editingConfig ? '编辑自定义模型' : '新增自定义模型')}
                open={modelModalOpen}
                onOk={handleModelModalOk}
                onCancel={() => setModelModalOpen(false)}
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
                  {getModelPreset(editingConfig?.presetId) ? (
                    <>
                      <div className="settings-model-preset-summary">
                        <div>
                          <strong>{getModelPreset(editingConfig?.presetId)?.label}</strong>
                          <span>{getModelPreset(editingConfig?.presetId)?.summary}</span>
                        </div>
                        <div className="settings-model-preset-meta">
                          <PurrTag>系统内置</PurrTag>
                          <PurrTag>Context {getModelPreset(editingConfig?.presetId)?.contextWindow.toUpperCase()}</PurrTag>
                          <PurrTag>模型输出上限 {displayModelOutputCapability(editingConfig!)}</PurrTag>
                        </div>
                      </div>
                      <PurrForm.Item name="nickname" label="昵称">
                        <PurrInput placeholder="选填，AI 对话中优先显示昵称" />
                      </PurrForm.Item>
                      <PurrForm.Item
                        name="contextWindow"
                        label="Context"
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
                      <PurrForm.Item name="apiKey" label="API Key" rules={[{ required: true, message: '请填写 API Key' }]}>
                        <PurrInput.Password
                          placeholder={getBuiltinProvider(editingConfig?.providerId)?.keyPlaceholder ?? 'sk-xxxxxxxxxxxxxxxx'}
                        />
                      </PurrForm.Item>
                      <div className="settings-model-preset-endpoint">
                        <span>接口地址</span>
                        <code>{getBuiltinProvider(editingConfig?.providerId)?.baseUrl}</code>
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
                    name="thinkingEnabled"
                    valuePropName="checked"
                    label="Thinking"
                    extra={editingConfig?.thinkingOnly ? '该模型使用思考模式，服务端不支持关闭。' : undefined}
                  >
                    <PurrSwitch size='small' disabled={editingConfig?.thinkingOnly} />
                  </PurrForm.Item>
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
                    <PurrInput.Password placeholder="sk-xxxxxxxxxxxxxxxx" />
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
              <p className="settings-section-desc">导出完整数据库备份到本地文件，或从备份文件恢复数据。导入将覆盖当前全部数据并刷新应用。</p>
              <div className="settings-field" style={{ maxWidth: 820, marginBottom: 16 }}>
                <div className="settings-field-label">当前数据库</div>
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--text-muted)',
                    padding: '8px 10px',
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'var(--bg-surface)',
                    wordBreak: 'break-all',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                  }}
                >
                  {dbInfoLoading ? '读取中…' : (dbInfo?.dbPath || '读取失败')}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {dbInfo
                    ? `书籍 ${dbInfo.books} 本，章节 ${dbInfo.outlineChapters} 条，正文 ${dbInfo.articles} 篇`
                    : '—'}
                </div>
                <div className="settings-field-actions">
                  {canOpenDbDir && (
                    <PurrButton onClick={handleOpenDbDir}>打开数据库目录</PurrButton>
                  )}
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
                  导出数据库
                </PurrButton>
                <PurrButton
                  type="default"
                  icon={<ImportIcon />}
                  onClick={handleImportDatabase}
                  loading={importingDb}
                  danger
                >
                  导入数据库
                </PurrButton>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
