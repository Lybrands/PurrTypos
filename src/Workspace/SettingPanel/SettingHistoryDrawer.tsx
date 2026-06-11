import React from 'react'
import { App as AntdApp, Button, Drawer, Empty, Modal, Spin, Tag, Tooltip } from 'antd'
import { HistoryOutlined, RollbackOutlined } from '@ant-design/icons'
import type {
  CharacterSettingHistory,
  EntityId,
  StoryBackgroundSettingHistory,
} from '../../types'
import './SettingHistoryDrawer.scss'

type HistoryKind = 'character' | 'background'

interface SettingHistoryDrawerProps {
  kind: HistoryKind
  characterId?: number | null
  bookId?: EntityId | null
  entityTitle?: string
  open: boolean
  onClose: () => void
  onRestored?: () => void
}

const SOURCE_LABEL: Record<string, { color: string; text: string }> = {
  user: { color: 'blue', text: '手动编辑' },
  ai_tool: { color: 'purple', text: 'AI 修改' },
  ai_tool_edit: { color: 'purple', text: 'AI 修改' },
}

function renderSourceTag(source: string) {
  if (source.startsWith('rollback_of:')) return <Tag color="orange">回退</Tag>
  const meta = SOURCE_LABEL[source]
  if (meta) return <Tag color={meta.color}>{meta.text}</Tag>
  return <Tag>{source || 'unknown'}</Tag>
}

function previewText(text: string, max = 200): string {
  const t = (text || '').trim()
  if (!t) return '（空）'
  return t.length > max ? `${t.slice(0, max)}…` : t
}

