import React from 'react'
import {
  CheckCircleIcon,
  CloseIcon,
  ClockIcon,
  LoadingIcon,
  MessageIcon,
  PanelToggleIcon,
  PauseCircleIcon,
  PlusIcon,
  PurrButton,
  PurrEmpty,
  PurrInput,
  PurrTooltip,
} from '@/purr-components'
import type { AiSession } from '../../types'
import './index.scss'

export type AgentConversationActivityState =
  | 'running'
  | 'paused'
  | 'queued'
  | 'completed'
  | 'failed'
  | 'canceled'

export interface AgentConversationActivity {
  state: AgentConversationActivityState
  queuedCount: number
}

interface AgentConversationIndexProps {
  sessions: AiSession[]
  activeSessionId: number | null
  editingSessionId: number | null
  editingTitle: string
  isCurrentSessionEmpty?: boolean
  disabled?: boolean
  context?: React.ReactNode
  extraActions?: React.ReactNode
  sessionActivities?: Record<number, AgentConversationActivity>
  getActivityLabel?: (activity: AgentConversationActivity) => string
  emptyDescription?: React.ReactNode
  onActiveSessionChange: (sessionId: number) => void
  onEditingSessionIdChange: (sessionId: number | null) => void
  onEditingTitleChange: (title: string) => void
  onSaveTitle: () => void
  onNewSession: () => void
  onCloseSession: (session: AiSession) => void
  onCollapse: () => void
}

function ActivityIcon({ activity }: { activity: AgentConversationActivity }) {
  if (activity.state === 'running') return <LoadingIcon spin />
  if (activity.state === 'queued') return <ClockIcon />
  if (activity.state === 'paused') return <PauseCircleIcon />
  if (activity.state === 'completed') return <CheckCircleIcon />
  if (activity.state === 'canceled') return <PauseCircleIcon />
  return <CloseIcon />
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

function defaultActivityLabel(activity: AgentConversationActivity) {
  if (activity.state === 'running') return '生成中'
  if (activity.state === 'queued') return '等待发送'
  if (activity.state === 'paused') return '任务已暂停'
  if (activity.state === 'completed') return '已完成'
  if (activity.state === 'failed') return '生成失败'
  return '已终止'
}

export default function AgentConversationIndex({
  sessions,
  activeSessionId,
  editingSessionId,
  editingTitle,
  isCurrentSessionEmpty = false,
  disabled = false,
  context,
  extraActions,
  sessionActivities = {},
  getActivityLabel = defaultActivityLabel,
  emptyDescription = '暂无对话',
  onActiveSessionChange,
  onEditingSessionIdChange,
  onEditingTitleChange,
  onSaveTitle,
  onNewSession,
  onCloseSession,
  onCollapse,
}: AgentConversationIndexProps) {
  const hasBlankSession = sessions.length > 0 && isCurrentSessionEmpty

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
              disabled={disabled || hasBlankSession}
              onClick={onNewSession}
              aria-label="新建对话"
            />
          </PurrTooltip>
        </div>
      </div>

      <div className="agent-conversation-index__list">
        {sessions.length === 0 ? (
          <PurrEmpty image={false} description={emptyDescription} />
        ) : [...sessions].reverse().map((session) => {
          const active = session.id === activeSessionId
          const activity = sessionActivities[session.id]
          return (
            <div
              key={session.id}
              className={`agent-conversation-index__item${active ? ' is-active' : ''}${activity ? ` is-${activity.state}` : ''}`}
              role="button"
              tabIndex={disabled ? -1 : 0}
              aria-disabled={disabled}
              onClick={() => {
                if (!disabled) onActiveSessionChange(session.id)
              }}
              onKeyDown={(event) => {
                if (disabled || (event.key !== 'Enter' && event.key !== ' ')) return
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
                      if (disabled) return
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
                    {formatSessionTime(session.create_time)}
                  </span>
                  {activity ? (
                    <span
                      className={`agent-conversation-index__status agent-conversation-index__status--${activity.state}`}
                      role="status"
                    >
                      <ActivityIcon activity={activity} />
                      <span>{getActivityLabel(activity)}</span>
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
                  disabled={disabled}
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
