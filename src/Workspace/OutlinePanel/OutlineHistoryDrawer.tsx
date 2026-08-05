import { services } from '@/services'
import React from 'react'
import { PurrButton, PurrDrawer, PurrEmpty, PurrSpin, PurrTag, PurrTooltip, usePurrConfirm, usePurrToast } from '@/purr-components'
import { HistoryIcon, RollbackIcon } from '@/purr-components'
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
    return <PurrTag color="orange">回退</PurrTag>
  }
  const meta = SOURCE_LABEL[source]
  if (meta) return <PurrTag color={meta.color}>{meta.text}</PurrTag>
  return <PurrTag>{source || 'unknown'}</PurrTag>
}

export default function OutlineHistoryDrawer({
  outlineId, outlineTitle, open, onClose, onRestored,
}: OutlineHistoryDrawerProps) {
  const appMessage = usePurrToast()
  const confirm = usePurrConfirm()
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
      const res = await services.outlines.listOutlineHistory({ outlineId, limit: 100 })
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
      const res = await services.outlines.getOutlineHistory({ historyId })
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
    void confirm({
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
      confirmText: '回退',
      confirmVariant: 'danger',
      cancelText: '取消',
    }).then(async (result) => {
      if (result === 'confirm') {
        setRestoringId(item.id)
        try {
          const res = await services.outlines.restoreOutlineHistory({ historyId: item.id })
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
      }
    })
  }

  return (
    <PurrDrawer
      title={
        <span>
          <HistoryIcon style={{ marginRight: 8 }} />
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
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><PurrSpin /></div>
      ) : items.length === 0 ? (
        <PurrEmpty description="该大纲还没有修改历史" />
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
                      <PurrTooltip title={item.note}>
                        <span className="outline-history-item-note">{item.note}</span>
                      </PurrTooltip>
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
                  <PurrButton
                    type="text"
                    size="small"
                    onClick={() => handleToggleExpand(item)}
                  >
                    {isExpanded ? '收起' : '查看完整内容'}
                  </PurrButton>
                  <PurrTooltip title="把当前大纲替换为此版本">
                    <PurrButton
                      type="text"
                      size="small"
                      danger
                      icon={<RollbackIcon />}
                      loading={restoringId === item.id}
                      onClick={() => handleRestore(item)}
                    >
                      回退到此版本
                    </PurrButton>
                  </PurrTooltip>
                </div>

                {isExpanded ? (
                  <div className="outline-history-item-full">
                    {detailLoadingId === item.id && !detail ? (
                      <PurrSpin size="small" />
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
    </PurrDrawer>
  )
}
