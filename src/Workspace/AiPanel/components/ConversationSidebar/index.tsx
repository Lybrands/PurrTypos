import React from 'react'
import {
  CheckCircleOutlined,
  CloseOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  MessageOutlined,
  PanelToggleIcon,
  PauseCircleOutlined,
  PlusOutlined,
  ReadOutlined,
} from '../../../../ui'
import { Button, Empty, Input, Segmented, Tooltip } from '../../../../ui'
import type { AiSession, EntityId } from '../../../../types'
import {
  getSessionActivityLabel,
  type ChatSessionActivity,
  type ChatSessionScope,
} from '../../hooks'
import SessionHistoryPopover from '../SessionHistoryPopover'
import './index.scss'

interface ConversationSidebarProps {
  bookId: EntityId
  bookTitle: string
  chapterId: EntityId | null
  chapterTitle?: string
  scope: ChatSessionScope
  sessions: AiSession[]
  activeSessionId: number | null
  sessionActivities: Record<number, ChatSessionActivity>
  isCurrentSessionEmpty: boolean
  editingSessionId: number | null
  editingTitle: string
  onScopeChange: (scope: ChatSessionScope) => void
  onActiveSessionChange: (sessionId: number) => void
  onEditingSessionIdChange: (sessionId: number | null) => void
  onEditingTitleChange: (title: string) => void
  onSaveTitle: () => void
  onNewSession: () => void
  onCloseSession: (session: AiSession) => void
  onOpenFromHistory: (session: AiSession) => void
  onDeleteFromHistory: (session: AiSession) => void
  onCollapse: () => void
}

function SessionActivityIcon({ activity }: { activity: ChatSessionActivity }) {
  if (activity.state === 'running') return <LoadingOutlined spin />
  if (activity.state === 'queued') return <ClockCircleOutlined />
  if (activity.state === 'completed') return <CheckCircleOutlined />
  if (activity.state === 'canceled') return <PauseCircleOutlined />
  return <CloseOutlined />
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

export default function ConversationSidebar({
  bookId,
  bookTitle,
  chapterId,
  chapterTitle,
  scope,
  sessions,
  activeSessionId,
  sessionActivities,
  isCurrentSessionEmpty,
  editingSessionId,
  editingTitle,
  onScopeChange,
  onActiveSessionChange,
  onEditingSessionIdChange,
  onEditingTitleChange,
  onSaveTitle,
  onNewSession,
  onCloseSession,
  onOpenFromHistory,
  onDeleteFromHistory,
  onCollapse,
}: ConversationSidebarProps) {
  const hasBlankSession = sessions.length > 0 && isCurrentSessionEmpty

  return (
    <aside className="conversation-sidebar" aria-label="AI 对话记录">
      <div className="conversation-book-card">
        <div className="conversation-book-icon"><ReadOutlined /></div>
        <div className="conversation-book-meta">
          <span className="conversation-book-label">当前书籍</span>
          <strong title={bookTitle || '未命名书籍'}>{bookTitle || '未命名书籍'}</strong>
          <span title={scope === 'chapter' ? chapterTitle : '整本书'}>
            {scope === 'chapter' ? (chapterTitle || '未选择章节') : '整本书 · 全局上下文'}
          </span>
        </div>
      </div>

      <Segmented
        block
        size="small"
        value={scope}
        options={[
          { label: '章节', value: 'chapter' },
          { label: '全局', value: 'setting' },
        ]}
        onChange={(value) => {
          onScopeChange(value as ChatSessionScope)
        }}
        className="conversation-scope-switch"
      />

      <div className="conversation-list-heading">
        <span>对话记录</span>
        <div className="conversation-list-actions">
          <Tooltip title="收起对话列表">
            <Button
              type="text"
              size="small"
              icon={<PanelToggleIcon side="left" action="collapse" />}
              onClick={onCollapse}
              aria-label="收起对话列表"
            />
          </Tooltip>
          <SessionHistoryPopover
            bookId={bookId}
            chapterId={scope === 'setting' ? null : chapterId}
            scope={scope}
            activeSessionId={activeSessionId}
            onOpen={onOpenFromHistory}
            onDelete={onDeleteFromHistory}
          />
          <Tooltip title={hasBlankSession ? '当前对话尚未开始' : '新建对话'}>
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              disabled={hasBlankSession || (scope === 'chapter' && chapterId == null)}
              onClick={onNewSession}
              aria-label="新建对话"
            />
          </Tooltip>
        </div>
      </div>

      <div className="conversation-session-list">
        {sessions.length === 0 ? (
          <Empty image={false} description={scope === 'chapter' && chapterId == null ? '先选择一个章节' : '暂无对话'} />
        ) : [...sessions].reverse().map((session) => {
          const active = session.id === activeSessionId
          const activity = sessionActivities[session.id]
          return (
            <div
              key={session.id}
              className={`conversation-session-item${active ? ' is-active' : ''}${activity ? ` is-${activity.state}` : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                onActiveSessionChange(session.id)
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                event.preventDefault()
                onActiveSessionChange(session.id)
              }}
            >
              <MessageOutlined className="conversation-session-icon" />
              <div className="conversation-session-meta">
                {editingSessionId === session.id ? (
                  <Input
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
                    className="conversation-session-title"
                    title={session.title}
                    onDoubleClick={(event) => {
                      event.stopPropagation()
                      onEditingSessionIdChange(session.id)
                      onEditingTitleChange(session.title)
                    }}
                  >
                    {session.title || '新对话'}
                  </span>
                )}
                <div className="conversation-session-subline">
                  <span className="conversation-session-time">
                    {formatSessionTime(session.create_time)}
                  </span>
                  {activity ? (
                    <span
                      className={`conversation-session-status conversation-session-status--${activity.state}`}
                      role="status"
                    >
                      <SessionActivityIcon activity={activity} />
                      <span>{getSessionActivityLabel(activity)}</span>
                    </span>
                  ) : null}
                </div>
              </div>
              <Tooltip title="关闭对话">
                <Button
                  type="text"
                  size="small"
                  icon={<CloseOutlined />}
                  className="conversation-session-close"
                  onClick={(event) => {
                    event.stopPropagation()
                    onCloseSession(session)
                  }}
                  aria-label={`关闭对话 ${session.title}`}
                />
              </Tooltip>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
