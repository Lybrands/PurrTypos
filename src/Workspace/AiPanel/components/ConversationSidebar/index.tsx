import React from 'react'
import { services } from '@/services'
import AgentConversationIndex from '@/components/AgentConversation/ConversationIndex'
import type { AgentConversationSession } from '@/components/AgentConversation'
import { toAgentConversationSession } from '@/components/AgentConversation/sessionView'
import { ReadIcon, PurrSegmented } from '@/purr-components'
import type { AiSession, EntityId } from '../../../../types'
import {
  getSessionActivityLabel,
  type ChatSessionActivity,
  type ChatSessionScope,
} from '../../hooks'
import SessionHistory, {
  type AgentConversationHistoryController,
} from '@/components/AgentConversation/ConversationIndex/SessionHistory'
import { createHistoryRequestCoordinator } from './historyRequestCoordinator'
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
  const [historySessions, setHistorySessions] = React.useState<AiSession[]>([])
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [historyError, setHistoryError] = React.useState<string>()
  const [historyRequests] = React.useState(createHistoryRequestCoordinator)
  const visibleSessions = React.useMemo(
    () => sessions.map((session) => (
      toAgentConversationSession(session, session.create_time)
    )),
    [sessions],
  )

  React.useEffect(() => () => historyRequests.unmount(), [historyRequests])

  React.useEffect(() => {
    historyRequests.invalidateLatest()
    setHistorySessions([])
    setHistoryLoading(false)
    setHistoryError(undefined)
  }, [bookId, chapterId, historyRequests, scope])

  const loadSessionHistory = React.useCallback(async () => {
    const request = historyRequests.beginLatest()
    if (bookId == null) {
      if (historyRequests.isCurrent(request)) {
        setHistorySessions([])
        setHistoryError('请先选择书籍')
      }
      return
    }
    if (historyRequests.isCurrent(request)) {
      setHistoryLoading(true)
      setHistoryError(undefined)
    }
    try {
      const result = await services.sessions.getSessions(
        scope === 'setting'
          ? { bookId, includeClosed: true, scope: 'setting' }
          : { bookId, chapterId: chapterId ?? null, includeClosed: true },
      )
      if (!historyRequests.isCurrent(request)) return
      if (!result.success) {
        setHistorySessions([])
        setHistoryError(result.error || '历史对话加载失败')
        return
      }
      setHistorySessions(result.data)
    } catch {
      if (historyRequests.isCurrent(request)) {
        setHistorySessions([])
        setHistoryError('历史对话加载失败，请稍后重试')
      }
    } finally {
      if (historyRequests.isCurrent(request)) setHistoryLoading(false)
    }
  }, [bookId, chapterId, historyRequests, scope])

  const openHistorySession = React.useCallback((id: string | number) => {
    const session = historySessions.find((item) => item.id === id)
    if (session) onOpenFromHistory(session)
  }, [historySessions, onOpenFromHistory])

  const deleteHistorySession = React.useCallback(async (id: string | number) => {
    const session = historySessions.find((item) => item.id === id)
    if (!session) return
    const request = historyRequests.captureLatest()
    await historyRequests.runOnce(`session:${session.id}`, async () => {
      try {
        const result = await services.sessions.deleteSession({ sessionId: session.id })
        if (!historyRequests.isCurrent(request)) return
        if (!result.success) {
          setHistoryError(result.error || '删除历史对话失败')
          return
        }
        setHistorySessions((current) => current.filter((item) => item.id !== session.id))
        onDeleteFromHistory(session)
      } catch {
        if (historyRequests.isCurrent(request)) {
          setHistoryError('删除历史对话失败，请稍后重试')
        }
      }
    })
  }, [historyRequests, historySessions, onDeleteFromHistory])

  const historyController = React.useMemo<AgentConversationHistoryController>(() => ({
    conversation: {
      activeSessionId,
      history: {
        sessions: historySessions.map<AgentConversationSession>((session) => (
          toAgentConversationSession(session, session.create_time)
        )),
        loading: historyLoading,
        error: historyError,
      },
    },
    actions: {
      loadSessionHistory,
      openHistorySession,
      deleteSession: deleteHistorySession,
    },
  }), [
    activeSessionId,
    deleteHistorySession,
    historyError,
    historyLoading,
    historySessions,
    loadSessionHistory,
    openHistorySession,
  ])

  return (
    <AgentConversationIndex
      sessions={visibleSessions}
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
        <SessionHistory controller={historyController} />
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
