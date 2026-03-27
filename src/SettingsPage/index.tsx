import React from 'react'
import { ArrowLeftOutlined, CheckOutlined, CopyOutlined, DeleteOutlined, EditOutlined, ExportOutlined, ImportOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Checkbox, Form, Input, Modal, Radio, Slider, Switch, Tooltip } from 'antd'
import type { AiModelConfig } from '../types'
import { useAntdApp } from '../hooks/useAntdApp'
import { shortUuid } from '../utils/common'
import './index.scss'

type SettingsTab = 'general' | 'ai' | 'models' | 'data'

const NAV_ITEMS: { key: SettingsTab; label: string }[] = [
  { key: 'general', label: '通用' },
  { key: 'ai', label: 'AI 配置' },
  { key: 'models', label: '模型配置' },
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
  const { message } = useAntdApp()
  const [activeTab, setActiveTab] = React.useState<SettingsTab>('general')
  const [modelConfigList, setModelConfigList] = React.useState<AiModelConfig[]>(modelConfigs)
  const [modelModalOpen, setModelModalOpen] = React.useState(false)
  const [editingConfig, setEditingConfig] = React.useState<AiModelConfig | null>(null)
  const [form] = Form.useForm<Omit<AiModelConfig, 'id'>>()
  const apiProviderWatch = Form.useWatch('apiProvider', form)
  const supportsThinkingWatch = Form.useWatch('supportsThinking', form)
  const customizeTemperatureWatch = Form.useWatch('customizeTemperature', form)

  /** 列表/弹窗中展示用：昵称优先，否则模型名称 */
  const displayName = (c: AiModelConfig) => (c.nickname?.trim() || c.name) || '未命名'

  React.useEffect(() => {
    setModelConfigList(modelConfigs)
  }, [modelConfigs])

  /** 开启自定义 Temperature 且勾选「支持thinking模式」时，若尚未有思考温度则补默认值 */
  React.useEffect(() => {
    if (!modelModalOpen || customizeTemperatureWatch !== true || supportsThinkingWatch !== true) return
    const t = form.getFieldValue('temperatureThinking')
    if (t === undefined || t === null) {
      form.setFieldValue('temperatureThinking', 0.6)
    }
  }, [modelModalOpen, customizeTemperatureWatch, supportsThinkingWatch, form])

  const openAddModel = () => {
    setEditingConfig(null)
    form.setFieldsValue({
      apiProvider: 'openai',
      name: '',
      nickname: '',
      supportsThinking: true,
      thinkingOnly: false,
      customizeTemperature: false,
      temperatureThinking: 0.6,
      temperatureNonThinking: 0.6,
      apiKey: '',
      baseUrl: '',
    })
    setModelModalOpen(true)
  }

  const openEditModel = (config: AiModelConfig) => {
    setEditingConfig(config)
    form.setFieldsValue({
      apiProvider: config.apiProvider ?? 'openai',
      name: config.name,
      nickname: config.nickname ?? '',
      supportsThinking: config.supportsThinking,
      thinkingOnly: config.thinkingOnly,
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
      const name = (values.name ?? '').trim()
      const nickname = (values.nickname ?? '').trim()
      const apiKey = (values.apiKey ?? '').trim()
      const baseUrl = (values.baseUrl ?? '').trim()
      const supportsThinking = !!values.supportsThinking
      const customizeTemperature = !!values.customizeTemperature
      let temperatureNonThinking = editingConfig?.temperatureNonThinking ?? 0.6
      let temperatureThinking = editingConfig?.temperatureThinking ?? 0.6
      if (customizeTemperature) {
        temperatureNonThinking =
          values.temperatureNonThinking != null ? Number(values.temperatureNonThinking) : 0.6
        temperatureThinking = supportsThinking
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
      const prov: 'openai' | 'anthropic' =
        values.apiProvider === 'anthropic' ? 'anthropic' : 'openai'
      if (!baseUrl) {
        message.warning('请填写接口地址')
        return
      }
      if (editingConfig) {
        const next = modelConfigList.map((c) =>
          c.id === editingConfig.id
            ? {
                ...c,
                apiProvider: prov,
                name,
                nickname: nickname || undefined,
                supportsThinking,
                thinkingOnly: !!values.thinkingOnly,
                customizeTemperature,
                temperatureThinking,
                temperatureNonThinking,
                apiKey,
                baseUrl,
              }
            : c
        )
        setModelConfigList(next)
        onSaveModelConfigs(next)
        message.success('已更新')
      } else {
        const newConfig: AiModelConfig = {
          id: `model_${shortUuid()}`,
          apiProvider: prov,
          name,
          nickname: nickname || undefined,
          supportsThinking,
          thinkingOnly: !!values.thinkingOnly,
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

  const [exportingDb, setExportingDb] = React.useState(false)
  const [importingDb, setImportingDb] = React.useState(false)
  const [dbInfoLoading, setDbInfoLoading] = React.useState(false)
  const [dbInfo, setDbInfo] = React.useState<{
    dbPath: string
    books: number
    outlineChapters: number
    articles: number
  } | null>(null)

  const refreshDbInfo = React.useCallback(async () => {
    setDbInfoLoading(true)
    try {
      const res = await window.electronAPI.getDatabaseInfo()
      if (res.success && res.data) {
        setDbInfo(res.data)
      } else {
        setDbInfo(null)
      }
    } finally {
      setDbInfoLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (activeTab !== 'data') return
    void refreshDbInfo()
  }, [activeTab, refreshDbInfo])

  const handleExportDatabase = React.useCallback(async () => {
    setExportingDb(true)
    try {
      const res = await window.electronAPI.exportDatabase()
      if (res.success) {
        message.success('数据库已导出')
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导出失败')
      }
    } finally {
      setExportingDb(false)
    }
  }, [message])

  const handleImportDatabase = React.useCallback(async () => {
    const confirmed = window.confirm(
      '导入将使用所选备份文件覆盖当前全部数据，完成后将自动刷新页面。是否继续？'
    )
    if (!confirmed) return
    setImportingDb(true)
    try {
      const res = await window.electronAPI.importDatabase()
      if (res.success) {
        const before = res.data?.beforeStats
        const after = res.data?.afterStats
        if (before && after) {
          message.success(
            `数据库已导入：章节 ${before.outlineChapters} -> ${after.outlineChapters}，正文 ${before.articles} -> ${after.articles}。正在刷新…`
          )
        } else {
          message.success('数据库已导入，正在刷新…')
        }
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导入失败')
      }
    } finally {
      setImportingDb(false)
    }
  }, [message])

  const handleOpenDbDir = React.useCallback(async () => {
    const res = await window.electronAPI.openDatabaseDirectory()
    if (!res.success) {
      message.error(res.error || '打开目录失败')
      return
    }
    message.success('已打开数据库目录')
  }, [message])

  return (
    <div className="settings-page">
      <header className="settings-header">
        <Tooltip title="返回">
          <Button
            type="text"
            size="small"
            icon={<ArrowLeftOutlined style={{ fontSize: 14 }} />}
            onClick={onClose}
            style={{ marginRight: 4 }}
          />
        </Tooltip>
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
              <Checkbox
                checked={syncOutlineChapter}
                onChange={(e) => onSyncOutlineChapterChange(e.target.checked)}
              >
                点击章节大纲或章节列表时，同步切换另一侧选中项
              </Checkbox>
            </div>
          )}
          {activeTab === 'ai' && (
            <div className="settings-section">
              <h2 className="settings-section-title">AI 配置</h2>
              <p className="settings-section-desc">系统提示词已内置，不对终端用户开放自定义。</p>
              <div className="settings-field">
                <div className="settings-field-label">系统提示词</div>
                <p className="settings-field-desc">
                  当前版本统一使用内置 ReAct 提示词，以保证工具调用与智能体行为稳定一致。
                </p>
              </div>
            </div>
          )}
          {activeTab === 'models' && (
            <div className="settings-section">
              <h2 className="settings-section-title">模型配置</h2>
              <div className="settings-models-actions" style={{ marginBottom: 12 }}>
                <Button type="primary" icon={<PlusOutlined />} onClick={openAddModel}>
                  新增模型
                </Button>
              </div>
              {modelConfigList.length === 0 ? (
                <p className="settings-field-desc">暂无模型，请点击「新增模型」添加后，在 AI 对话中选择使用。</p>
              ) : (
                <ul className="settings-model-list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {modelConfigList.map((c) => (
                    <li key={c.id} className="settings-model-item">
                      <div className="settings-model-item-main">
                        <span style={{ fontWeight: 500 }}>{displayName(c)}</span>
                        {c.nickname?.trim() ? (
                          <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>{c.name}</span>
                        ) : null}
                        <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                          {c.supportsThinking ? (c.thinkingOnly ? '仅thinking模式' : '支持thinking模式') : '不支持thinking'}
                        </span>
                        <span style={{ marginLeft: 8, color: 'var(--text-secondary, #666)', fontSize: 12 }}>
                          {c.apiProvider === 'anthropic' ? 'Anthropic 兼容' : 'OpenAI 兼容'}
                        </span>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                          {c.baseUrl?.trim() || (c.apiProvider === 'anthropic' ? '默认 api.anthropic.com' : '未填写接口地址')}
                          {c.apiKey ? (
                            <span style={{ marginLeft: 8 }}>
                              <CheckOutlined style={{ fontSize: 12 }} /> 已配置 Key
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="settings-model-item-actions">
                        <Tooltip title="复制一项">
                          <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => handleDuplicateModel(c)} />
                        </Tooltip>
                        <Tooltip title="编辑">
                          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEditModel(c)} />
                        </Tooltip>
                        <Tooltip title="删除">
                          <Button
                            type="text"
                            size="small"
                            icon={<DeleteOutlined />}
                            onClick={() => {
                              if (window.confirm(`确定删除模型「${displayName(c)}」？`)) handleDeleteModel(c.id)
                            }}
                          />
                        </Tooltip>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <Modal
                title={editingConfig ? '编辑模型' : '新增模型'}
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
                <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
                  <Form.Item name="apiProvider" label="API 类型" rules={[{ required: true }]}>
                    <Radio.Group>
                      <Radio value="openai">OpenAI</Radio>
                      <Radio value="anthropic">Anthropic</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item name="name" label="模型名称" rules={[{ required: true, message: '请填写模型名称' }]}>
                    <Input placeholder="如 gpt-4、moonshot-v1-32k 等 API 模型名" />
                  </Form.Item>
                  <Form.Item name="nickname" label="昵称">
                    <Input placeholder="选填，AI 对话中优先显示昵称" />
                  </Form.Item>
                  <Form.Item name="supportsThinking" valuePropName="checked" label="支持thinking模式">
                    <Switch size='small' />
                  </Form.Item>
                  <Form.Item name="thinkingOnly" valuePropName="checked" label="仅thinking模式">
                    <Switch size='small' />
                  </Form.Item>
                  <Form.Item name="customizeTemperature" valuePropName="checked" label="自定义 Temperature">
                    <Switch size='small' />
                  </Form.Item>
                  {customizeTemperatureWatch === true ? (
                  <div className="settings-model-temperature-panel">
                    <div className="settings-model-temperature-panel-title">Temperature</div>
                    {supportsThinkingWatch !== false ? (
                      <Form.Item
                        name="temperatureThinking"
                        label="思考模式开启时"
                        rules={[
                          { required: true, message: '请设置 temperature' },
                          { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                        ]}
                      >
                        <Slider
                          min={0}
                          max={1}
                          step={0.1}
                          tooltip={{ formatter: (v) => (v != null ? String(v) : '') }}
                        />
                      </Form.Item>
                    ) : null}
                    <Form.Item
                      name="temperatureNonThinking"
                      label={supportsThinkingWatch === false ? '非思考模式' : '思考模式关闭时'}
                      rules={[
                        { required: true, message: '请设置 temperature' },
                        { type: 'number', min: 0, max: 1, message: '范围为 0～1' },
                      ]}
                    >
                      <Slider
                        min={0}
                        max={1}
                        step={0.1}
                        tooltip={{ formatter: (v) => (v != null ? String(v) : '') }}
                      />
                    </Form.Item>
                  </div>
                  ) : null}
                  <Form.Item name="apiKey" label="API Key" rules={[{ required: true, message: '请填写 API Key' }]}>
                    <Input.Password placeholder="sk-xxxxxxxxxxxxxxxx" />
                  </Form.Item>
                  <Form.Item
                    name="baseUrl"
                    label="接口地址"
                    rules={[{ required: true, message: '请填写接口地址' }]}
                  >
                    <Input
                      placeholder={
                        apiProviderWatch === 'anthropic'
                          ? '建议 https://api.anthropic.com（或填代理 / 自建反代地址）'
                          : 'https://api.example.com/v1'
                      }
                    />
                  </Form.Item>
                </Form>
              </Modal>
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
                  <Button onClick={handleOpenDbDir}>打开数据库目录</Button>
                  <Button onClick={refreshDbInfo} loading={dbInfoLoading}>刷新统计</Button>
                </div>
              </div>
              <div className="settings-data-actions">
                <Button
                  type="default"
                  icon={<ExportOutlined />}
                  onClick={handleExportDatabase}
                  loading={exportingDb}
                >
                  导出数据库
                </Button>
                <Button
                  type="default"
                  icon={<ImportOutlined />}
                  onClick={handleImportDatabase}
                  loading={importingDb}
                  danger
                >
                  导入数据库
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
