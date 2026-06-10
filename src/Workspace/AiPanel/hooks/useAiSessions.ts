import React from "react";
import { App as AntdApp } from "antd";
import type { AiSession, EntityId } from "../../../types";
import type { ChatMessage } from "./chat.types";

interface UseAiSessionsParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
}

export function useAiSessions({
  bookId,
  chapterId,
  conversations,
  setConversations,
  loading,
  setLoading,
}: UseAiSessionsParams) {
  const { message: appMessage } = AntdApp.useApp();
  const [sessions, setSessions] = React.useState<AiSession[]>([]);
  const [activeSessionId, setActiveSessionId] = React.useState<number | null>(
    null,
  );
  const [prependedHistory, setPrependedHistory] = React.useState<ChatMessage[]>(
    [],
  );
  const [editingTabId, setEditingTabId] = React.useState<number | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const loadKeyRef = React.useRef<string>("");

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
      });
  }, [bookId, chapterId, setConversations, setLoading]);

  React.useEffect(() => {
    setPrependedHistory([]);
  }, [activeSessionId]);

  const handleNewSession = React.useCallback(async () => {
    if (bookId == null) return;
    if (chapterId == null) {
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
      chapterId,
    });
    if (!res.success || !res.data) return;
    setSessions((prev) => [...prev, res.data]);
    setActiveSessionId(res.data.id);
  }, [
    bookId,
    chapterId,
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
