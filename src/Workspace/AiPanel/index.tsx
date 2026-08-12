import { services } from '@/services'
/// <reference path="../../vite-env.d.ts" />
import React from 'react'
import { usePurrToast, type PurrDropdownItem } from '@/purr-components'
import type { AiModelConfig, Conversation, SettingDiffCardState } from '../../types'
import { getActiveTaskPlan } from '../../agent-runtime/taskPlan'
import { AgentConversationPanel } from '../../components/AgentConversation'
import { useWorkspace } from '../WorkspaceContext'
import {
  useAssociatedContext,
  useAiModelPrefs,
  useAiSessions,
  useMemorySelection,
  usePromptTemplateContext,
  useChatSubmit,
  type ChatMessage,
  type ChatSessionScope,
} from './hooks'
import { getChatSessionRuntime } from './hooks/chatRuntimeStore'
import { parseConversationsFromApi } from './utils'
import FavoritesModal from './components/FavoritesModal'
import MemoryModal from './components/MemoryModal'
import AiPanelHeader from './components/AiPanelHeader'
import type { AiContextBarBindings } from './components/AiContextBar'
import {
  addBookAssistantAttachment,
  getBookAssistantAttachments,
  getBookAssistantAttachmentsVersion,
  resolveStoredBookAssistantAttachments,
  subscribeBookAssistantAttachments,
} from './bookAssistantAttachments'
import { useBookConversationController } from './useBookConversationController'
import { useBookConversationExtensions } from './BookConversationExtensions'
import './index.scss'

interface AiPanelProps {
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
  ) => void
  onOpenModelSettings: () => void
  conversationSidebarOpen?: boolean
  onConversationSidebarOpenChange?: (open: boolean) => void
  onReady?: () => void
}

