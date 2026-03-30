/// <reference path="../../vite-env.d.ts" />
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ExpandOutlined,
  CompressOutlined,
  PlusOutlined,
  StarOutlined,
  EllipsisOutlined,
  EditOutlined,
  LoadingOutlined,
  VerticalAlignBottomOutlined,
} from "@ant-design/icons";
import type { TextAreaRef } from "antd/es/input/TextArea";
import StopCircleIcon from "../../icons/StopCircleIcon";
import {
  App as AntdApp,
  Button,
  Input,
  Empty,
  Tooltip,
  Tabs,
  Dropdown,
} from "antd";
import type { MenuProps } from "antd";
import type { AiModelConfig, AiSession, Conversation } from "../../types";
import { useWorkspace } from "../WorkspaceContext";
import {
  INPUT_AREA_DEFAULT,
  INPUT_AREA_MAX,
  INPUT_AREA_MIN,
  INITIAL_FIRST_ITEM_INDEX,
} from "./constants";
import {
  formatModelName,
  loadModelPrefs,
  parseConversationsFromApi,
  saveModelPrefs,
} from "./utils";
import { Virtuoso, type VirtuosoHandle, type ListProps } from "react-virtuoso";
import { useAssociatedContext, useChatSubmit, type ChatMessage } from "./hooks";
import SessionHistoryPopover from "./components/SessionHistoryPopover";
import FavoritesModal from "./components/FavoritesModal";
import MemoryModal from "./components/MemoryModal";
import AiContextBar from "./components/AiContextBar";
import AiComposeBottom from "./components/AiComposeBottom";
import ToolCallStatus from "./components/ToolCallStatus";
import ThinkingRegion from "./components/ThinkingRegion";
import SubagentStageStrip from "./components/SubagentStageStrip";
import type { PipelineStageId } from "./pipelineStages";
import "./index.scss";

interface AiPanelProps {
  modelConfigs: AiModelConfig[];
  aiAgentMode?: "legacy" | "subagent";
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
}

const validModelIds = (configs: AiModelConfig[]) => configs.map((c) => c.id);
const thinkingOnlyModelIds = (configs: AiModelConfig[]) =>
  configs.filter((c) => c.thinkingOnly).map((c) => c.id);

