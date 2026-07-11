import React from 'react'
import {
  CloseOutlined,
  MessageOutlined,
  MenuFoldOutlined,
  PlusOutlined,
  ReadOutlined,
} from '@ant-design/icons'
import { Button, Empty, Input, Segmented, Tooltip } from 'antd'
import type { AiSession, EntityId } from '../../../../types'
import type { ChatSessionScope } from '../../hooks'
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
  loading: boolean
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
  onBlockedByLoading: () => void
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

export default function ConversationSidebar({
  bookId,
  bookTitle,
  chapterId,
  chapterTitle,
  scope,
  sessions,
  activeSessionId,
  loading,
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
  onBlockedByLoading,
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
          if (loading) return onBlockedByLoading()
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
              icon={<MenuFoldOutlined />}
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
              disabled={loading || hasBlankSession || (scope === 'chapter' && chapterId == null)}
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
          return (
            <div
              key={session.id}
              className={`conversation-session-item${active ? ' is-active' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                if (loading) return onBlockedByLoading()
                onActiveSessionChange(session.id)
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                event.preventDefault()
                if (loading) return onBlockedByLoading()
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
                <span className="conversation-session-time">{formatSessionTime(session.create_time)}</span>
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