export default function AiPanel({
  modelConfigs = [],
  onUpdateModelConfig,
  onOpenModelSettings,
  conversationSidebarOpen = true,
  onConversationSidebarOpenChange,
  onReady,
}: AiPanelProps) {
  React.useEffect(() => {
    if (!onReady) return
    let secondFrame = 0
    let readyTimer = 0
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        readyTimer = window.setTimeout(onReady, 200)
      })
    })
    return () => {
      window.cancelAnimationFrame(firstFrame)
      if (secondFrame) window.cancelAnimationFrame(secondFrame)
      if (readyTimer) window.clearTimeout(readyTimer)
    }
  }, [onReady])

  const appMessage = usePurrToast()
  const {
    activeChapterId: chapterId,
    activeChapterTitle,
    bookId,
    bookTitle,
    writingChapters,
  } = useWorkspace()
  const [prompt, setPrompt] = React.useState('')
  const [conversations, setConversations] = React.useState<ChatMessage[]>([])
  const [loading, setLoading] = React.useState(false)
  const [conversationInitializing, setConversationInitializing] = React.useState(false)
  const [chatScope, setChatScope] = React.useState<ChatSessionScope>('chapter')
  const [favoritesModalOpen, setFavoritesModalOpen] = React.useState(false)
  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false)
  const [contextPopoverOpen, setContextPopoverOpen] = React.useState(false)
  const attachmentsVersion = React.useSyncExternalStore(
    subscribeBookAssistantAttachments,
    getBookAssistantAttachmentsVersion,
    getBookAssistantAttachmentsVersion,
  )
  const attachments = getBookAssistantAttachments()
  const pendingSettingSessionRef = React.useRef(false)
  const effectiveChapterId = chatScope === 'setting' ? null : chapterId
  const scopeAvailable = bookId != null
    && (chatScope === 'setting' || chapterId != null)

  const addAssistantAttachment = React.useCallback((
    message: ChatMessage,
    card: SettingDiffCardState,
  ) => {
    addBookAssistantAttachment(message, card)
  }, [])

  React.useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<SettingDiffCardState>).detail
      if (!detail?.sessionKey) return
      resolveStoredBookAssistantAttachments(detail)
    }
    window.addEventListener('setting-diff-resolved', handler as EventListener)
    return () => window.removeEventListener('setting-diff-resolved', handler as EventListener)
  }, [])

  const {
    selectedModel,
    setSelectedModel,
    chatAgentMode,
    setChatAgentMode,
    selectedModelConfig,
  } = useAiModelPrefs(bookId, modelConfigs)
  const {
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId)
  const {
    sessions,
    setSessions,
    sessionsLoaded,
    activeSessionId,
    setActiveSessionId,
    prependedHistory,
    handleNewSession,
    handleCloseTab,
    handleOpenFromHistory,
    handleDeleteFromHistory,
    currentSessionTitle,
    handleRenameSession,
  } = useAiSessions({
    bookId,
    chapterId: effectiveChapterId,
    scope: chatScope,
    conversations,
    setConversations,
    setLoading,
  })

  React.useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ prefill?: string }>).detail
      setChatScope('setting')
      pendingSettingSessionRef.current = true
      if (detail?.prefill) setPrompt(detail.prefill)
    }
    window.addEventListener('open-setting-chat', handler as EventListener)
    return () => window.removeEventListener('open-setting-chat', handler as EventListener)
  }, [])

  React.useEffect(() => {
    if (!pendingSettingSessionRef.current) return
    if (chatScope !== 'setting' || !sessionsLoaded) return
    pendingSettingSessionRef.current = false
    if (sessions.length === 0) void handleNewSession()
  }, [chatScope, handleNewSession, sessions.length, sessionsLoaded])

  const {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    outlineSelectOptions,
    chapterSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  } = useAssociatedContext({ bookId, chapterId, writingChapters })

  const contextBar = React.useMemo<AiContextBarBindings>(() => ({
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
    onQuickAssociateChapter: handleQuickAssociateChapter,
    onQuickAssociateOutline: handleQuickAssociateOutline,
    selectedMemoryIds,
    selectedForeshadowingIds,
    onOpenMemoryModal: () => setMemoryModalOpen(true),
    contextPopoverOpen,
    onContextPopoverOpenChange: setContextPopoverOpen,
  }), [
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    contextPopoverOpen,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
    outlineSelectOptions,
    selectedForeshadowingIds,
    selectedMemoryIds,
    setAssociatedChapterIds,
    setAssociatedOutlineIds,
  ])

  const promptTemplateContext = usePromptTemplateContext({
    chapterId,
    writingChapters,
    bookTitle,
    activeChapterTitle,
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
  })
  const activeTaskPlan = React.useMemo(
    () => getActiveTaskPlan(conversations, loading),
    [conversations, loading],
  )

  const {
    handleSubmit: doSubmit,
    handleAbort,
    queuedMessages,
    sessionActivities,
  } = useChatSubmit({
    selectedModelConfig,
    prompt,
    setPrompt,
    loading,
    setLoading,
    conversations,
    setConversations,
    bookId: bookId ?? undefined,
    chapterId: effectiveChapterId,
    activeSessionId,
    setActiveSessionId,
    sessions,
    setSessions,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    currentChapterTitle: chatScope === 'setting'
      ? undefined
      : activeChapterTitle || undefined,
    selectedModel,
    agentEnabled: chatAgentMode !== 'ask',
    selectedMemoryIds,
    selectedForeshadowingIds,
    sessionScope: chatScope,
    onAssistantAttachment: addAssistantAttachment,
  })

  const clearSelectedContext = React.useCallback(() => {
    setSelectedMemoryIds([])
    setSelectedForeshadowingIds([])
  }, [setSelectedForeshadowingIds, setSelectedMemoryIds])

  const handleSubmit = React.useCallback((content?: string) => {
    if (content !== undefined) {
      const trimmed = content.trim()
      if (!trimmed) return
      doSubmit({ content: trimmed })
    } else {
      doSubmit()
    }
    clearSelectedContext()
  }, [clearSelectedContext, doSubmit])

  const handleEditMessage = React.useCallback((index: number, content: string) => {
    const editIndex = index - prependedHistory.length
    const trimmed = content.trim()
    if (editIndex < 0 || !trimmed) return
    doSubmit({ editIndex, content: trimmed })
    clearSelectedContext()
  }, [clearSelectedContext, doSubmit, prependedHistory.length])

  const handleResolveToolApproval = React.useCallback((
    approvalId: string,
    approved: boolean,
  ) => services.ai.resolveAiToolApproval({ approvalId, approved }), [])
  const handleSubmitErrorReport = React.useCallback(
    (reportId: string) => services.ai.submitAiErrorReport({ reportId }),
    [],
  )

  React.useEffect(() => {
    let active = true
    if (activeSessionId == null) {
      setConversationInitializing(false)
      setConversations([])
      return () => { active = false }
    }
    const sessionId = activeSessionId
    const currentRuntime = getChatSessionRuntime(sessionId)
    setConversations(currentRuntime?.messages ?? [])
    setLoading(currentRuntime?.loading ?? false)
    setConversationInitializing(true)
    void services.conversations
      .getConversations({ sessionId })
      .then((result) => {
        if (!active || !result.success) return
        const loaded = parseConversationsFromApi(result.data as Conversation[])
        const runtime = getChatSessionRuntime(sessionId)
        setConversations(runtime?.messages ?? loaded)
        setLoading(runtime?.loading ?? false)
      })
      .finally(() => {
        if (active) setConversationInitializing(false)
      })
    return () => { active = false }
  }, [activeSessionId, setConversations, setLoading])

  const handleAddFavorite = React.useCallback(async (
    sourcePrompt: string,
    content: string,
  ) => {
    if (activeSessionId == null) return
    const result = await services.favorites.saveAiFavorite({
      sessionId: activeSessionId,
      sessionTitle: currentSessionTitle,
      prompt: sourcePrompt,
      content,
    })
    if (result.success) appMessage.success('已收藏')
    else appMessage.error(result.error ?? '收藏失败')
  }, [activeSessionId, appMessage, currentSessionTitle])

  const closeSession = React.useCallback((id: string | number) => {
    const session = sessions.find((item) => item.id === id)
    if (session) handleCloseTab(session)
  }, [handleCloseTab, sessions])

  const bookConversationController = useBookConversationController({
    bookId,
    chapterId: effectiveChapterId,
    scope: chatScope,
    historyService: services.sessions,
    sessions,
    activeSessionId,
    messages: conversations,
    prependedHistory,
    activities: sessionActivities,
    queuedMessages,
    prompt,
    setPrompt,
    initializing: Boolean(scopeAvailable && (!sessionsLoaded || conversationInitializing)),
    running: loading,
    attachmentsVersion,
    scopeAvailable,
    modelConfigs,
    selectedModelId: selectedModel,
    setSelectedModelId: setSelectedModel,
    updateModel: onUpdateModelConfig,
    openModelSettings: onOpenModelSettings,
    taskPlan: activeTaskPlan,
    actions: {
      selectSession: (id) => setActiveSessionId(Number(id)),
      createSession: handleNewSession,
      closeSession,
      renameSession: (id, title) => handleRenameSession(Number(id), title),
      send: handleSubmit,
      abort: handleAbort,
      editMessage: handleEditMessage,
      resolveToolApproval: handleResolveToolApproval,
      onSubmitErrorReport: handleSubmitErrorReport,
    },
    onOpenFromHistory: handleOpenFromHistory,
    onDeleteFromHistory: handleDeleteFromHistory,
  })
  const combinedMessages = bookConversationController.conversation.messages
  const bookConversationExtensions = useBookConversationExtensions({
    bookId,
    bookTitle,
    chapterId: effectiveChapterId,
    chapterTitle: chatScope === 'chapter'
      ? activeChapterTitle || undefined
      : undefined,
    scope: chatScope,
    setScope: setChatScope,
    chatAgentMode,
    setChatAgentMode,
    contextBar,
    prompt,
    onInsertPrompt: setPrompt,
    promptTemplateContext,
    messages: combinedMessages,
    attachments,
    running: loading,
    onAddFavorite: handleAddFavorite,
  })

  const ellipsisMenuItems: PurrDropdownItem[] = [{
    key: 'favorites',
    label: '查看收藏列表',
    onClick: () => setFavoritesModalOpen(true),
  }]

  return (
    <div className="ai-panel panel-main">
      <AiPanelHeader menuItems={ellipsisMenuItems} />
      <div className="ai-panel-body">
        <AgentConversationPanel
          controller={bookConversationController}
          extensions={bookConversationExtensions}
          indexOpen={conversationSidebarOpen}
          onIndexOpenChange={onConversationSidebarOpenChange}
          className="book-agent-conversation-panel"
        />
      </div>
      <FavoritesModal
        open={favoritesModalOpen}
        onCancel={() => setFavoritesModalOpen(false)}
      />
      <MemoryModal
        open={memoryModalOpen}
        onCancel={() => setMemoryModalOpen(false)}
        bookId={bookId ?? null}
        writingChapters={writingChapters}
        selectedIds={selectedMemoryIds}
        selectedForeshadowingIds={selectedForeshadowingIds}
        onSelectConfirm={(memoryIds, foreshadowingIds) => {
          setSelectedMemoryIds(memoryIds)
          setSelectedForeshadowingIds(foreshadowingIds)
        }}
      />
    </div>
  )
}
