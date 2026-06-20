import React from 'react'
import { Button, Empty, Input, List, Modal, Select, Space, Spin, Switch, Tag, Tooltip, message } from 'antd'
import { PushpinOutlined, InboxOutlined } from '@ant-design/icons'
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

export default function MemoryCenter({ bookId }: MemoryCenterProps) {
  const [query, setQuery] = React.useState('')
  const [status, setStatus] = React.useState<MemoryStatus>('pending')
  const [kind, setKind] = React.useState<MemoryKind | undefined>()
  const [items, setItems] = React.useState<MemoryItem[]>([])
  const [loading, setLoading] = React.useState(false)
  const [creating, setCreating] = React.useState(false)
  const [newKind, setNewKind] = React.useState<MemoryKind>('summary')
  const [newContent, setNewContent] = React.useState('')
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
      status: newKind === 'summary' ? 'pending' : 'active',
      sourceType: 'manual',
    })
    setCreating(false)
    if (res.success) {
      setNewContent('')
      message.success(res.data?.deduped ? '已有相同记忆，已更新时间' : '已保存长期记忆')
      load()
    } else {
      message.error(res.error || '保存长期记忆失败')
    }
  }, [bookId, newKind, newContent, load])

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
        <Input.Search
          allowClear
          placeholder="搜索长期记忆、伏笔、人物状态..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onSearch={load}
        />
        <Select value={status} options={STATUS_OPTIONS} onChange={setStatus} style={{ width: 112 }} />
        <Select
          allowClear
          placeholder="全部类型"
          value={kind}
          options={KIND_OPTIONS}
          onChange={setKind}
          style={{ width: 128 }}
        />
      </div>

      <div className="memory-center-create">
        <Select value={newKind} options={KIND_OPTIONS} onChange={setNewKind} style={{ width: 128 }} />
        <Input.TextArea
          rows={2}
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          placeholder="手动添加一条长期记忆。阶段总结默认进入待确认，其余默认启用。"
        />
        <Button type="primary" loading={creating} onClick={handleCreate}>
          保存
        </Button>
      </div>

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
