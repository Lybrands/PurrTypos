import React from 'react'
import {
  DeleteIcon,
  HistoryIcon,
  PurrButton,
  PurrEmpty,
  PurrInput,
  PurrList,
  PurrPopover,
  PurrSpin,
  PurrTooltip,
} from '@/purr-components'
import type { AgentSessionId } from '../../../../agent-runtime'
import type {
  AgentConversationController,
  AgentConversationSession,
} from '../../controller'
import './index.scss'

export interface AgentConversationHistoryController {
  conversation: Pick<
    AgentConversationController['conversation'],
    'activeSessionId' | 'history'
  >
  actions: Pick<
    AgentConversationController['actions'],
    'loadSessionHistory' | 'openHistorySession' | 'deleteSession'
  >
}

export interface SessionHistoryProps {
  controller: AgentConversationHistoryController
  disabled?: boolean
}

function parseUTCDate(dateStr: string): Date {
  return new Date(dateStr.replace(' ', 'T') + 'Z')
}

function getDateGroup(dateStr?: string): string {
  if (!dateStr) return '更早'
  const date = parseUTCDate(dateStr)
  if (Number.isNaN(date.getTime())) return '更早'
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dateStart = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const diffDays = Math.round((todayStart.getTime() - dateStart.getTime()) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return '近 7 天'
  return '更早'
}

const DATE_GROUP_ORDER = ['今天', '昨天', '近 7 天', '更早']

export default function SessionHistory({
  controller,
  disabled = false,
}: SessionHistoryProps) {
  const [popoverOpen, setPopoverOpen] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const history = controller.conversation.history
  const sessions = history?.sessions ?? []

  const handleOpenChange = React.useCallback((open: boolean) => {
    if (disabled) return
    setPopoverOpen(open)
    if (open) {
      setSearch('')
      void controller.actions.loadSessionHistory?.()
    }
  }, [controller.actions, disabled])

  const handleOpen = React.useCallback((id: AgentSessionId) => {
    void controller.actions.openHistorySession?.(id)
    setPopoverOpen(false)
  }, [controller.actions])

  const handleDelete = React.useCallback((
    id: AgentSessionId,
    event: React.MouseEvent,
  ) => {
    event.stopPropagation()
    void controller.actions.deleteSession?.(id)
  }, [controller.actions])

  const filtered = React.useMemo(() => {
    const query = search.trim().toLowerCase()
    return query
      ? sessions.filter((session) => session.title.toLowerCase().includes(query))
      : sessions
  }, [search, sessions])

  const grouped = React.useMemo(() => {
    const groups: Record<string, AgentConversationSession[]> = {}
    for (const session of [...filtered].reverse()) {
      const group = getDateGroup(session.createdAt)
      if (!groups[group]) groups[group] = []
      groups[group].push(session)
    }
    return DATE_GROUP_ORDER
      .filter((group) => groups[group]?.length)
      .map((group) => ({ group, items: groups[group] }))
  }, [filtered])

  const content = (
    <div className="history-popover-body">
      <PurrInput.Search
        placeholder="搜索对话标题..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        allowClear
        className="history-search"
      />
      {history?.loading ? (
        <div className="history-loading"><PurrSpin size="small" /></div>
      ) : history?.error ? (
        <PurrEmpty image={false} description={history.error} className="history-empty" />
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
                    className={`history-item${controller.conversation.activeSessionId === session.id ? ' active' : ''}`}
                    actions={controller.actions.deleteSession ? [
                      <PurrTooltip title="删除" key="delete">
                        <PurrButton
                          type="text"
                          size="small"
                          icon={<DeleteIcon />}
                          className="history-delete-btn"
                          onClick={(event) => handleDelete(session.id, event)}
                          aria-label={`删除对话：${session.title}`}
                        />
                      </PurrTooltip>,
                    ] : undefined}
                  >
                    <button
                      type="button"
                      className="history-item-open"
                      aria-current={controller.conversation.activeSessionId === session.id ? 'page' : undefined}
                      onClick={() => handleOpen(session.id)}
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
            disabled={disabled || !history || !controller.actions.loadSessionHistory}
          />
        </PurrPopover>
      </span>
    </PurrTooltip>
  )
}
