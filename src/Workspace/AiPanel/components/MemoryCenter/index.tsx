import React from 'react'
import { Button, Empty, Input, List, Modal, Radio, Select, Space, Spin, Switch, Tag, Tooltip, message } from 'antd'
import { CheckCircleOutlined, ClockCircleOutlined, CloseOutlined, InboxOutlined, PlusOutlined, PushpinOutlined } from '@ant-design/icons'
import type { EntityId, MemoryItem, MemoryKind, MemoryStatus } from '../../../../types'
import './index.scss'

interface MemoryCenterProps {
  bookId: EntityId | null
}

const KIND_OPTIONS: { value: MemoryKind; label: string }[] = [
  { value: 'canon', label: '设定' },
  { value: 'plot', label: '剧情事实' },
  { value: 'character', label: '人物状态' },
  { value: 'world', label: '世界观' },
  { value: 'foreshadowing', label: '伏笔' },
  { value: 'style', label: '风格' },
  { value: 'summary', label: '总结' },
]

const STATUS_OPTIONS: { value: MemoryStatus; label: string }[] = [
  { value: 'pending', label: '待确认' },
  { value: 'active', label: '已启用' },
  { value: 'archived', label: '已归档' },
  { value: 'superseded', label: '被覆盖' },
]

const KIND_LABEL = Object.fromEntries(KIND_OPTIONS.map((item) => [item.value, item.label])) as Record<MemoryKind, string>
const STATUS_LABEL = Object.fromEntries(STATUS_OPTIONS.map((item) => [item.value, item.label])) as Record<MemoryStatus, string>

const KIND_HINTS: Record<MemoryKind, string> = {
  canon: '记录不会轻易改变的规则、身份或核心设定。',
  plot: '记录已经发生且后续剧情必须遵守的事实。',
  character: '记录人物当前的关系、立场、目标或状态变化。',
  world: '记录地点、组织、制度、能力体系等世界观信息。',
  foreshadowing: '记录需要在后文呼应、推进或回收的线索。',
  style: '记录叙事语气、表达偏好或必须遵守的写作规则。',
  summary: '记录阶段性结论；建议先保存为待确认，审核后再启用。',
}

const MEMORY_CONTENT_PLACEHOLDER = '请输入希望 AI 在后续创作中持续记住的内容…'

