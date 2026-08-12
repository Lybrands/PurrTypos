import { services } from '@/services'
/// <reference path="../../vite-env.d.ts" />
import React from "react";
import {
  ArrowUpIcon,
  MessageIcon,
} from '@/purr-components';
import { StopCircleIcon } from '@/purr-components';
import { PurrButton, PurrTooltip, usePurrToast, type PurrDropdownItem } from '@/purr-components';
import AgentComposer from '../../components/AgentComposer'
import AgentTaskProgress from '../../components/AgentTaskProgress'
import { getAgentConversationCapabilities } from '../../agent-runtime/conversationCapabilities'
import type {
  AiModelConfig,
  Conversation,
} from "../../types";
import { useWorkspace } from "../WorkspaceContext";
import {
  INITIAL_FIRST_ITEM_INDEX,
} from "./constants";
import {
  parseConversationsFromApi,
} from "./utils";
import { useAssociatedContext, useAiModelPrefs, useAiSessions, useMemorySelection, useChatScroll, useMessageEditing, usePromptTemplateContext, useChatSubmit, type ChatMessage, type ChatSessionScope } from "./hooks";
import FavoritesModal from "./components/FavoritesModal";
import MemoryModal from "./components/MemoryModal";
import AiPanelHeader from "./components/AiPanelHeader";
import ChatEmptyState from "./components/ChatEmptyState";
import ChatMessageList from "./components/ChatMessageList";
import ConversationSidebar from "./components/ConversationSidebar";
import AiContextBar, { type AiContextBarBindings } from "./components/AiContextBar";
import AiComposeBottom, {
  type ModelSelectionBindings,
} from "./components/AiComposeBottom";
import ContextUsageIndicator from "./components/ContextUsageIndicator";
import { getActiveTaskPlan } from "../../agent-runtime/taskPlan";
import { getChatSessionRuntime } from "./hooks/chatRuntimeStore";
import "./index.scss";

interface AiPanelProps {
  modelConfigs: AiModelConfig[];
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void;
  conversationSidebarOpen?: boolean;
  onConversationSidebarOpenChange?: (open: boolean) => void;
  onReady?: () => void;
}

