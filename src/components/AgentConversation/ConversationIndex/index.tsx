import React from 'react'
import {
  CloseIcon,
  LoadingIcon,
  MessageIcon,
  PanelToggleIcon,
  PlusIcon,
  PurrButton,
  PurrEmpty,
  PurrInput,
  PurrTooltip,
} from '@/purr-components'
import type {
  AgentConversationActivity,
  AgentSessionId,
} from '../../../agent-runtime'
import type { AgentConversationSession } from '../controller'
import { sortConversationSessionsNewestFirst } from '../sessionView'
import './index.scss'

export type { AgentConversationActivity } from '../../../agent-runtime'

interface AgentConversationIndexProps<
  TSession extends AgentConversationSession = AgentConversationSession,
> {
  sessions: TSession[]
  activeSessionId: TSession['id'] | null
  editingSessionId: TSession['id'] | null
  editingTitle: string
  isCurrentSessionEmpty?: boolean
  context?: React.ReactNode
  extraActions?: React.ReactNode
  sessionActivities?: Partial<Record<AgentSessionId, AgentConversationActivity>>
  emptyDescription?: React.ReactNode
  onActiveSessionChange: (sessionId: TSession['id']) => void
  onEditingSessionIdChange: (sessionId: TSession['id'] | null) => void
  onEditingTitleChange: (title: string) => void
  onSaveTitle: () => void
  onNewSession: () => void
  onCloseSession: (session: TSession) => void
  onCollapse: () => void
}

function formatSessionTime(value?: string) {
  if (!value) return ''
  const date = new Date(value.replace(' ', 'T') + 'Z')
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export default function AgentConversationIndex<
  TSession extends AgentConversationSession = AgentConversationSession,
>({
  sessions,
  activeSessionId,
  editingSessionId,
  editingTitle,
  isCurrentSessionEmpty = false,
  context,
  extraActions,
  sessionActivities = {},
  emptyDescription = '暂无对话',
  onActiveSessionChange,
  onEditingSessionIdChange,
  onEditingTitleChange,
  onSaveTitle,
  onNewSession,
  onCloseSession,
  onCollapse,
}: AgentConversationIndexProps<TSession>) {
  const hasBlankSession = sessions.length > 0 && isCurrentSessionEmpty
  const orderedSessions = React.useMemo(
    () => sortConversationSessionsNewestFirst(sessions),
    [sessions],
  )

  return (
    <aside className="agent-conversation-index" aria-label="AI 对话记录">
      {context}
      <div className="agent-conversation-index__heading">
        <span>对话记录</span>
        <div className="agent-conversation-index__actions">
          <PurrTooltip title="收起对话列表">
            <PurrButton
              type="text"
              size="small"
              icon={<PanelToggleIcon side="left" state="expanded" />}
              onClick={onCollapse}
              aria-label="收起对话列表"
            />
          </PurrTooltip>
          {extraActions}
          <PurrTooltip title={hasBlankSession ? '当前对话尚未开始' : '新建对话'}>
            <PurrButton
              type="text"
              size="small"
              icon={<PlusIcon />}
              disabled={hasBlankSession}
              onClick={onNewSession}
              aria-label="新建对话"
            />
          </PurrTooltip>
        </div>
      </div>

      <div className="agent-conversation-index__list">
        {sessions.length === 0 ? (
          <PurrEmpty image={false} description={emptyDescription} />
        ) : orderedSessions.map((session) => {
          const active = session.id === activeSessionId
          const activity = sessionActivities[session.id]
          return (
            <div
              key={session.id}
              className={`agent-conversation-index__item${active ? ' is-active' : ''}${activity ? ` is-${activity.state}` : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => onActiveSessionChange(session.id)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                event.preventDefault()
                onActiveSessionChange(session.id)
              }}
            >
              <MessageIcon className="agent-conversation-index__icon" />
              <div className="agent-conversation-index__meta">
                {editingSessionId === session.id ? (
                  <PurrInput
                    size="small"
                    value={editingTitle}
                    autoFocus
                    onChange={(event) => onEditingTitleChange(event.target.value)}
                    onBlur={onSaveTitle}
                    onPressEnter={onSaveTitle}
                    onClick={(event) => event.stopPropagation()}
                    onKeyDown={(event) => event.stopPropagation()}
                  />
                ) : (
                  <span
                    className="agent-conversation-index__title"
                    title={session.title || '新对话'}
                    onDoubleClick={(event) => {
                      event.stopPropagation()
                      onEditingSessionIdChange(session.id)
                      onEditingTitleChange(session.title || '新对话')
                    }}
                  >
                    {session.title || '新对话'}
                  </span>
                )}
                <div className="agent-conversation-index__subline">
                  <span className="agent-conversation-index__time">
                    {formatSessionTime(session.createdAt)}
                  </span>
                  {activity?.state === 'running' ? (
                    <span
                      className="agent-conversation-index__status agent-conversation-index__status--running"
                      role="status"
                      aria-label="对话进行中"
                    >
                      <LoadingIcon spin />
                    </span>
                  ) : null}
                </div>
              </div>
              <PurrTooltip title="关闭对话">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<CloseIcon />}
                  className="agent-conversation-index__close"
                  onClick={(event) => {
                    event.stopPropagation()
                    onCloseSession(session)
                  }}
                  aria-label={`关闭对话 ${session.title || '新对话'}`}
                />
              </PurrTooltip>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
