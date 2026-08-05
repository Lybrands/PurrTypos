import { services } from '@/services'
import React from 'react'
import { PurrButton, PurrEmpty, PurrInput, PurrList, PurrPopover, PurrSpin, PurrTooltip } from '@/purr-components'
import { DeleteIcon, HistoryIcon } from '@/purr-components'
import type { AiSession, EntityId } from '../../../../types'
import './index.scss'

// ─── 日期工具 ─────────────────────────────────────────────────────────────────
// SQLite CURRENT_TIMESTAMP 存储的是 UTC 时间，解析时需加 'Z' 保证正确转换为本地时间

function parseUTCDate(dateStr: string): Date {
  return new Date(dateStr.replace(' ', 'T') + 'Z')
}

function getDateGroup(dateStr?: string): string {
  if (!dateStr) return '更早'
  const date = parseUTCDate(dateStr)
  if (isNaN(date.getTime())) return '更早'
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dStart = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const diffDays = Math.round((todayStart.getTime() - dStart.getTime()) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return '近 7 天'
  return '更早'
}

const DATE_GROUP_ORDER = ['今天', '昨天', '近 7 天', '更早']

// ─── Props ───────────────────────────────────────────────────────────────────

export interface SessionHistoryPopoverProps {
  bookId: EntityId | null | undefined
  chapterId: EntityId | null | undefined
  /** setting 时查询不绑章节的全局会话历史 */
  scope?: 'chapter' | 'setting'
  activeSessionId: number | null
  disabled?: boolean
  /** 用户点击某条历史对话时回调，父组件负责加入标签栏并激活 */
  onOpen: (session: AiSession) => void
  /** 用户删除某条历史对话后回调，父组件负责同步标签栏状态 */
  onDelete: (session: AiSession) => void
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function SessionHistoryPopover({
  bookId,
  chapterId,
  scope = 'chapter',
  activeSessionId,
  disabled = false,
  onOpen,
  onDelete,
}: SessionHistoryPopoverProps) {
  const [popoverOpen, setPopoverOpen] = React.useState(false)
  const [allSessions, setAllSessions] = React.useState<AiSession[]>([])
  const [loading, setLoading] = React.useState(false)
  const [loadError, setLoadError] = React.useState<string | null>(null)
  const [search, setSearch] = React.useState('')

  const loadSessions = React.useCallback(() => {
    if (bookId == null) {
      setAllSessions([])
      setLoadError('请先选择书籍')
      return
    }
    setSearch('')
    setLoadError(null)
    setLoading(true)
    services.sessions
      .getSessions(
        scope === 'setting'
          ? { bookId, includeClosed: true, scope: 'setting' }
          : { bookId, chapterId: chapterId ?? null, includeClosed: true },
      )
      .then((res) => {
        if (res.success) {
          setAllSessions(res.data)
          return
        }
        setAllSessions([])
        setLoadError(res.error || '历史对话加载失败')
      })
      .catch(() => {
        setAllSessions([])
        setLoadError('历史对话加载失败，请稍后重试')
      })
      .finally(() => setLoading(false))
  }, [bookId, chapterId, scope])

  const handleOpenChange = React.useCallback((visible: boolean) => {
    if (disabled) return
    setPopoverOpen(visible)
    if (visible) loadSessions()
  }, [disabled, loadSessions])

  const handleDelete = React.useCallback(async (session: AiSession, e: React.MouseEvent) => {
    e.stopPropagation()
    await services.sessions.deleteSession({ sessionId: session.id })
    setAllSessions((prev) => prev.filter((s) => s.id !== session.id))
    onDelete(session)
  }, [onDelete])

  const handleOpen = React.useCallback((session: AiSession) => {
    onOpen(session)
    setPopoverOpen(false)
  }, [onOpen])

  // 搜索过滤
  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase()
    return q ? allSessions.filter((s) => s.title.toLowerCase().includes(q)) : allSessions
  }, [allSessions, search])

  // 按日期分组（倒序：最新在前）
  const grouped = React.useMemo(() => {
    const map: Record<string, AiSession[]> = {}
    for (const s of [...filtered].reverse()) {
      const g = getDateGroup(s.create_time)
      if (!map[g]) map[g] = []
      map[g].push(s)
    }
    return DATE_GROUP_ORDER.filter((g) => map[g]?.length).map((g) => ({ group: g, items: map[g] }))
  }, [filtered])

  const content = (
    <div className="history-popover-body">
      <PurrInput.Search
        placeholder="搜索对话标题..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        allowClear
        className="history-search"
      />
      {loading ? (
        <div className="history-loading"><PurrSpin size="small" /></div>
      ) : loadError ? (
        <PurrEmpty image={false} description={loadError} className="history-empty" />
      ) : filtered.length === 0 ? (
        <PurrEmpty image={false} description="暂无历史对话" className="history-empty" />
      ) : (
        <div className="history-list">
          {grouped.map(({ group, items }) => (
            <div key={group} className="history-group">
              <div className="history-group-label">{group}</div>
              <PurrList
                dataSource={items}
                renderItem={(session) => (
                  <PurrList.Item
                    className={`history-item${activeSessionId === session.id ? ' active' : ''}`}
                    actions={[
                      <PurrTooltip title="删除" key="del">
                        <PurrButton
                          type="text"
                          size="small"
                          icon={<DeleteIcon />}
                          className="history-delete-btn"
                          onClick={(e) => handleDelete(session, e)}
                          aria-label={`删除对话：${session.title}`}
                        />
                      </PurrTooltip>,
                    ]}
                  >
                    <button
                      type="button"
                      className="history-item-open"
                      aria-current={activeSessionId === session.id ? 'page' : undefined}
                      onClick={() => handleOpen(session)}
                    >
                      <span className="history-item-title">{session.title}</span>
                    </button>
                  </PurrList.Item>
                )}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <PurrTooltip title="历史对话" mouseEnterDelay={0.5} placement="left" getPopupContainer={() => document.body}>
      <span className="purr-popup-trigger session-history-trigger">
        <PurrPopover
          content={content}
          title="历史对话"
          trigger="click"
          open={popoverOpen}
          onOpenChange={handleOpenChange}
          placement="bottomRight"
          overlayClassName="session-history-popover"
          arrow={false}
        >
        <PurrButton
          type="text"
          size="small"
          icon={<HistoryIcon />}
          className="session-new-btn"
          aria-label="打开历史对话"
          disabled={disabled || bookId == null}
        />
        </PurrPopover>
      </span>
    </PurrTooltip>
  )
}
