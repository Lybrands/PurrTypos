import React from 'react'
import { App as AntdApp, Button, Drawer, Empty, Modal, Spin, Tag, Tooltip } from 'antd'
import { HistoryOutlined, RollbackOutlined } from '@ant-design/icons'
import type { EntityId, OutlineHistoryDetail, OutlineHistoryListItem } from '../../types'
import './OutlineHistoryDrawer.scss'

interface OutlineHistoryDrawerProps {
  outlineId: EntityId | null
  outlineTitle?: string
  open: boolean
  onClose: () => void
  /** 回退成功后通知父级刷新大纲内容 */
  onRestored?: (outlineId: EntityId) => void
}

const SOURCE_LABEL: Record<string, { color: string; text: string }> = {
  user: { color: 'blue', text: '手动编辑' },
  ai_tool: { color: 'purple', text: 'AI 修改' },
}

function renderSourceTag(source: string) {
  if (source.startsWith('rollback_of:')) {
    return <Tag color="orange">回退</Tag>
  }
  const meta = SOURCE_LABEL[source]
  if (meta) return <Tag color={meta.color}>{meta.text}</Tag>
  return <Tag>{source || 'unknown'}</Tag>
}

export default function OutlineHistoryDrawer({
  outlineId, outlineTitle, open, onClose, onRestored,
}: OutlineHistoryDrawerProps) {
  const { message: appMessage } = AntdApp.useApp()
  const [modal, modalCtx] = Modal.useModal()
  const [loading, setLoading] = React.useState(false)
  const [items, setItems] = React.useState<OutlineHistoryListItem[]>([])
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
  const [detailMap, setDetailMap] = React.useState<Record<number, OutlineHistoryDetail>>({})
  const [detailLoadingId, setDetailLoadingId] = React.useState<number | null>(null)
  const [restoringId, setRestoringId] = React.useState<number | null>(null)

  const reload = React.useCallback(async () => {
    if (outlineId == null) return
    setLoading(true)
    try {
      const res = await window.electronAPI.listOutlineHistory({ outlineId, limit: 100 })
      if (res?.success) setItems(res.data ?? [])
      else appMessage.error('加载大纲历史失败')
    } catch (e) {
      console.error('[OutlineHistoryDrawer] list failed', e)
      appMessage.error('加载大纲历史失败')
    } finally {
      setLoading(false)
    }
  }, [outlineId, appMessage])

  React.useEffect(() => {
    if (open) {
      setExpandedId(null)
      setDetailMap({})
      reload()
    }
  }, [open, reload])

  const ensureDetail = React.useCallback(async (historyId: number) => {
    if (detailMap[historyId]) return
    setDetailLoadingId(historyId)
    try {
      const res = await window.electronAPI.getOutlineHistory({ historyId })
      if (res?.success && res.data) {
        setDetailMap((prev) => ({ ...prev, [historyId]: res.data! }))
      } else {
        appMessage.error('加载历史详情失败')
      }
    } catch (e) {
      console.error('[OutlineHistoryDrawer] detail failed', e)
      appMessage.error('加载历史详情失败')
    } finally {
      setDetailLoadingId(null)
    }
  }, [detailMap, appMessage])

  const handleToggleExpand = (item: OutlineHistoryListItem) => {
    if (expandedId === item.id) {
      setExpandedId(null)
      return
    }
    setExpandedId(item.id)
    void ensureDetail(item.id)
  }

  const handleRestore = (item: OutlineHistoryListItem) => {
    modal.confirm({
      title: '回退到此版本',
      content: (
        <div>
          <p>当前大纲将被替换为这条历史的「之前」状态：</p>
          <pre className="outline-history-restore-preview">
            {(item.markdown_preview || '（此快照无 Markdown 正文）').slice(0, 400)}
            {item.markdown_length > 400 ? '…' : ''}
          </pre>
          <p className="outline-history-restore-hint">
            会插入一条「回退」历史，之后还能再回退回去。
          </p>
        </div>
      ),
      okText: '回退',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setRestoringId(item.id)
        try {
          const res = await window.electronAPI.restoreOutlineHistory({ historyId: item.id })
          if (res?.success && outlineId != null) {
            appMessage.success('已回退到该版本')
            onRestored?.(outlineId)
            reload()
          } else {
            appMessage.error(res?.error || '回退失败')
          }
        } catch (e) {
          console.error('[OutlineHistoryDrawer] restore failed', e)
          appMessage.error('回退失败')
        } finally {
          setRestoringId(null)
        }
      },
    })
  }

  return (
    <Drawer
      title={
        <span>
          <HistoryOutlined style={{ marginRight: 8 }} />
          大纲历史
          {outlineTitle ? (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
              · {outlineTitle}
            </span>
          ) : null}
        </span>
      }
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
        <Empty description="该大纲还没有修改历史" />
      ) : (
        <div className="outline-history-list">
          {items.map((item) => {
            const isExpanded = expandedId === item.id
            const detail = detailMap[item.id]
            return (
              <div key={item.id} className="outline-history-item">
                <div className="outline-history-item-header">
                  <div className="outline-history-item-meta">
                    <span className="outline-history-item-id">#{item.id}</span>
                    {renderSourceTag(item.source)}
                    {item.note ? (
                      <Tooltip title={item.note}>
                        <span className="outline-history-item-note">{item.note}</span>
                      </Tooltip>
                    ) : null}
                  </div>
                  <span className="outline-history-item-time">
                    {item.create_time ? new Date(item.create_time).toLocaleString() : '—'}
                  </span>
                </div>

                {item.markdown_preview ? (
                  <div className="outline-history-item-preview">
                    {item.markdown_preview}
                    {item.markdown_length > (item.markdown_preview?.length ?? 0) ? '…' : ''}
                  </div>
                ) : (
                  <div className="outline-history-item-preview outline-history-item-preview--empty">
                    （此快照无 Markdown 正文）
                  </div>
                )}

                <div className="outline-history-item-actions">
                  <Button
                    type="text"
                    size="small"
                    onClick={() => handleToggleExpand(item)}
                  >
                    {isExpanded ? '收起' : '查看完整内容'}
                  </Button>
                  <Tooltip title="把当前大纲替换为此版本">
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<RollbackOutlined />}
                      loading={restoringId === item.id}
                      onClick={() => handleRestore(item)}
                    >
                      回退到此版本
                    </Button>
                  </Tooltip>
                </div>

                {isExpanded ? (
                  <div className="outline-history-item-full">
                    {detailLoadingId === item.id && !detail ? (
                      <Spin size="small" />
                    ) : detail ? (
                      <pre className="outline-history-item-full-text">
                        {detail.before_markdown_content || '（空）'}
                      </pre>
                    ) : (
                      <span className="outline-history-item-preview--empty">未能加载</span>
                    )}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}
    </Drawer>
  )
}
