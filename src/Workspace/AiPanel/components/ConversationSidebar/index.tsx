import React from 'react'
import AgentConversationIndex from '@/components/AgentConversationIndex'
import { ReadIcon, PurrSegmented } from '@/purr-components'
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
  return (
    <AgentConversationIndex
      sessions={sessions}
      activeSessionId={activeSessionId}
      sessionActivities={sessionActivities}
      isCurrentSessionEmpty={isCurrentSessionEmpty}
      editingSessionId={editingSessionId}
      editingTitle={editingTitle}
      onActiveSessionChange={onActiveSessionChange}
      onEditingSessionIdChange={onEditingSessionIdChange}
      onEditingTitleChange={onEditingTitleChange}
      onSaveTitle={onSaveTitle}
      onNewSession={onNewSession}
      onCloseSession={onCloseSession}
      onCollapse={onCollapse}
      getActivityLabel={getSessionActivityLabel}
      emptyDescription={scope === 'chapter' && chapterId == null ? '先选择一个章节' : '暂无对话'}
      extraActions={(
        <SessionHistoryPopover
          bookId={bookId}
          chapterId={scope === 'setting' ? null : chapterId}
          scope={scope}
          activeSessionId={activeSessionId}
          onOpen={onOpenFromHistory}
          onDelete={onDeleteFromHistory}
        />
      )}
      context={(
        <>
          <div className="conversation-book-card">
            <div className="conversation-book-icon"><ReadIcon /></div>
            <div className="conversation-book-meta">
              <span className="conversation-book-label">当前书籍</span>
              <strong title={bookTitle || '未命名书籍'}>{bookTitle || '未命名书籍'}</strong>
              <span title={scope === 'chapter' ? chapterTitle : '整本书'}>
                {scope === 'chapter' ? (chapterTitle || '未选择章节') : '整本书 · 全局上下文'}
              </span>
            </div>
          </div>
          <PurrSegmented
            block
            size="small"
            value={scope}
            options={[
              { label: '章节', value: 'chapter' },
              { label: '全局', value: 'setting' },
            ]}
            onChange={(value) => onScopeChange(value as ChatSessionScope)}
            className="conversation-scope-switch"
          />
        </>
      )}
    />
  )
}
