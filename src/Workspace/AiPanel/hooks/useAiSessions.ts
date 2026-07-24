import React from "react";
import { useToast } from "../../../ui";
import type { AiSession, EntityId } from "../../../types";
import type { ChatMessage } from "./chat.types";

// "setting" 为历史存储值，对应 UI 上的「全局对话」（不绑章节、整本书共享）
export type ChatSessionScope = "chapter" | "setting";

interface UseAiSessionsParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  /** 会话作用域：chapter = 按章节隔离（默认）；setting = 不绑章节的全局会话 */
  scope?: ChatSessionScope;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
}

export function useAiSessions({
  bookId,
  chapterId,
  scope = "chapter",
  conversations,
  setConversations,
  loading,
  setLoading,
}: UseAiSessionsParams) {
  const appMessage = useToast();
  const [sessions, setSessions] = React.useState<AiSession[]>([]);
  const [activeSessionId, setActiveSessionId] = React.useState<number | null>(
    null,
  );
  const [prependedHistory, setPrependedHistory] = React.useState<ChatMessage[]>(
    [],
  );
  const [editingTabId, setEditingTabId] = React.useState<number | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  /** 当前 book+scope(+chapter) 维度的会话列表是否已完成首次拉取 */
  const [sessionsLoaded, setSessionsLoaded] = React.useState(false);
  const loadKeyRef = React.useRef<string>("");

  React.useEffect(() => {
    const isSettingScope = scope === "setting";
    if (bookId == null || (!isSettingScope && chapterId == null)) {
      setConversations([]);
      setSessions([]);
      setActiveSessionId(null);
      setSessionsLoaded(false);
      setLoading(false);
      return;
    }
    const key = isSettingScope
      ? `${bookId}-__setting__`
      : `${bookId}-${chapterId}`;
    if (loadKeyRef.current === key) return;
    loadKeyRef.current = key;
    setConversations([]);
    setSessions([]);
    setActiveSessionId(null);
    setSessionsLoaded(false);
    setLoading(false);

    window.electronAPI
      .getSessions(
        isSettingScope
          ? { bookId, scope: "setting" }
          : { bookId, chapterId },
      )
      .then((res) => {
        if (loadKeyRef.current !== key) return;
        if (res.success && res.data.length > 0) {
          setSessions(res.data);
          setActiveSessionId(res.data[res.data.length - 1].id);
        }
        setSessionsLoaded(true);
      });
  }, [bookId, chapterId, scope, setConversations, setLoading]);

  React.useEffect(() => {
    setPrependedHistory([]);
  }, [activeSessionId]);

  const handleNewSession = React.useCallback(async () => {
    if (bookId == null) return;
    const isSettingScope = scope === "setting";
    if (!isSettingScope && chapterId == null) {
      appMessage.warning("请选择一个章节，再创建对话");
      return;
    }
    if (loading) {
      appMessage.warning("当前对话进行中，请先等待完成或停止");
      return;
    }
    if (sessions.length > 0 && conversations.length === 0) return;
    const res = await window.electronAPI.createSession({
      bookId,
      chapterId: isSettingScope ? null : chapterId,
      ...(isSettingScope ? { scope: "setting" as const } : {}),
    });
    if (!res.success || !res.data) return;
    setSessions((prev) => [...prev, res.data]);
    setActiveSessionId(res.data.id);
  }, [
    bookId,
    chapterId,
    scope,
    sessions.length,
    conversations.length,
    loading,
    appMessage,
  ]);

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

  return {
    sessions,
    setSessions,
    sessionsLoaded,
    activeSessionId,
    setActiveSessionId,
    prependedHistory,
    setPrependedHistory,
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
  };
}
