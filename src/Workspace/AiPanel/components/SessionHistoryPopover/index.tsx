import React from 'react'
import { Button, Empty, Input, List, Popover, Spin, Tooltip } from 'antd'
import { DeleteOutlined, HistoryOutlined } from '@ant-design/icons'
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
  activeSessionId: number | null
  /** 用户点击某条历史对话时回调，父组件负责加入标签栏并激活 */
  onOpen: (session: AiSession) => void
  /** 用户删除某条历史对话后回调，父组件负责同步标签栏状态 */
  onDelete: (session: AiSession) => void
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function SessionHistoryPopover({
  bookId,
  chapterId,
  activeSessionId,
  onOpen,
  onDelete,
}: SessionHistoryPopoverProps) {
  const [popoverOpen, setPopoverOpen] = React.useState(false)
  const [allSessions, setAllSessions] = React.useState<AiSession[]>([])
  const [loading, setLoading] = React.useState(false)
  const [search, setSearch] = React.useState('')

  const loadSessions = React.useCallback(() => {
    if (bookId == null) return
    setSearch('')
    setLoading(true)
    window.electronAPI.getSessions({ bookId, chapterId: chapterId ?? null, includeClosed: true }).then((res) => {
      if (res.success) setAllSessions(res.data)
      setLoading(false)
    })
  }, [bookId, chapterId])

  const handleOpenChange = React.useCallback((visible: boolean) => {
    setPopoverOpen(visible)
    if (visible) loadSessions()
  }, [loadSessions])

  const handleDelete = React.useCallback(async (session: AiSession, e: React.MouseEvent) => {
    e.stopPropagation()
    await window.electronAPI.deleteSession({ sessionId: session.id })
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
      <Input.Search
        placeholder="搜索对话标题..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        allowClear
        className="history-search"
      />
      {loading ? (
        <div className="history-loading"><Spin size="small" /></div>
      ) : filtered.length === 0 ? (
        <Empty image={false} description="暂无历史对话" className="history-empty" />
      ) : (
        <div className="history-list">
          {grouped.map(({ group, items }) => (
            <div key={group} className="history-group">
              <div className="history-group-label">{group}</div>
              <List
                dataSource={items}
                renderItem={(session) => (
                  <List.Item
                    className={`history-item${activeSessionId === session.id ? ' active' : ''}`}
                    onClick={() => handleOpen(session)}
                    actions={[
                      <Tooltip title="删除" key="del">
                        <Button
                          type="text"
                          size="small"
                          icon={<DeleteOutlined />}
                          className="history-delete-btn"
                          onClick={(e) => handleDelete(session, e)}
                        />
                      </Tooltip>,
                    ]}
                  >
                    <div className="history-item-content">
                      <span className="history-item-title">{session.title}</span>
                    </div>
                  </List.Item>
                )}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <Popover
      content={content}
      title="历史对话"
      trigger="click"
      open={popoverOpen}
      onOpenChange={handleOpenChange}
      placement="bottomRight"
      overlayClassName="session-history-popover"
      arrow={false}
    >
      <Tooltip title="历史对话" mouseEnterDelay={0.5} placement="left" getPopupContainer={() => document.body}>
        <Button
          type="text"
          size="small"
          icon={<HistoryOutlined style={{ fontSize: 13 }} />}
          className="session-new-btn"
        />
      </Tooltip>
    </Popover>
  )
}