export default function AiPanel({
  modelConfigs = [],
  onUpdateModelConfig,
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

  const appMessage = usePurrToast();
  const {
    activeChapterId: chapterId,
    activeChapterTitle,
    bookId,
    bookTitle,
    writingChapters,
  } = useWorkspace();
  const [prompt, setPrompt] = React.useState("");
  const [conversations, setConversations] = React.useState<ChatMessage[]>([]);
  const [loading, setLoading] = React.useState(false);
  const conversationCapabilities = getAgentConversationCapabilities({
    running: loading,
    readOnly: false,
    sessionLoading: false,
  });
  /** 会话作用域：chapter = 章节对话（默认）；setting = 全局对话（不绑章节，整本书共享；存储值仍为 setting 以兼容历史） */
  const [chatScope, setChatScope] = React.useState<ChatSessionScope>("chapter");
  /** 人物卡/背景「与 AI 讨论」入口触发后，待会话列表就绪时自动建会话 */
  const pendingSettingSessionRef = React.useRef(false);
  const effectiveChapterId = chatScope === "setting" ? null : chapterId;

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<import("../../types").SettingDiffCardState>).detail;
      if (!detail?.sessionKey) return;
      setConversations((prev) =>
        prev.map((msg) => {
          if (!msg.settingDiffCards?.length) return msg;
          const cards = msg.settingDiffCards.map((c) =>
            c.sessionKey === detail.sessionKey ? { ...c, ...detail } : c,
          );
          return { ...msg, settingDiffCards: cards };
        }),
      );
    };
    window.addEventListener("setting-diff-resolved", handler as EventListener);
    return () => window.removeEventListener("setting-diff-resolved", handler as EventListener);
  }, []);
  const {
    selectedModel,
    setSelectedModel,
    chatAgentMode,
    setChatAgentMode,
    selectedModelConfig,
  } = useAiModelPrefs(bookId, modelConfigs);
  const [favoritesModalOpen, setFavoritesModalOpen] = React.useState(false);
  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false);
  const {
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId);
  const [contextPopoverOpen, setContextPopoverOpen] = React.useState(false);

  const {
    sessions,
    setSessions,
    sessionsLoaded,
    activeSessionId,
    setActiveSessionId,
    prependedHistory,
    editingTabId,
    setEditingTabId,
    editingTitle,
    setEditingTitle,
    handleNewSession,
    handleCloseTab,
    handleOpenFromHistory,
    handleDeleteFromHistory,
    currentSessionTitle,
    handleSaveTabTitle,
  } = useAiSessions({
    bookId,
    chapterId: effectiveChapterId,
    scope: chatScope,
    conversations,
    setConversations,
    setLoading,
  });

  // 「与 AI 讨论」入口事件：切到全局作用域 + 预填上下文；会话列表就绪后若无会话自动新建
  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ prefill?: string }>).detail;
      setChatScope("setting");
      pendingSettingSessionRef.current = true;
      if (detail?.prefill) setPrompt(detail.prefill);
    };
    window.addEventListener("open-setting-chat", handler as EventListener);
    return () => window.removeEventListener("open-setting-chat", handler as EventListener);
  }, []);

  React.useEffect(() => {
    if (!pendingSettingSessionRef.current) return;
    if (chatScope !== "setting" || !sessionsLoaded) return;
    pendingSettingSessionRef.current = false;
    if (sessions.length === 0) void handleNewSession();
  }, [chatScope, sessionsLoaded, sessions.length, handleNewSession]);

  const handleInsertPrompt = React.useCallback(
    (text: string) => setPrompt(text),
    [],
  );

  const combinedData = React.useMemo<ChatMessage[]>(
    () => [...prependedHistory, ...conversations],
    [prependedHistory, conversations],
  );
  const activeTaskPlan = React.useMemo(
    () => getActiveTaskPlan(conversations, loading),
    [conversations, loading],
  );
  const firstItemIndex = INITIAL_FIRST_ITEM_INDEX - prependedHistory.length;

  const {
    virtuosoRef,
    userHasScrolledUp,
    setScrolledUpByReason,
    isAtBottom,
    handleAtBottomStateChange,
    handleScrollToBottom,
    pinNewTurnToTop,
  } = useChatScroll({ loading, combinedData });

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
  } = useAssociatedContext({ bookId, chapterId, writingChapters });

  /** 关联上下文栏的整组绑定：打成一个对象，沿途只透传这一个 prop（见 AiContextBarBindings）。 */
  const contextBar = React.useMemo<AiContextBarBindings>(
    () => ({
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
    }),
    [
      associatedChapterIds,
      setAssociatedChapterIds,
      associatedOutlineIds,
      setAssociatedOutlineIds,
      chapterSelectOptions,
      outlineSelectOptions,
      handleQuickAssociateChapter,
      handleQuickAssociateOutline,
      selectedMemoryIds,
      selectedForeshadowingIds,
      contextPopoverOpen,
    ],
  );

  /** 模型选择的整组绑定：对话模式、所选模型（见 ModelSelectionBindings）。 */
  const modelSelection = React.useMemo<ModelSelectionBindings>(
    () => ({
      chatAgentMode,
      setChatAgentMode,
      selectedModel,
      setSelectedModel,
      updateModelConfig: onUpdateModelConfig,
    }),
    [
      chatAgentMode,
      setChatAgentMode,
      selectedModel,
      setSelectedModel,
      onUpdateModelConfig,
    ],
  );

  const promptTemplateContext = usePromptTemplateContext({
    chapterId,
    writingChapters,
    bookTitle,
    activeChapterTitle,
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
  });

  const {
    handleSubmit: doSubmit,
    handleAbort,
    queuedCount,
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
    currentChapterTitle:
      chatScope === "setting" ? undefined : activeChapterTitle || undefined,
    selectedModel,
    agentEnabled: chatAgentMode !== "ask",
    selectedMemoryIds,
    selectedForeshadowingIds,
    sessionScope: chatScope,
  });
  const {
    editingMessageIndex,
    setEditingMessageIndex,
    editTextareaRef,
    editingMessageDraftRef,
    getEditTextareaValue,
  } = useMessageEditing();

  const handleSubmit = React.useCallback(() => {
    pinNewTurnToTop();
    doSubmit();
    setSelectedMemoryIds([]);
    setSelectedForeshadowingIds([]);
  }, [doSubmit, pinNewTurnToTop]);

  const handleStructuredAnswer = React.useCallback(
    (answer: string) => {
      const trimmed = answer.trim();
      if (!trimmed) return;
      pinNewTurnToTop();
      doSubmit({ content: trimmed });
      setSelectedMemoryIds([]);
      setSelectedForeshadowingIds([]);
    },
    [doSubmit, pinNewTurnToTop],
  );

  const handleEditSend = React.useCallback(
    (editIndex: number, content?: string) => {
      const raw = content ?? getEditTextareaValue() ?? editingMessageDraftRef.current;
      const trimmed = raw.trim();
      if (!trimmed) return;
      pinNewTurnToTop();
      doSubmit({ editIndex, content: trimmed });
      setEditingMessageIndex(null);
      editingMessageDraftRef.current = "";
      setSelectedMemoryIds([]);
      setSelectedForeshadowingIds([]);
    },
    [doSubmit, getEditTextareaValue, pinNewTurnToTop],
  );

  // activeSessionId 变化时加载对话记录
  React.useEffect(() => {
    if (!activeSessionId) {
      setConversations([]);
      return;
    }
    const currentRuntime = getChatSessionRuntime(activeSessionId);
    setConversations(currentRuntime?.messages ?? []);
    setLoading(currentRuntime?.loading ?? false);
    services.conversations
      .getConversations({ sessionId: activeSessionId })
      .then((res) => {
        if (!res.success) return;
        const loaded = parseConversationsFromApi(res.data as Conversation[]);
        const runtime = getChatSessionRuntime(activeSessionId);
        setConversations(runtime?.messages ?? loaded);
        setLoading(runtime?.loading ?? false);
      });
  }, [activeSessionId]);

  const handleAddFavorite = React.useCallback(
    async (prompt: string, content: string) => {
      if (activeSessionId == null) return;
      const res = await services.favorites.saveAiFavorite({
        sessionId: activeSessionId,
        sessionTitle: currentSessionTitle,
        prompt,
        content,
      });
      if (res.success) appMessage.success("已收藏");
      else appMessage.error(res.error ?? "收藏失败");
    },
    [activeSessionId, currentSessionTitle, appMessage],
  );

  /*
   * v3.2 起，AiPanel 不再提供任何“把 AI 回复直接落到正文/草稿”的快捷入口：
   * - 改正文：必须通过 AI 工具调用 editChapterContent → 后端推 chunk.proposedChapterDiff
   *           → DiffProvider 自动 startDiff → 用户在 DiffOverlay 接受/拒绝
   * - 草稿区（Canvas）整体下线
   * - 光标插入也下线（避免误覆盖 / 跳过 diff 审阅）
   * 因此 handleWriteToCanvas / handleInsertAtCursor / handleApplyAsDiff 全部移除。
   */

  const ellipsisMenuItems: PurrDropdownItem[] = [
    {
      key: "favorites",
      label: "查看收藏列表",
      onClick: () => setFavoritesModalOpen(true),
    },
  ];

  return (
    <div className="ai-panel panel-main">
      <AiPanelHeader
        menuItems={ellipsisMenuItems}
      />

      <div className="ai-panel-body">
        {bookId != null && conversationSidebarOpen && (
          <ConversationSidebar
          bookId={bookId}
          chapterId={effectiveChapterId}
          bookTitle={bookTitle}
          scope={chatScope}
          chapterTitle={chatScope === "chapter" ? activeChapterTitle || undefined : undefined}
          sessions={sessions}
          activeSessionId={activeSessionId}
          sessionActivities={sessionActivities}
          isCurrentSessionEmpty={conversations.length === 0}
          editingSessionId={editingTabId}
          editingTitle={editingTitle}
          onScopeChange={setChatScope}
          onActiveSessionChange={setActiveSessionId}
          onEditingSessionIdChange={setEditingTabId}
          onEditingTitleChange={setEditingTitle}
          onSaveTitle={handleSaveTabTitle}
          onNewSession={handleNewSession}
          onCloseSession={handleCloseTab}
          onOpenFromHistory={handleOpenFromHistory}
          onDeleteFromHistory={handleDeleteFromHistory}
          onCollapse={() => onConversationSidebarOpenChange?.(false)}
        />
        )}

        {bookId != null && !conversationSidebarOpen && (
          <PurrTooltip title="展开对话列表" placement="right">
            <PurrButton
              type="text"
              className="conversation-sidebar-reopen"
              icon={<MessageIcon />}
              onClick={() => onConversationSidebarOpenChange?.(true)}
              aria-label="展开对话列表"
            />
          </PurrTooltip>
        )}

        <div className="ai-conversation-main">
          <div className="chat-history-wrap">
            <div className="chat-history">
          {combinedData.length === 0 && !loading && (
            <ChatEmptyState
              hasBook={bookId != null}
              hasSessions={sessions.length > 0}
            />
          )}

          <ChatMessageList
            virtuosoRef={virtuosoRef}
            combinedData={combinedData}
            firstItemIndex={firstItemIndex}
            prependedHistoryLength={prependedHistory.length}
            loading={loading}
            userHasScrolledUp={userHasScrolledUp}
            isAtBottom={isAtBottom}
            onAtBottomStateChange={handleAtBottomStateChange}
            setScrolledUpByReason={setScrolledUpByReason}
            onScrollToBottom={handleScrollToBottom}
            bookId={bookId}
            chapterId={effectiveChapterId}
            contextBar={contextBar}
            editingMessageIndex={editingMessageIndex}
            setEditingMessageIndex={setEditingMessageIndex}
            editingMessageDraftRef={editingMessageDraftRef}
            editTextareaRef={editTextareaRef}
            onEditSend={handleEditSend}
            modelConfigs={modelConfigs}
            modelSelection={modelSelection}
            onAbort={handleAbort}
            onAddFavorite={handleAddFavorite}
            onStructuredAnswer={handleStructuredAnswer}
          />
            </div>
          </div>
          <AgentComposer
            className="ai-conversation-composer"
            value={prompt}
            onChange={setPrompt}
            onSubmit={handleSubmit}
            placeholder="想写点什么"
            disabled={conversationCapabilities.inputDisabled}
            autoSize={{ minRows: 1, maxRows: 5 }}
            submitDisabled={
              !prompt.trim()
              || bookId == null
              || (chatScope === "chapter" && chapterId == null)
              || activeSessionId == null
            }
            floatingContent={activeTaskPlan ? (
              <AgentTaskProgress
                plan={activeTaskPlan}
                placement="topLeft"
              />
            ) : null}
            supplementaryContent={queuedMessages.length > 0 ? (
              <div className="chat-queued-messages" aria-label="待发送消息">
                {queuedMessages.slice(0, 3).map((message, index) => (
                  <div className="chat-queued-message" key={`${index}-${message}`}>
                    <span>待发送 {index + 1}</span>
                    <span title={message}>{message}</span>
                  </div>
                ))}
                {queuedMessages.length > 3 ? (
                  <div className="chat-queued-more">
                    另有 {queuedMessages.length - 3} 条消息排队
                  </div>
                ) : null}
              </div>
            ) : null}
            footer={(
              <AiComposeBottom
                modelConfigs={modelConfigs}
                {...modelSelection}
                loading={loading}
                onAbort={handleAbort}
                leftContent={bookId != null ? (
                  <AiContextBar
                    bookId={bookId}
                    chapterId={effectiveChapterId ?? null}
                    {...contextBar}
                    currentPrompt={prompt}
                    onInsertPrompt={handleInsertPrompt}
                    promptTemplateContext={promptTemplateContext}
                    promptTemplateDisabled={false}
                  />
                ) : null}
                rightContent={(
                  <div className="chat-compose-right">
                    {queuedCount > 0 ? (
                      <span className="chat-queue-count" role="status">
                        排队 {queuedCount}
                      </span>
                    ) : null}
                    <ContextUsageIndicator
                      conversations={conversations}
                      selectedModelConfig={selectedModelConfig}
                    />
                    {loading ? (
                      <PurrButton
                        className="agent-composer__stop"
                        icon={<StopCircleIcon size={18} />}
                        type="text"
                        onClick={handleAbort}
                        aria-label="停止生成"
                      />
                    ) : null}
                    <PurrTooltip title={conversationCapabilities.submitMode === 'queue'
                      ? "加入发送队列 (Enter)"
                      : "发送 (Enter)"}>
                      <PurrButton
                        type="primary"
                        shape="circle"
                        className="agent-composer__send"
                        icon={<ArrowUpIcon style={{ fontSize: 16 }} />}
                        onClick={handleSubmit}
                        disabled={
                          !prompt.trim()
                          || bookId == null
                          || (chatScope === "chapter" && chapterId == null)
                          || activeSessionId == null
                        }
                        aria-label={conversationCapabilities.submitMode === 'queue'
                          ? '加入发送队列'
                          : '发送'}
                      />
                    </PurrTooltip>
                  </div>
                )}
              />
            )}
          />
        </div>
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
          setSelectedMemoryIds(memoryIds);
          setSelectedForeshadowingIds(foreshadowingIds);
        }}
      />
    </div>
  );
}