export default function AiPanel({
  modelConfigs = [],
  aiAgentMode = "legacy",
  isFullscreen,
  onToggleFullscreen,
}: AiPanelProps) {
  const { message: appMessage } = AntdApp.useApp();
  const {
    activeChapterId: chapterId,
    activeChapterTitle,
    bookId,
    writingChapters,
  } = useWorkspace();
  const [prompt, setPrompt] = React.useState("");
  const [conversations, setConversations] = React.useState<ChatMessage[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [sessions, setSessions] = React.useState<AiSession[]>([]);
  const [activeSessionId, setActiveSessionId] = React.useState<number | null>(
    null,
  );
  const ids = React.useMemo(() => validModelIds(modelConfigs), [modelConfigs]);
  const initialPrefs = React.useMemo(
    () => loadModelPrefs(bookId, ids, aiAgentMode),
    [bookId, ids.join(","), aiAgentMode],
  );
  const [selectedModel, setSelectedModel] = React.useState<string>(
    initialPrefs.model,
  );
  const [chatAgentMode, setChatAgentMode] = React.useState(
    initialPrefs.chatAgentMode,
  );
  const [thinkingEnabled, setThinkingEnabled] = React.useState(
    initialPrefs.thinkingEnabled,
  );
  const [agentActions, setAgentActions] = React.useState<PipelineStageId[]>([
    "full",
  ]);

  const selectedModelConfig = React.useMemo(
    () => modelConfigs.find((c) => c.id === selectedModel) ?? null,
    [modelConfigs, selectedModel],
  );
  const modelConfigsRecord = React.useMemo(() => {
    const r: Record<string, { label?: string; max_tokens?: number }> = {};
    modelConfigs.forEach((c) => {
      r[c.id] = { label: c.name, max_tokens: 8192 };
    });
    return r;
  }, [modelConfigs]);

  React.useEffect(() => {
    const prefs = loadModelPrefs(bookId, ids, aiAgentMode);
    setSelectedModel(prefs.model);
    setChatAgentMode(prefs.chatAgentMode);
    setThinkingEnabled(prefs.thinkingEnabled);
  }, [bookId, ids.join(","), aiAgentMode]);

  React.useEffect(() => {
    if (ids.length && !ids.includes(selectedModel)) {
      setSelectedModel(ids[0]);
    }
    if (ids.length === 0 && selectedModel) {
      setSelectedModel("");
    }
  }, [ids, selectedModel]);

  React.useEffect(() => {
    saveModelPrefs(bookId, selectedModel, chatAgentMode, thinkingEnabled);
  }, [bookId, selectedModel, chatAgentMode, thinkingEnabled]);
  const [favoritesModalOpen, setFavoritesModalOpen] = React.useState(false);
  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false);
  const [selectedMemoryIds, setSelectedMemoryIds] = React.useState<
    (number | string)[]
  >([]);
  const [selectedForeshadowingIds, setSelectedForeshadowingIds] =
    React.useState<(number | string)[]>([]);
  const [editingTabId, setEditingTabId] = React.useState<number | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const [contextPopoverOpen, setContextPopoverOpen] = React.useState(false);
  const [pipelinePopoverOpen, setPipelinePopoverOpen] = React.useState(false);

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

  const { handleSubmit: doSubmit, handleAbort, runningSessionIdRef, runningAccRef } = useChatSubmit({
    selectedModelConfig,
    prompt,
    setPrompt,
    loading,
    setLoading,
    conversations,
    setConversations,
    bookId: bookId ?? undefined,
    chapterId,
    activeSessionId,
    setActiveSessionId,
    sessions,
    setSessions,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    currentChapterTitle: activeChapterTitle || undefined,
    selectedModel,
    thinkingEnabled,
    agentEnabled: chatAgentMode !== "ask",
    modelConfigs: modelConfigsRecord,
    selectedMemoryIds,
    selectedForeshadowingIds,
    agentMode: chatAgentMode === "expert" ? "subagent" : "legacy",
    writingMode: chatAgentMode === "collab" ? "collab" : "default",
    agentActions:
      chatAgentMode === "expert" ? agentActions : undefined,
  });

  const [editingMessageIndex, setEditingMessageIndex] = React.useState<
    number | null
  >(null);
  const editTextareaRef = React.useRef<TextAreaRef | null>(null);
  const editingMessageDraftRef = React.useRef("");
  const pinNewTurnToTopRef = React.useRef(false);

  const getEditTextareaValue = React.useCallback(() => {
    const el = editTextareaRef.current;
    if (!el) return "";
    const textarea =
      el.resizableTextArea?.textArea ??
      (el.nativeElement as HTMLTextAreaElement | null);
    return textarea?.value ?? "";
  }, []);

  React.useEffect(() => {
    if (editingMessageIndex == null) return;
    const raf = requestAnimationFrame(() => {
      const el = editTextareaRef.current;
      const textarea =
        el?.resizableTextArea?.textArea ??
        (el?.nativeElement as HTMLTextAreaElement | null);
      if (!textarea) return;
      const end = textarea.value.length;
      textarea.focus();
      textarea.setSelectionRange(end, end);
    });
    return () => cancelAnimationFrame(raf);
  }, [editingMessageIndex]);

  const handleSubmit = React.useCallback(() => {
    pinNewTurnToTopRef.current = true;
    doSubmit();
    setSelectedMemoryIds([]);
    setSelectedForeshadowingIds([]);
  }, [doSubmit]);

  const handleEditSend = React.useCallback(
    (editIndex: number, content?: string) => {
      const raw = content ?? getEditTextareaValue() ?? editingMessageDraftRef.current;
      const trimmed = raw.trim();
      if (!trimmed) return;
      pinNewTurnToTopRef.current = true;
      doSubmit({ editIndex, content: trimmed });
      setEditingMessageIndex(null);
      editingMessageDraftRef.current = "";
      setSelectedMemoryIds([]);
      setSelectedForeshadowingIds([]);
    },
    [doSubmit, getEditTextareaValue],
  );

  const virtuosoRef = React.useRef<VirtuosoHandle>(null);
  const loadKeyRef = React.useRef<string>("");
  /** 用户主动上滚后为 true，不再自动滚到底部；滚回底部或点击「回到底部」后恢复为 false */
  const [userHasScrolledUp, setUserHasScrolledUp] = React.useState(false);
  const [isAtBottom, setIsAtBottom] = React.useState(true);
  /** 向上滚动时加载的历史消息（逆序：越靠前越旧）；与 conversations 合并后为 [历史...当前] */
  const [prependedHistory, setPrependedHistory] = React.useState<ChatMessage[]>(
    [],
  );
  const [inputAreaHeight, setInputAreaHeight] =
    React.useState(INPUT_AREA_DEFAULT);
  const inputAreaDragRef = React.useRef(false);
  const setScrolledUpByReason = React.useCallback(
    (nextValue: boolean, _reason: string) => {
      setUserHasScrolledUp((prev) => {
        if (prev === nextValue) return prev;
        return nextValue;
      });
    },
    [],
  );

  // 书籍/章节变化时加载 session 列表（仅按当前章节隔离）
  React.useEffect(() => {
    if (bookId == null || chapterId == null) {
      setConversations([]);
      setSessions([]);
      setActiveSessionId(null);
      setLoading(false);
      return;
    }
    const key = `${bookId}-${chapterId}`;
    if (loadKeyRef.current === key) return;
    loadKeyRef.current = key;
    setConversations([]);
    setSessions([]);
    setActiveSessionId(null);
    setLoading(false);

    window.electronAPI
      .getSessions({ bookId, chapterId })
      .then((res) => {
        if (loadKeyRef.current !== key) return;
        if (res.success && res.data.length > 0) {
          setSessions(res.data);
          setActiveSessionId(res.data[res.data.length - 1].id);
        }
        // 无会话时不自动创建，须由用户点击「新建对话」或从历史打开
      });
  }, [bookId, chapterId]);

  // 切换会话时清空预加载的历史，避免混用
  React.useEffect(() => {
    setPrependedHistory([]);
  }, [activeSessionId]);

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

  // 回到底部：使用 Virtuoso 的 scrollToIndex，一次性跳到底部（不 smooth，避免断断续续）
  const handleScrollToBottom = React.useCallback(() => {
    const total = prependedHistory.length + conversations.length;
    if (total > 0) {
      virtuosoRef.current?.scrollToIndex({
        index: total - 1,
        align: "end",
        behavior: "auto",
      });
    }
    setScrolledUpByReason(false, "manual-scroll-to-bottom");
  }, [prependedHistory.length, conversations.length, setScrolledUpByReason]);

  React.useEffect(() => {
    if (!pinNewTurnToTopRef.current) return;
    const total = prependedHistory.length + conversations.length;
    if (total < 2) return;
    // 发送后优先展示本轮用户消息顶部，避免被流式自动贴底立即覆盖
    setScrolledUpByReason(true, "pin-new-turn-top");
    requestAnimationFrame(() => {
      virtuosoRef.current?.scrollToIndex({
        index: total - 2,
        align: "start",
        behavior: "auto",
      });
    });
    pinNewTurnToTopRef.current = false;
  }, [
    prependedHistory.length,
    conversations.length,
    setScrolledUpByReason,
  ]);

  const handleInputAreaDividerMouseDown = React.useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      inputAreaDragRef.current = true;
      document.body.style.cursor = "ns-resize";
      document.body.style.userSelect = "none";
    },
    [],
  );

  React.useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!inputAreaDragRef.current) return;
      setInputAreaHeight((h) =>
        Math.min(INPUT_AREA_MAX, Math.max(INPUT_AREA_MIN, h - e.movementY)),
      );
    };
    const onMouseUp = () => {
      inputAreaDragRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const handleNewSession = React.useCallback(async () => {
    if (bookId == null) return;
    if (chapterId == null) {
      appMessage.warning("请先选择一个章节，再创建对话");
      return;
    }
    if (loading) {
      appMessage.warning("当前对话进行中，请先等待完成或停止");
      return;
    }
    if (sessions.length > 0 && conversations.length === 0) return;
    const res = await window.electronAPI.createSession({
      bookId,
      chapterId,
    });
    if (!res.success || !res.data) return;
    setSessions((prev) => [...prev, res.data]);
    setActiveSessionId(res.data.id);
  }, [bookId, chapterId, sessions.length, conversations.length, loading, appMessage]);

  // 关闭 Tab：从标签栏移除并写入数据库 closed=1，刷新列表时不再展示
  const handleCloseTab = React.useCallback(
    (session: AiSession) => {
      window.electronAPI.setSessionClosed({ sessionId: session.id });
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== session.id);
        if (activeSessionId === session.id) {
          setActiveSessionId(next[next.length - 1]?.id ?? null);
        }
        return next;
      });
    },
    [activeSessionId],
  );

  // 历史弹窗：点击打开某条对话时标记为已重新打开（closed=0），并加入标签栏
  const handleOpenFromHistory = React.useCallback(
    (session: AiSession) => {
      if (loading) {
        appMessage.warning("当前对话进行中，请先等待完成或停止");
        return;
      }
      window.electronAPI.setSessionReopened({ sessionId: session.id });
      setSessions((prev) =>
        prev.some((s) => s.id === session.id) ? prev : [...prev, session],
      );
      setActiveSessionId(session.id);
    },
    [loading, appMessage],
  );

  // 历史弹窗：删除对话后同步标签栏
  const handleDeleteFromHistory = React.useCallback(
    (session: AiSession) => {
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== session.id);
        if (activeSessionId === session.id) {
          setActiveSessionId(next[next.length - 1]?.id ?? null);
        }
        return next;
      });
    },
    [activeSessionId],
  );

  const currentSessionTitle =
    activeSessionId != null
      ? (sessions.find((s) => s.id === activeSessionId)?.title ?? "新对话")
      : "新对话";

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

  const ellipsisMenuItems: MenuProps["items"] = [
    {
      key: "favorites",
      label: "查看收藏列表",
      onClick: () => setFavoritesModalOpen(true),
    },
  ];

  const handleSaveTabTitle = React.useCallback(async () => {
    if (editingTabId == null) return;
    const title = editingTitle.trim();
    if (!title) {
      appMessage.warning("名称不能为空");
      setEditingTabId(null);
      return;
    }
    const res = await window.electronAPI.updateSessionTitle({
      sessionId: editingTabId,
      title,
    });
    if (res.success) {
      setSessions((prev) =>
        prev.map((s) => (s.id === editingTabId ? { ...s, title } : s)),
      );
    }
    setEditingTabId(null);
  }, [editingTabId, editingTitle, appMessage]);

  // 虚拟列表数据：历史（上） + 当前会话（下）；firstItemIndex 用于将来向上加载更多时保持滚动位置
  const combinedData = React.useMemo(
    () => [...prependedHistory, ...conversations],
    [prependedHistory, conversations],
  );
  const firstItemIndex = INITIAL_FIRST_ITEM_INDEX - prependedHistory.length;

  const streamFollowKey = React.useMemo(() => {
    if (!loading || userHasScrolledUp || combinedData.length === 0) return "";
    const last = combinedData[combinedData.length - 1] as ChatMessage | undefined;
    if (!last || last.role !== "assistant" || last.isError) return "";
    const key = [
      combinedData.length,
      last.content ?? "",
      last.thinking ?? "",
      last.contentAfterToolCalls ?? "",
      last.toolCalling ? "1" : "0",
    ].join("|");
    return key;
  }, [loading, userHasScrolledUp, combinedData]);

  // 流式输出期间（用户未主动上滑）保持视图贴底
  React.useEffect(() => {
    if (!streamFollowKey) return;
    const raf = requestAnimationFrame(() => {
      virtuosoRef.current?.scrollToIndex({
        index: combinedData.length - 1,
        align: "end",
        behavior: "auto",
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [streamFollowKey, combinedData.length]);

  return (
    <div className={`ai-panel ${isFullscreen ? "fullscreen" : ""}`}>
      <div className="panel-header">
        <span className="panel-title">AI 对话</span>
        <div className="panel-header-actions">
          <Button
            type="text"
            size="small"
            icon={
              isFullscreen ? (
                <CompressOutlined style={{ fontSize: 16 }} />
              ) : (
                <ExpandOutlined style={{ fontSize: 16 }} />
              )
            }
            title={isFullscreen ? "退出全屏" : "全屏"}
            onClick={onToggleFullscreen}
          />
          <Dropdown menu={{ items: ellipsisMenuItems }} trigger={["click"]}>
            <Tooltip title="更多" mouseEnterDelay={0.5}>
              <Button
                type="text"
                size="small"
                icon={<EllipsisOutlined style={{ fontSize: 16 }} />}
                className="panel-header-action-btn"
              />
            </Tooltip>
          </Dropdown>
        </div>
      </div>

      {/* Session 切换栏：仅在已选章节时显示（按章节隔离） */}
      {bookId != null && chapterId != null && (
        <Tabs
          type="editable-card"
          hideAdd
          className="session-tabs-bar"
          size="small"
          activeKey={activeSessionId ? String(activeSessionId) : undefined}
          onChange={(key) => {
            if (loading) {
              appMessage.warning("当前对话进行中，请先等待完成或停止");
              return;
            }
            setActiveSessionId(Number(key));
          }}
          onEdit={(targetKey, action) => {
            if (action === "remove") {
              const session = sessions.find((s) => String(s.id) === targetKey);
              if (session) handleCloseTab(session);
            }
          }}
          tabBarExtraContent={
            <div className="session-tab-actions">
              <Tooltip
                title={
                  sessions.length > 0 && conversations.length === 0
                    ? "当前对话尚未开始"
                    : "新建对话"
                }
              >
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined style={{ fontSize: 13 }} />}
                  onClick={handleNewSession}
                  disabled={
                    loading || (sessions.length > 0 && conversations.length === 0)
                  }
                  className="session-new-btn"
                />
              </Tooltip>
              <SessionHistoryPopover
                bookId={bookId}
                chapterId={chapterId}
                activeSessionId={activeSessionId}
                onOpen={handleOpenFromHistory}
                onDelete={handleDeleteFromHistory}
              />
            </div>
          }
          items={sessions.map((s) => ({
            key: String(s.id),
            label:
              editingTabId === s.id ? (
                <Input
                  size="small"
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={handleSaveTabTitle}
                  onPressEnter={handleSaveTabTitle}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                  className="session-tab-edit-input"
                  autoFocus
                />
              ) : (
                <Tooltip
                  title={s.title}
                  placement="bottom"
                  mouseEnterDelay={0.6}
                  styles={{
                    root: { maxWidth: 320 },
                    container: {
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                    },
                  }}
                >
                  <span
                    className="session-tab-title"
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      setEditingTabId(s.id);
                      setEditingTitle(s.title);
                    }}
                  >
                    {s.title}
                  </span>
                </Tooltip>
              ),
            closable: true,
          }))}
        />
      )}

      <div className="chat-history-wrap">
        <div className="chat-history">
          {combinedData.length === 0 && !loading && (
            <Empty
              image={false}
              description={
                bookId == null
                  ? "请先选择书籍"
                  : sessions.length === 0
                    ? "开始与 AI 对话，请从上方 + 新建对话"
                    : "开始与 AI 对话"
              }
              className="chat-empty"
            />
          )}

          {combinedData.length > 0 && (
            <Virtuoso
              ref={virtuosoRef}
              data={combinedData}
              firstItemIndex={firstItemIndex}
              initialTopMostItemIndex={{
                index: combinedData.length - 1,
                align: "end",
              }}
              alignToBottom={!userHasScrolledUp}
              followOutput="auto"
              atBottomThreshold={40}
              atBottomStateChange={(atBottom) => {
                setIsAtBottom(atBottom);
                if (atBottom) return;
                // 新交互：流式期间，向上滚动后锁定“停止展示最新”，不被 atBottom 自动复位
                if (loading) {
                  setScrolledUpByReason(true, "atBottom-false-while-loading");
                } else {
                  setScrolledUpByReason(true, "atBottom-false-idle");
                }
              }}
              atTopStateChange={() => {
                /* 向上滚动加载历史：可在此接入分页 API */
              }}
              computeItemKey={(index) => firstItemIndex + index}
              components={{
                List: React.forwardRef<HTMLDivElement, ListProps>(
                  ({ style, children, ...rest }, ref) => (
                    <div
                      ref={ref}
                      style={style}
                      className="chat-virtuoso-list"
                      {...rest}
                    >
                      {children}
                    </div>
                  ),
                ),
              }}
              style={{ flex: 1, minHeight: 0 }}
              itemContent={(index, msg) => {
                const dataIndex = index - firstItemIndex;
                const convIndex = dataIndex - prependedHistory.length;
                const isLast = dataIndex === combinedData.length - 1;
                const cm = msg as ChatMessage;
                const hasThinkingBlocks = (cm.thinkingBlocks?.length ?? 0) > 0;
                const hasAnyThinking =
                  hasThinkingBlocks ||
                  (cm.thinking !== undefined && cm.thinking !== "");
                const hasSubagentProgress = Boolean(
                  cm.subagentStages?.length ||
                    cm.subagentStageName ||
                    cm.subagentStageId ||
                    cm.subagentBridging ||
                    cm.subagentMainPresenter,
                );
                const isEmpty =
                  !msg.content &&
                  !cm.toolCallSegments?.length &&
                  !hasAnyThinking &&
                  !hasSubagentProgress &&
                  !(cm.subagentPipelineDigest || "").trim();
                const isLastAssistant =
                  isLast && msg.role === "assistant" && !msg.isError;
                /** 发送后占位：最后一条且为空内容时显示「思考中」+ 闪烁「...」 */
                const showPlaceholder = isLastAssistant && isEmpty;
                /** 有内容时，等待中在气泡内显示的小 spinner / 闪烁「...」 */
                const showWaitingInBubble =
                  isLastAssistant && loading && !cm.toolCalling;
                if (msg.role === "assistant" && isEmpty && !isLast) {
                  return (
                    <div className="chat-bubble assistant">
                      <div className="bubble-label">AI</div>
                      <div className="bubble-content">
                        内容同步中。
                      </div>
                    </div>
                  );
                }
                return (
                  <div
                    className={`chat-bubble ${msg.role} ${msg.isError ? "error" : ""} ${msg.role === "user" && convIndex >= 0 && editingMessageIndex === convIndex ? "chat-bubble--editing" : ""}`}
                  >
                    <div className="bubble-label">
                      {msg.role === "user" ? "你" : "AI"}
                    </div>
                    {msg.role === "user" &&
                      convIndex >= 0 &&
                      editingMessageIndex === convIndex && (
                        <div className="bubble-content bubble-content--edit">
                          {bookId != null && (
                            <AiContextBar
                              bookId={bookId}
                              chapterId={chapterId ?? null}
                              associatedChapterIds={associatedChapterIds}
                              setAssociatedChapterIds={setAssociatedChapterIds}
                              associatedOutlineIds={associatedOutlineIds}
                              setAssociatedOutlineIds={setAssociatedOutlineIds}
                              chapterSelectOptions={chapterSelectOptions}
                              outlineSelectOptions={outlineSelectOptions}
                              onQuickAssociateChapter={
                                handleQuickAssociateChapter
                              }
                              onQuickAssociateOutline={
                                handleQuickAssociateOutline
                              }
                              selectedMemoryIds={selectedMemoryIds}
                              selectedForeshadowingIds={
                                selectedForeshadowingIds
                              }
                              onOpenMemoryModal={() => setMemoryModalOpen(true)}
                              contextPopoverOpen={contextPopoverOpen}
                              onContextPopoverOpenChange={setContextPopoverOpen}
                              pipelinePopoverOpen={pipelinePopoverOpen}
                              onPipelinePopoverOpenChange={setPipelinePopoverOpen}
                              pipelineAgentEnabled={
                                chatAgentMode === "expert"
                              }
                              pipelineSelectedStages={agentActions}
                              onPipelineStagesChange={setAgentActions}
                            />
                          )}
                          <Input.TextArea
                            key={`edit-${editingMessageIndex}`}
                            className="bubble-edit-textarea"
                            defaultValue={editingMessageDraftRef.current}
                            ref={editTextareaRef}
                            placeholder="编辑内容，发送将从此处重新对话…"
                            autoSize={{ minRows: 2, maxRows: 8 }}
                            autoFocus
                            onChange={(e) => {
                              editingMessageDraftRef.current = e.target.value;
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                handleEditSend(convIndex);
                              }
                            }}
                          />
                          <AiComposeBottom
                            modelConfigs={modelConfigs}
                            thinkingOnlyModelIds={thinkingOnlyModelIds(modelConfigs)}
                            chatAgentMode={chatAgentMode}
                            setChatAgentMode={setChatAgentMode}
                            selectedModel={selectedModel}
                            setSelectedModel={setSelectedModel}
                            thinkingEnabled={thinkingEnabled}
                            setThinkingEnabled={setThinkingEnabled}
                            loading={false}
                            onAbort={handleAbort}
                            rightContent={
                              <div className="bubble-edit-actions">
                                <Button
                                  type="text"
                                  size="small"
                                  onClick={() => {
                                    setEditingMessageIndex(null);
                                    editingMessageDraftRef.current = "";
                                  }}
                                >
                                  取消
                                </Button>
                                <Button
                                  type="primary"
                                  size="small"
                                  onClick={() => handleEditSend(convIndex)}
                                >
                                  发送
                                </Button>
                              </div>
                            }
                          />
                        </div>
                      )}
                    {msg.role === "user" &&
                      (convIndex < 0 || editingMessageIndex !== convIndex) && (
                        <>
                          {msg.content ? (
                            <div className="bubble-content">{msg.content}</div>
                          ) : null}
                          {!loading && convIndex >= 0 && (
                            <div className="bubble-user-actions">
                              <Tooltip title="编辑提问">
                                <Button
                                  type="text"
                                  size="small"
                                  icon={
                                    <EditOutlined style={{ fontSize: 12 }} />
                                  }
                                  className="bubble-edit-btn"
                                  onClick={() => {
                                    editingMessageDraftRef.current =
                                      msg.content ?? "";
                                    setEditingMessageIndex(convIndex);
                                  }}
                                />
                              </Tooltip>
                            </div>
                          )}
                        </>
                      )}
                    {msg.role === "assistant" &&
                      !msg.isError &&
                      (() => {
                        const segments =
                          (msg as ChatMessage).toolCallSegments ?? [];
                        const blocks =
                          (msg as ChatMessage).thinkingBlocks ?? [];
                        const isStreaming = loading && isLast;
                        const currentThinking =
                          (msg as ChatMessage).thinking ?? "";
                        const hasGeneratedContent = Boolean(
                          (msg.content || "").trim() ||
                            ((msg as ChatMessage).contentAfterToolCalls || "").trim() ||
                            ((msg as ChatMessage).subagentPipelineDigest || "").trim(),
                        );

                        return (
                          <div className="bubble-assistant-body">
                            {showPlaceholder && (
                              <div className="bubble-content bubble-content--thinking-placeholder">
                                <span className="bubble-placeholder-text">
                                  正在思考
                                </span>
                                <span className="a-blink-dots">...</span>
                              </div>
                            )}
                            {!showPlaceholder && (
                              <>
                                {(cm.subagentPipelineDigest || "").trim() ? (
                                  <div className="bubble-content bubble-content--subagent-digest">
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                      {(cm.subagentPipelineDigest || "").trim()}
                                    </ReactMarkdown>
                                  </div>
                                ) : null}
                                {segments.map((seg, segIdx) => {
                                  const cm = msg as ChatMessage;
                                  const isToolLive =
                                    isLastAssistant &&
                                    loading &&
                                    segIdx === segments.length - 1 &&
                                    Boolean(cm.toolCalling) &&
                                    seg.labels.length > 0;
                                  const toolCompletedCount = isToolLive
                                    ? (seg.completedToolCount ?? 0)
                                    : seg.labels.length;
                                  return (
                                    <React.Fragment key={segIdx}>
                                      {blocks[segIdx]?.trim() && (
                                        <ThinkingRegion
                                          key={`${index}-seg-${segIdx}`}
                                          regionKey={`${index}-seg-${segIdx}`}
                                          content={blocks[segIdx]}
                                          streaming={false}
                                          defaultOpen={false}
                                          onWheelUp={() =>
                                            setScrolledUpByReason(
                                              true,
                                              "thinking-region-wheel-up",
                                            )
                                          }
                                        />
                                      )}
                                      <div className="bubble-content">
                                        {seg.textBefore && (
                                          <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                          >
                                            {seg.textBefore}
                                          </ReactMarkdown>
                                        )}
                                        {seg.labels.length > 0 ? (
                                          <ToolCallStatus
                                            labels={seg.labels}
                                            labelOutcomes={seg.labelOutcomes}
                                            cachedFlags={seg.cachedFlags}
                                            completedToolCount={
                                              toolCompletedCount
                                            }
                                            trace={seg.trace}
                                          />
                                        ) : null}
                                      </div>
                                    </React.Fragment>
                                  );
                                })}
                                {isStreaming &&
                                  currentThinking !== undefined &&
                                  currentThinking !== "" &&
                                  !hasGeneratedContent && (
                                    <ThinkingRegion
                                      key={`${index}-stream-th`}
                                      regionKey={`${index}-stream-main`}
                                      content={currentThinking}
                                      streaming
                                      streamingHeader
                                      showCursor={
                                        !(
                                          msg.content ||
                                          (msg as ChatMessage).contentAfterToolCalls
                                        )
                                      }
                                      defaultOpen
                                      onWheelUp={() =>
                                        setScrolledUpByReason(
                                          true,
                                          "thinking-region-wheel-up",
                                        )
                                      }
                                    />
                                  )}
                                {isStreaming &&
                                  currentThinking !== undefined &&
                                  currentThinking !== "" &&
                                  hasGeneratedContent && (
                                    <ThinkingRegion
                                      key={`${index}-stream-pa`}
                                      regionKey={`${index}-stream-main`}
                                      content={currentThinking}
                                      streaming
                                      defaultOpen={false}
                                      onWheelUp={() =>
                                        setScrolledUpByReason(
                                          true,
                                          "thinking-region-wheel-up",
                                        )
                                      }
                                    />
                                  )}
                                {!isStreaming &&
                                  segments.length > 0 &&
                                  blocks[segments.length]?.trim() && (
                                    <ThinkingRegion
                                      key={`${index}-tail-main`}
                                      regionKey={`${index}-stream-main`}
                                      content={blocks[segments.length]}
                                      streaming={false}
                                      defaultOpen={false}
                                      onWheelUp={() =>
                                        setScrolledUpByReason(
                                          true,
                                          "thinking-region-wheel-up",
                                        )
                                      }
                                    />
                                  )}
                                {!isStreaming &&
                                  segments.length === 0 &&
                                  blocks[0]?.trim() && (
                                    <ThinkingRegion
                                      key={`${index}-tail-main`}
                                      regionKey={`${index}-stream-main`}
                                      content={blocks[0]}
                                      streaming={false}
                                      defaultOpen={false}
                                      onWheelUp={() =>
                                        setScrolledUpByReason(
                                          true,
                                          "thinking-region-wheel-up",
                                        )
                                      }
                                    />
                                  )}
                                {(msg.content ||
                                  (msg as ChatMessage)
                                    .contentAfterToolCalls) && (
                                  <div className="bubble-content">
                                    {(msg as ChatMessage).toolCallSegments
                                      ?.length
                                      ? (msg as ChatMessage)
                                          .contentAfterToolCalls && (
                                          <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                          >
                                            {
                                              (msg as ChatMessage)
                                                .contentAfterToolCalls!
                                            }
                                          </ReactMarkdown>
                                        )
                                      : msg.content && (
                                          <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                          >
                                            {msg.content}
                                          </ReactMarkdown>
                                        )}
                                  </div>
                                )}
                                {!showPlaceholder &&
                                  isLastAssistant &&
                                  loading && (
                                    <div className="bubble-content bubble-content--waiting-dots">
                                      <span className="a-blink-dots">
                                        ...
                                      </span>
                                    </div>
                                  )}
                              </>
                            )}
                            {hasSubagentProgress ? (
                              <SubagentStageStrip
                                message={cm}
                                isLastAssistant={isLastAssistant}
                                loading={loading}
                              />
                            ) : null}
                          </div>
                        );
                      })()}
                    {msg.role === "assistant" &&
                      !msg.isError &&
                      !showPlaceholder &&
                      !(isLastAssistant && loading) && (
                        <div className="bubble-footer">
                          {msg.model && (
                            <span className="bubble-model-tag">
                              {formatModelName(msg.model, modelConfigs)}
                            </span>
                          )}
                          <Tooltip title="收藏">
                            <Button
                              type="text"
                              size="small"
                              icon={<StarOutlined style={{ fontSize: 12 }} />}
                              className="bubble-bookmark-btn"
                              onClick={() =>
                                handleAddFavorite(
                                  combinedData[dataIndex - 1]?.content ?? "",
                                  msg.content,
                                )
                              }
                            />
                          </Tooltip>
                        </div>
                      )}
                  </div>
                );
              }}
            />
          )}
        </div>
        {userHasScrolledUp && !isAtBottom && (
          <Tooltip title="回到底部">
            <Button
              type="primary"
              size="small"
              icon={<VerticalAlignBottomOutlined />}
              className="chat-scroll-to-bottom-btn"
              onClick={handleScrollToBottom}
            />
          </Tooltip>
        )}
      </div>

      <div
        className="chat-input-area-divider"
        onMouseDown={handleInputAreaDividerMouseDown}
        role="separator"
        aria-orientation="horizontal"
        title="拖拽调整输入区域高度"
      />
      <div
        className="chat-input-area"
        style={{
          height: inputAreaHeight,
          minHeight: INPUT_AREA_MIN,
          maxHeight: INPUT_AREA_MAX,
        }}
      >
        {bookId != null && (
          <AiContextBar
            bookId={bookId}
            chapterId={chapterId ?? null}
            associatedChapterIds={associatedChapterIds}
            setAssociatedChapterIds={setAssociatedChapterIds}
            associatedOutlineIds={associatedOutlineIds}
            setAssociatedOutlineIds={setAssociatedOutlineIds}
            chapterSelectOptions={chapterSelectOptions}
            outlineSelectOptions={outlineSelectOptions}
            onQuickAssociateChapter={handleQuickAssociateChapter}
            onQuickAssociateOutline={handleQuickAssociateOutline}
            selectedMemoryIds={selectedMemoryIds}
            selectedForeshadowingIds={selectedForeshadowingIds}
            onOpenMemoryModal={() => setMemoryModalOpen(true)}
            contextPopoverOpen={contextPopoverOpen}
            onContextPopoverOpenChange={setContextPopoverOpen}
            pipelinePopoverOpen={pipelinePopoverOpen}
            onPipelinePopoverOpenChange={setPipelinePopoverOpen}
            pipelineAgentEnabled={chatAgentMode === "expert"}
            pipelineSelectedStages={agentActions}
            onPipelineStagesChange={setAgentActions}
          />
        )}

        <div className="chat-input-inner">
          <Input.TextArea
            className="chat-input"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="告诉我你要创作的内容，或者和我一起讨论你的想法吧💡"
            disabled={loading}
            autoSize={false}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                if (
                  !prompt.trim() ||
                  bookId == null ||
                  chapterId == null ||
                  loading ||
                  (bookId != null &&
                    chapterId != null &&
                    activeSessionId == null)
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
          thinkingOnlyModelIds={thinkingOnlyModelIds(modelConfigs)}
          chatAgentMode={chatAgentMode}
          setChatAgentMode={setChatAgentMode}
          selectedModel={selectedModel}
          setSelectedModel={setSelectedModel}
          thinkingEnabled={thinkingEnabled}
          setThinkingEnabled={setThinkingEnabled}
          loading={loading}
          onAbort={handleAbort}
          rightContent={
            loading ? (
              <Button
                className="btn-submit btn-stop"
                icon={<StopCircleIcon size={18} />}
                type="text"
                onClick={handleAbort}
              />
            ) : (
              <Button
                type="primary"
                className="btn-submit"
                onClick={handleSubmit}
                disabled={
                  !prompt.trim() ||
                  bookId == null ||
                  chapterId == null ||
                  loading ||
                  (bookId != null &&
                    chapterId != null &&
                    activeSessionId == null)
                }
              >
                发送
              </Button>
            )
          }
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
          setSelectedMemoryIds(memoryIds);
          setSelectedForeshadowingIds(foreshadowingIds);
        }}
      />
    </div>
  );
}
