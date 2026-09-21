import { services } from '@/services'
import { notifyChapterContentUpdated } from '../../stores/workspaceStore'
import React from 'react'
import { PurrButton, PurrDrawer, PurrEmpty, PurrSpin, PurrTag, PurrTooltip, usePurrConfirm, usePurrToast } from '@/purr-components'
import { HistoryIcon, RollbackIcon } from '@/purr-components'
import type { ChapterDiffHistory, EntityId } from '../../types'
import { diffParagraphs } from './paragraphDiff'
import './diff.scss'

interface DiffHistoryDrawerProps {
  chapterId: EntityId | null
  chapterTitle: string
  open: boolean
  onClose: () => void
}

/**
 * 章节 diff 历史面板：
 * - 列出该章节所有 commit 过的 diff（含 source / 接受/拒绝段数 / 时间）
 * - 点击「查看」展开段落对比（只读，复用段落 diff 算法）
 * - 点击「回滚」把该 diff 的 before_text 恢复为当前正文（会插入一条 rollback_of 历史）
 */
export default function DiffHistoryDrawer({
  chapterId, chapterTitle, open, onClose,
}: DiffHistoryDrawerProps) {
  const appMessage = usePurrToast()
  const confirm = usePurrConfirm()
  const [loading, setLoading] = React.useState(false)
  const [items, setItems] = React.useState<ChapterDiffHistory[]>([])
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
  const [rollbackingId, setRollbackingId] = React.useState<number | null>(null)

  const reload = React.useCallback(async () => {
    if (chapterId == null) return
    setLoading(true)
    try {
      const res = await services.history.listChapterDiff({ chapterId, limit: 100 })
      if (res?.success) setItems(res.data ?? [])
      else appMessage.error('加载 diff 历史失败')
    } catch (e) {
      console.error('[DiffHistoryDrawer] list failed', e)
      appMessage.error('加载 diff 历史失败')
    } finally {
      setLoading(false)
    }
  }, [chapterId, appMessage])

  React.useEffect(() => {
    if (open) {
      setExpandedId(null)
      reload()
    }
  }, [open, reload])

  const handleRollback = (item: ChapterDiffHistory) => {
    void confirm({
      title: '回滚到此版本',
      content: (
        <div>
          <p>当前正文将被替换为：</p>
          <p style={{ background: 'var(--bg-surface)', padding: 8, borderRadius: 4, whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto' }}>
            {(item.before_text || '（空）').slice(0, 400)}
            {(item.before_text || '').length > 400 ? '…' : ''}
          </p>
          <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            会插入一条「rollback_of:{item.id}」的历史，你之后还能再回滚回去。
          </p>
        </div>
      ),
      confirmText: '回滚',
      confirmVariant: 'danger',
      cancelText: '取消',
    }).then(async (result) => {
      if (result === 'confirm') {
        setRollbackingId(item.id)
        try {
          const res = await services.history.rollbackChapterDiff({ diffId: item.id })
          if (res?.success && chapterId != null) {
            appMessage.success('已回滚')
            notifyChapterContentUpdated(chapterId)
            reload()
          } else {
            appMessage.error('回滚失败')
          }
        } catch (e) {
          console.error('[DiffHistoryDrawer] rollback failed', e)
          appMessage.error('回滚失败')
        } finally {
          setRollbackingId(null)
        }
      }
    })
  }

  return (
    <PurrDrawer
      title={
        <span>
          <HistoryIcon style={{ marginRight: 8 }} />
          diff 历史
          {chapterTitle ? <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>· {chapterTitle}</span> : null}
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
        <PurrEmpty description="该章节还没有 diff 历史" />
      ) : (
        <div className="diff-history-list">
          {items.map((item) => {
            const isExpanded = expandedId === item.id
            const isRollback = (item.source || '').startsWith('rollback_of:')
            return (
              <div key={item.id} className="diff-history-item">
                <div className="diff-history-item-header">
                  <div className="diff-history-item-meta">
                    <span className="diff-history-item-id">#{item.id}</span>
                    {isRollback ? (
                      <PurrTag color="orange">回滚</PurrTag>
                    ) : (
                      <PurrTag color="blue">{item.source || 'unknown'}</PurrTag>
                    )}
                    <span className="diff-history-item-time">
                      {item.create_time ? new Date(item.create_time).toLocaleString() : '—'}
                    </span>
                  </div>
                  <div className="diff-history-item-stats">
                    {item.accepted_segments > 0 && <PurrTag color="success">接受 {item.accepted_segments}</PurrTag>}
                    {item.rejected_segments > 0 && <PurrTag>拒绝 {item.rejected_segments}</PurrTag>}
                  </div>
                </div>
                <div className="diff-history-item-actions">
                  <PurrButton
                    type="text"
                    size="small"
                    onClick={() => setExpandedId(isExpanded ? null : item.id)}
                  >
                    {isExpanded ? '收起对比' : '查看对比'}
                  </PurrButton>
                  <PurrTooltip title="把当前正文替换为此版本的「之前」状态">
                    <PurrButton
                      type="text"
                      size="small"
                      danger
                      icon={<RollbackIcon />}
                      loading={rollbackingId === item.id}
                      onClick={() => handleRollback(item)}
                    >
                      回滚到此版本
                    </PurrButton>
                  </PurrTooltip>
                </div>
                {isExpanded ? <DiffHistoryPreview item={item} /> : null}
              </div>
            )
          })}
        </div>
      )}
    </PurrDrawer>
  )
}

function DiffHistoryPreview({ item }: { item: ChapterDiffHistory }) {
  const ops = React.useMemo(
    () => diffParagraphs(item.before_text || '', item.after_text || ''),
    [item],
  )
  return (
    <div className="diff-history-preview">
      {ops.length === 0 ? (
        <div className="diff-overlay-empty">两个版本相同。</div>
      ) : (
        ops.map((op) => {
          if (op.kind === 'equal') {
            return (
              <div key={op.index} className="diff-row diff-row-equal diff-row-readonly">
                <div className="diff-row-marker">＝</div>
                <div className="diff-row-content">
                  <p className="diff-text-equal">{op.before || <span className="diff-empty">（空行）</span>}</p>
                </div>
              </div>
            )
          }
          return (
            <div key={op.index} className={`diff-row diff-row-${op.kind} diff-row-readonly`}>
              <div className="diff-row-marker">
                {op.kind === 'insert' ? '＋' : op.kind === 'delete' ? '－' : '↻'}
              </div>
              <div className="diff-row-content">
                {(op.kind === 'delete' || op.kind === 'replace') && (
                  <p className="diff-text-before">{op.before || <span className="diff-empty">（空段）</span>}</p>
                )}
                {(op.kind === 'insert' || op.kind === 'replace') && (
                  <p className="diff-text-after">{op.after || <span className="diff-empty">（空段）</span>}</p>
                )}
              </div>
            </div>
          )
        })
      )}
    </div>
  )
}