export default function MemoryCenter({ bookId }: MemoryCenterProps) {
  const [query, setQuery] = React.useState('')
  const [status, setStatus] = React.useState<MemoryStatus>('pending')
  const [kind, setKind] = React.useState<MemoryKind | undefined>()
  const [items, setItems] = React.useState<MemoryItem[]>([])
  const [loading, setLoading] = React.useState(false)
  const [creating, setCreating] = React.useState(false)
  const [createOpen, setCreateOpen] = React.useState(false)
  const [newKind, setNewKind] = React.useState<MemoryKind>('canon')
  const [newContent, setNewContent] = React.useState('')
  const [newStatus, setNewStatus] = React.useState<'active' | 'pending'>('active')
  const [newPinned, setNewPinned] = React.useState(false)
  const [intelligenceEnabled, setIntelligenceEnabled] = React.useState(false)
  const [intelligenceSaving, setIntelligenceSaving] = React.useState(false)

  const load = React.useCallback(async () => {
    if (bookId == null) {
      setItems([])
      return
    }
    setLoading(true)
    const res = await window.electronAPI.searchMemories({
      bookId,
      query,
      options: {
        statuses: [status],
        kinds: kind ? [kind] : undefined,
        limit: 80,
      },
    })
    setLoading(false)
    if (res.success && Array.isArray(res.data)) {
      setItems(res.data)
    } else {
      setItems([])
      message.error(res.error || '读取长期记忆失败')
    }
  }, [bookId, query, status, kind])

  React.useEffect(() => {
    load()
  }, [load])

  React.useEffect(() => {
    let cancelled = false
    window.electronAPI.getSettings().then((res) => {
      if (cancelled) return
      if (res.success) {
        setIntelligenceEnabled(!!res.data?.memory_intelligence_enabled)
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  const handleToggleIntelligence = React.useCallback(async (checked: boolean) => {
    setIntelligenceSaving(true)
    const res = await window.electronAPI.setSettings({ memory_intelligence_enabled: checked })
    setIntelligenceSaving(false)
    if (res.success) {
      setIntelligenceEnabled(checked)
      message.success(checked ? '已开启高级智能记忆' : '已关闭高级智能记忆')
    } else {
      message.error(res.error || '保存高级智能记忆开关失败')
    }
  }, [])

  const handleCreate = React.useCallback(async () => {
    if (bookId == null || !newContent.trim()) return
    setCreating(true)
    const res = await window.electronAPI.createMemory({
      bookId,
      kind: newKind,
      content: newContent.trim(),
      status: newStatus,
      pinned: newPinned,
      sourceType: 'manual',
    })
    setCreating(false)
    if (res.success) {
      setNewContent('')
      setNewPinned(false)
      setCreateOpen(false)
      message.success(res.data?.deduped ? '已有相同记忆，已更新时间' : '已保存长期记忆')
      load()
    } else {
      message.error(res.error || '保存长期记忆失败')
    }
  }, [bookId, newKind, newContent, newPinned, newStatus, load])

  const handleNewKindChange = React.useCallback((nextKind: MemoryKind) => {
    setNewKind(nextKind)
    setNewStatus(nextKind === 'summary' ? 'pending' : 'active')
  }, [])

  const updateItem = React.useCallback(async (item: MemoryItem, data: Partial<MemoryItem>) => {
    const res = await window.electronAPI.updateMemory({ id: item.id, data })
    if (res.success) {
      setItems((prev) => prev.map((row) => (row.id === item.id ? res.data : row)))
    } else {
      message.error(res.error || '更新长期记忆失败')
    }
  }, [])

  const handleArchive = React.useCallback((item: MemoryItem) => {
    Modal.confirm({
      title: '归档这条记忆？',
      content: '归档后默认不会自动召回，但仍可在已归档筛选中查看。',
      okText: '归档',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await window.electronAPI.archiveMemory({ id: item.id })
        if (res.success) {
          setItems((prev) => prev.filter((row) => row.id !== item.id))
        } else {
          message.error(res.error || '归档失败')
        }
      },
    })
  }, [])

  const renderActions = (item: MemoryItem) => (
    <Space size={6}>
      {item.status === 'pending' ? (
        <Button size="small" type="primary" onClick={() => updateItem(item, { status: 'active' })}>
          确认
        </Button>
      ) : null}
      <Tooltip title={item.pinned ? '取消固定' : '固定优先召回'}>
        <Button
          size="small"
          type={item.pinned ? 'primary' : 'default'}
          icon={<PushpinOutlined />}
          onClick={() => updateItem(item, { pinned: item.pinned ? 0 : 1 })}
        />
      </Tooltip>
      {item.status !== 'archived' ? (
        <Button size="small" icon={<InboxOutlined />} onClick={() => handleArchive(item)}>
          归档
        </Button>
      ) : null}
    </Space>
  )

  return (
    <div className="memory-center">
      <div className="memory-center-intelligence">
        <div>
          <div className="memory-center-intelligence-title">高级智能记忆</div>
          <div className="memory-center-intelligence-desc">
            开启后，AI 接受的改动会调用模型提炼待确认候选，并尝试识别冲突、替代和伏笔线索。
          </div>
        </div>
        <Switch
          checked={intelligenceEnabled}
          loading={intelligenceSaving}
          onChange={handleToggleIntelligence}
        />
      </div>

      <div className="memory-center-toolbar">
        <div className="memory-center-filters">
          <Input.Search
            allowClear
            placeholder="搜索长期记忆、伏笔、人物状态..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={load}
          />
          <Select value={status} options={STATUS_OPTIONS} onChange={setStatus} className="memory-center-status-filter" />
          <Select
            allowClear
            placeholder="全部类型"
            value={kind}
            options={KIND_OPTIONS}
            onChange={setKind}
            className="memory-center-kind-filter"
          />
        </div>
      </div>

      <section
        className={`memory-center-create ${createOpen ? 'is-open' : 'is-collapsed'}`}
        aria-label="手动添加长期记忆"
      >
        {!createOpen ? (
          <button
            type="button"
            className="memory-center-create-launcher"
            onClick={() => setCreateOpen(true)}
            disabled={bookId == null}
          >
            <span className="memory-center-create-launcher-icon"><PlusOutlined /></span>
            <span className="memory-center-create-launcher-copy">
              <span className="memory-center-create-launcher-title">手动添加记忆</span>
              <span className="memory-center-create-launcher-desc">
                {bookId == null ? '请先选择一本书' : '补充设定、剧情事实、人物状态或伏笔'}
              </span>
            </span>
            <span className="memory-center-create-launcher-action">添加</span>
          </button>
        ) : (
          <div className="memory-center-create-body">
            <div className="memory-center-create-header">
              <div>
                <div className="memory-center-create-title">手动添加记忆</div>
                <div className="memory-center-create-desc">写下希望 AI 在后续创作中持续记住的信息。</div>
              </div>
              <Button
                type="text"
                size="small"
                icon={<CloseOutlined />}
                onClick={() => setCreateOpen(false)}
                aria-label="关闭添加记忆"
              />
            </div>

            <div className="memory-center-create-field">
              <div className="memory-center-create-label">记忆类型</div>
              <Radio.Group
                value={newKind}
                onChange={(event) => handleNewKindChange(event.target.value as MemoryKind)}
                optionType="button"
                buttonStyle="solid"
                options={KIND_OPTIONS}
                className="memory-center-kind-options"
              />
              <div className="memory-center-create-hint">{KIND_HINTS[newKind]}</div>
            </div>

            <div className="memory-center-create-field">
              <div className="memory-center-create-field-header">
                <div className="memory-center-create-label">记忆内容</div>
                <span className="memory-center-create-count">{newContent.length} / 2000</span>
              </div>
              <Input.TextArea
                autoFocus
                autoSize={{ minRows: 3, maxRows: 7 }}
                maxLength={2000}
                value={newContent}
                onChange={(e) => setNewContent(e.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                    event.preventDefault()
                    void handleCreate()
                  }
                }}
                placeholder={MEMORY_CONTENT_PLACEHOLDER}
              />
            </div>

            <div className="memory-center-create-options">
              <div className="memory-center-create-status">
                <div className="memory-center-create-label">保存后状态</div>
                <Radio.Group
                  value={newStatus}
                  onChange={(event) => setNewStatus(event.target.value as 'active' | 'pending')}
                  className="memory-center-create-status-choices"
                >
                  <Radio.Button value="active">
                    <CheckCircleOutlined />
                    <span className="memory-center-create-status-copy">
                      <span className="memory-center-create-status-title">立即生效</span>
                      <span className="memory-center-create-status-desc">马上参与召回</span>
                    </span>
                  </Radio.Button>
                  <Radio.Button value="pending">
                    <ClockCircleOutlined />
                    <span className="memory-center-create-status-copy">
                      <span className="memory-center-create-status-title">待确认</span>
                      <span className="memory-center-create-status-desc">审核后生效</span>
                    </span>
                  </Radio.Button>
                </Radio.Group>
              </div>
              <div className="memory-center-create-pinned">
                <div>
                  <div className="memory-center-create-label">固定优先召回</div>
                  <div className="memory-center-create-hint">固定后，AI 会优先召回。</div>
                </div>
                <Switch size="small" checked={newPinned} onChange={setNewPinned} />
              </div>
            </div>

            <div className="memory-center-create-footer">
              <span className="memory-center-create-shortcut">Ctrl/⌘ + Enter 快速添加</span>
              <Space size={8}>
                <Button onClick={() => setCreateOpen(false)}>取消</Button>
                <Button
                  type="primary"
                  loading={creating}
                  disabled={!newContent.trim()}
                  onClick={() => void handleCreate()}
                >
                  添加到记忆库
                </Button>
              </Space>
            </div>
          </div>
        )}
      </section>

      <Spin spinning={loading}>
        <List
          className="memory-center-list"
          dataSource={items}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无长期记忆" /> }}
          renderItem={(item) => (
            <List.Item actions={[renderActions(item)]}>
              <List.Item.Meta
                title={
                  <Space size={6} wrap>
                    <Tag>{KIND_LABEL[item.kind] || item.kind}</Tag>
                    <Tag>{STATUS_LABEL[item.status] || item.status}</Tag>
                    {item.pinned ? <Tag>固定</Tag> : null}
                    <span className="memory-center-item-source">{item.source_type}</span>
                  </Space>
                }
                description={
                  <div className="memory-center-item-body">
                    <div>{item.content}</div>
                    {item.summary ? <div className="memory-center-item-summary">{item.summary}</div> : null}
                    {item.keywords ? <div className="memory-center-item-keywords">关键词：{item.keywords}</div> : null}
                  </div>
                }
              />
            </List.Item>
          )}
        />
      </Spin>
    </div>
  )
}
