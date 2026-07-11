/// <reference path="../../vite-env.d.ts" />
import React from "react";
import {
  ArrowUpOutlined,
  MessageOutlined,
} from "@ant-design/icons";
import StopCircleIcon from "../../icons/StopCircleIcon";
import {
  App as AntdApp,
  Button,
  Input,
  Tooltip,
} from "antd";
import type { MenuProps } from "antd";
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
import "./index.scss";

interface AiPanelProps {
  modelConfigs: AiModelConfig[];
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void;
  conversationSidebarOpen?: boolean;
  onConversationSidebarOpenChange?: (open: boolean) => void;
}

export default function AiPanel({
  modelConfigs = [],
  onUpdateModelConfig,
  conversationSidebarOpen = true,
  onConversationSidebarOpenChange,
}: AiPanelProps) {
  const { message: appMessage } = AntdApp.useApp();
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
    modelConfigsRecord,
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
    loading,
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
  const firstItemIndex = INITIAL_FIRST_ITEM_INDEX - prependedHistory.length;

  const {
    virtuosoRef,
    userHasScrolledUp,
    setScrolledUpByReason,
    isAtBottom,
    setIsAtBottom,
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

  const { handleSubmit: doSubmit, handleAbort, runningSessionIdRef, runningAccRef } = useChatSubmit({
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
    modelConfigs: modelConfigsRecord,
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
    window.electronAPI
      .getConversations({ sessionId: activeSessionId })
      .then((res) => {
        if (!res.success) return;
        const loaded = parseConversationsFromApi(res.data as Conversation[]);
        const acc = runningAccRef.current;
        if (
          runningSessionIdRef.current === activeSessionId &&
          acc != null
        ) {
          setConversations([
            ...loaded,
            { role: "user" as const, content: acc.userText },
            {
              role: "assistant" as const,
              content: acc.response || "",
              thinking: acc.thinking || undefined,
              thinkingStartedAt: acc.thinkingBlockStartedAt,
              turnStartedAt: acc.turnStartedAt,
              toolCallSegments: acc.toolCallSegments,
              contentAfterToolCalls: acc.toolCallSegments?.length
                ? (acc.contentAfterToolCalls ?? "")
                : undefined,
              thinkingBlocks: acc.thinkingBlocks?.length
                ? acc.thinkingBlocks
                : undefined,
            },
          ]);
          setLoading(true);
        } else {
          setConversations(loaded);
        }
      });
  }, [activeSessionId]);

  const handleAddFavorite = React.useCallback(
    async (prompt: string, content: string) => {
      if (activeSessionId == null) return;
      const res = await window.electronAPI.saveAiFavorite({
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

  const ellipsisMenuItems: MenuProps["items"] = [
    {
      key: "favorites",
      label: "查看收藏列表",
      onClick: () => setFavoritesModalOpen(true),
    },
  ];

  return (
    <div className="ai-panel panel-main">
      <AiPanelHeader menuItems={ellipsisMenuItems} />

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
          loading={loading}
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
          onBlockedByLoading={() => {
            appMessage.warning("当前对话进行中，请先等待完成或停止");
          }}
          onCollapse={() => onConversationSidebarOpenChange?.(false)}
        />
        )}

        {bookId != null && !conversationSidebarOpen && (
          <Tooltip title="展开对话列表" placement="right">
            <Button
              type="text"
              className="conversation-sidebar-reopen"
              icon={<MessageOutlined />}
              onClick={() => onConversationSidebarOpenChange?.(true)}
              aria-label="展开对话列表"
            />
          </Tooltip>
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
            setIsAtBottom={setIsAtBottom}
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
          />
            </div>
          </div>
          <div className="chat-input-area">
        <div className="chat-input-inner">
          <Input.TextArea
            className="chat-input"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="告诉我你要创作的内容，或者和我一起讨论你的想法吧"
            disabled={loading}
            autoSize={{ minRows: 1, maxRows: 5 }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                if (
                  !prompt.trim() ||
                  bookId == null ||
                  (chatScope === "chapter" && chapterId == null) ||
                  loading ||
                  activeSessionId == null
                ) {
                  return;
                }
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
        </div>
            <AiComposeBottom
          modelConfigs={modelConfigs}
          {...modelSelection}
          loading={loading}
          onAbort={handleAbort}
          leftContent={
            bookId != null ? (
              <AiContextBar
                bookId={bookId}
                chapterId={effectiveChapterId ?? null}
                {...contextBar}
                currentPrompt={prompt}
                onInsertPrompt={handleInsertPrompt}
                promptTemplateContext={promptTemplateContext}
                promptTemplateDisabled={loading}
              />
            ) : null
          }
          rightContent={
            <div className="chat-compose-right">
              {loading ? (
                <Button
                  className="btn-submit btn-stop btn-submit--icon"
                  icon={<StopCircleIcon size={18} />}
                  type="text"
                  onClick={handleAbort}
                />
              ) : (
                <Tooltip title="发送 (Enter)">
                  <Button
                    type="primary"
                    shape="circle"
                    className="btn-submit btn-submit--icon"
                    icon={<ArrowUpOutlined style={{ fontSize: 16 }} />}
                    onClick={handleSubmit}
                    disabled={
                      !prompt.trim() ||
                      bookId == null ||
                      (chatScope === "chapter" && chapterId == null) ||
                      loading ||
                      activeSessionId == null
                    }
                  />
                </Tooltip>
              )}
            </div>
          }
            />
          </div>
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
