import { services } from '@/services'
import React from "react";
import { usePurrToast } from '@/purr-components';
import type { AiSession, EntityId } from "../../../types";
import type { ChatMessage } from "./chat.types";

// "setting" 为历史存储值，对应 UI 上的「全局对话」（不绑章节、整本书共享）
export type ChatSessionScope = "chapter" | "setting";

// AiPanel 会随工作台切换卸载；把每个作用域最后打开的会话保留在模块生命周期内，
// 返回工作台时优先恢复原会话，而不是总是跳到列表最后一项。
const activeSessionByLoadKey = new Map<string, number>();

interface UseAiSessionsParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  /** 会话作用域：chapter = 按章节隔离（默认）；setting = 不绑章节的全局会话 */
  scope?: ChatSessionScope;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
}

export function useAiSessions({
  bookId,
  chapterId,
  scope = "chapter",
  conversations,
  setConversations,
  setLoading,
}: UseAiSessionsParams) {
  const appMessage = usePurrToast();
  const [sessions, setSessions] = React.useState<AiSession[]>([]);
  const [activeSessionId, setActiveSessionIdState] = React.useState<
    number | null
  >(null);
  const [prependedHistory, setPrependedHistory] = React.useState<ChatMessage[]>(
    [],
  );
  /** 当前 book+scope(+chapter) 维度的会话列表是否已完成首次拉取 */
  const [sessionsLoaded, setSessionsLoaded] = React.useState(false);
  const loadKeyRef = React.useRef<string>("");
  const setActiveSessionId = React.useCallback<
    React.Dispatch<React.SetStateAction<number | null>>
  >((next) => {
    setActiveSessionIdState((current) => {
      const resolved = typeof next === "function" ? next(current) : next;
      if (resolved != null && loadKeyRef.current) {
        activeSessionByLoadKey.set(loadKeyRef.current, resolved);
      }
      return resolved;
    });
  }, []);

  React.useEffect(() => {
    const isSettingScope = scope === "setting";
    if (bookId == null || (!isSettingScope && chapterId == null)) {
      setConversations([]);
      setSessions([]);
      setActiveSessionIdState(null);
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
    setActiveSessionIdState(null);
    setSessionsLoaded(false);
    setLoading(false);

    services.sessions
      .getSessions(
        isSettingScope
          ? { bookId, scope: "setting" }
          : { bookId, chapterId },
      )
      .then((res) => {
        if (loadKeyRef.current !== key) return;
        if (res.success && res.data.length > 0) {
          setSessions(res.data);
          const rememberedSessionId = activeSessionByLoadKey.get(key);
          const restoredSession = res.data.find(
            (session) => session.id === rememberedSessionId,
          );
          const nextSessionId =
            restoredSession?.id ?? res.data[res.data.length - 1].id;
          activeSessionByLoadKey.set(key, nextSessionId);
          setActiveSessionIdState(nextSessionId);
        } else if (res.success) {
          activeSessionByLoadKey.delete(key);
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
    if (sessions.length > 0 && conversations.length === 0) return;
    const res = await services.sessions.createSession({
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
    appMessage,
  ]);

  const handleCloseTab = React.useCallback(
    (session: AiSession) => {
      services.sessions.setSessionClosed({ sessionId: session.id });
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
      services.sessions.setSessionReopened({ sessionId: session.id });
      setSessions((prev) =>
        prev.some((s) => s.id === session.id) ? prev : [...prev, session],
      );
      setActiveSessionId(session.id);
    },
    [],
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

  const handleRenameSession = React.useCallback(async (
    sessionId: number,
    title: string,
  ) => {
    const nextTitle = title.trim();
    if (!nextTitle) {
      appMessage.warning("名称不能为空");
      return;
    }
    const res = await services.sessions.updateSessionTitle({
      sessionId,
      title: nextTitle,
    });
    if (res.success) {
      setSessions((prev) => prev.map((session) => (
        session.id === sessionId ? { ...session, title: nextTitle } : session
      )));
    }
  }, [appMessage]);

  return {
    sessions,
    setSessions,
    sessionsLoaded,
    activeSessionId,
    setActiveSessionId,
    prependedHistory,
    setPrependedHistory,
    handleNewSession,
    handleCloseTab,
    handleOpenFromHistory,
    handleDeleteFromHistory,
    currentSessionTitle,
    handleRenameSession,
  };
}