export default function SettingHistoryDrawer({
  kind,
  characterId,
  bookId,
  entityTitle,
  open,
  onClose,
  onRestored,
}: SettingHistoryDrawerProps) {
  const { message: appMessage } = AntdApp.useApp()
  const [modal, modalCtx] = Modal.useModal()
  const [loading, setLoading] = React.useState(false)
  const [charItems, setCharItems] = React.useState<CharacterSettingHistory[]>([])
  const [bgItems, setBgItems] = React.useState<StoryBackgroundSettingHistory[]>([])
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
  const [detailLoadingId, setDetailLoadingId] = React.useState<number | null>(null)
  const [charDetail, setCharDetail] = React.useState<CharacterSettingHistory | null>(null)
  const [bgDetail, setBgDetail] = React.useState<StoryBackgroundSettingHistory | null>(null)
  const [restoringId, setRestoringId] = React.useState<number | null>(null)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      if (kind === 'character' && characterId != null) {
        const res = await window.electronAPI.listCharacterSettingHistory({ characterId, limit: 100 })
        if (res?.success) setCharItems(res.data ?? [])
        else appMessage.error('加载人物历史失败')
      } else if (kind === 'background' && bookId != null) {
        const res = await window.electronAPI.listBackgroundSettingHistory({ bookId, limit: 100 })
        if (res?.success) setBgItems(res.data ?? [])
        else appMessage.error('加载背景历史失败')
      }
    } catch {
      appMessage.error('加载历史失败')
    } finally {
      setLoading(false)
    }
  }, [kind, characterId, bookId, appMessage])

  React.useEffect(() => {
    if (open) {
      setExpandedId(null)
      setCharDetail(null)
      setBgDetail(null)
      reload()
    }
  }, [open, reload])

  const loadDetail = async (historyId: number) => {
    setDetailLoadingId(historyId)
    try {
      if (kind === 'character') {
        const res = await window.electronAPI.getCharacterSettingHistory({ historyId })
        if (res?.success && res.data) setCharDetail(res.data)
      } else {
        const res = await window.electronAPI.getBackgroundSettingHistory({ historyId })
        if (res?.success && res.data) setBgDetail(res.data)
      }
    } finally {
      setDetailLoadingId(null)
    }
  }

  const handleRestore = (historyId: number, preview: string) => {
    modal.confirm({
      title: '回退到此版本',
      content: (
        <div>
          <p>当前设定将被替换为这条历史的「之前」状态：</p>
          <pre className="setting-history-restore-preview">{preview.slice(0, 400)}{preview.length > 400 ? '…' : ''}</pre>
        </div>
      ),
      okText: '回退',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setRestoringId(historyId)
        try {
          const res = kind === 'character'
            ? await window.electronAPI.rollbackCharacterSettingHistory({ historyId })
            : await window.electronAPI.rollbackBackgroundSettingHistory({ historyId })
          if (res?.success) {
            appMessage.success('已回退到该版本')
            onRestored?.()
            reload()
          } else {
            appMessage.error(res?.error || '回退失败')
          }
        } finally {
          setRestoringId(null)
        }
      },
    })
  }

  const titleLabel = kind === 'character' ? '人物历史' : '背景历史'
  const items = kind === 'character' ? charItems : bgItems

  return (
    <Drawer
      title={(
        <span>
          <HistoryOutlined style={{ marginRight: 8 }} />
          {titleLabel}
          {entityTitle ? (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
              · {entityTitle}
            </span>
          ) : null}
        </span>
      )}
      placement="right"
      width={560}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {modalCtx}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : items.length === 0 ? (
        <Empty description="还没有修改历史" />
      ) : (
        <div className="setting-history-list">
          {kind === 'character'
            ? charItems.map((item) => {
              const preview = previewText(item.before_profile_md || item.after_profile_md)
              const isExpanded = expandedId === item.id
              return (
                <div key={item.id} className="setting-history-item">
                  <div className="setting-history-item-header">
                    <div className="setting-history-item-meta">
                      <span>#{item.id}</span>
                      {renderSourceTag(item.source)}
                      <span>接受 {item.accepted_segments} / 拒绝 {item.rejected_segments}</span>
                    </div>
                    <span className="setting-history-item-time">
                      {item.create_time ? new Date(item.create_time).toLocaleString() : '—'}
                    </span>
                  </div>
                  <div className="setting-history-item-preview">{preview}</div>
                  <div className="setting-history-item-actions">
                    <Button type="text" size="small" onClick={() => {
                      if (isExpanded) setExpandedId(null)
                      else {
                        setExpandedId(item.id)
                        void loadDetail(item.id)
                      }
                    }}>
                      {isExpanded ? '收起' : '查看完整内容'}
                    </Button>
                    <Tooltip title="把当前设定替换为此版本">
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<RollbackOutlined />}
                        loading={restoringId === item.id}
                        onClick={() => handleRestore(item.id, item.before_profile_md || '')}
                      >
                        回退到此版本
                      </Button>
                    </Tooltip>
                  </div>
                  {isExpanded && detailLoadingId === item.id && !charDetail ? <Spin size="small" /> : null}
                  {isExpanded && charDetail?.id === item.id ? (
                    <pre className="setting-history-item-full">{charDetail.before_profile_md || '（空）'}</pre>
                  ) : null}
                </div>
              )
            })
            : bgItems.map((item) => {
              const preview = previewText(item.before_content || item.after_content)
              const isExpanded = expandedId === item.id
              return (
                <div key={item.id} className="setting-history-item">
                  <div className="setting-history-item-header">
                    <div className="setting-history-item-meta">
                      <span>#{item.id}</span>
                      {renderSourceTag(item.source)}
                      <span>接受 {item.accepted_segments} / 拒绝 {item.rejected_segments}</span>
                    </div>
                    <span className="setting-history-item-time">
                      {item.create_time ? new Date(item.create_time).toLocaleString() : '—'}
                    </span>
                  </div>
                  <div className="setting-history-item-preview">{preview}</div>
                  <div className="setting-history-item-actions">
                    <Button type="text" size="small" onClick={() => {
                      if (isExpanded) setExpandedId(null)
                      else {
                        setExpandedId(item.id)
                        void loadDetail(item.id)
                      }
                    }}>
                      {isExpanded ? '收起' : '查看完整内容'}
                    </Button>
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<RollbackOutlined />}
                      loading={restoringId === item.id}
                      onClick={() => handleRestore(item.id, item.before_content || '')}
                    >
                      回退到此版本
                    </Button>
                  </div>
                  {isExpanded && detailLoadingId === item.id && !bgDetail ? <Spin size="small" /> : null}
                  {isExpanded && bgDetail?.id === item.id ? (
                    <pre className="setting-history-item-full">{bgDetail.before_content || '（空）'}</pre>
                  ) : null}
                </div>
              )
            })}
        </div>
      )}
    </Drawer>
  )
}
