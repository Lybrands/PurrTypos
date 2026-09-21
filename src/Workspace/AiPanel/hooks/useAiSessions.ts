import { services } from '@/services'
import React from "react";
import { usePurrToast } from '@/purr-components';
import type { AiSession, EntityId } from "../../../types";
import type { AgentConversationMessage } from "../../../agent-runtime/contracts";
import { reconcileDeletedOpenSessions } from '../sessionDeletion'

// "setting" 为历史存储值，对应 UI 上的「全局对话」（不绑章节、整本书共享）
export type ChatSessionScope = "chapter" | "setting";

// AiPanel 会随工作台切换卸载；把每个作用域最后打开的会话保留在模块生命周期内，
// 返回工作台时优先恢复原会话，而不是总是跳到列表最后一项。
const activeSessionByLoadKey = new Map<string, number>();

// 模块级记忆随应用重启丢失；按作用域（book-chapter / book-__setting__）再落
// 一份 localStorage，重启后仍能定位到上次访问的那个对话。
const ACTIVE_SESSION_STORAGE_KEY = "purrtypos_active_session_by_scope";

export function loadRememberedSessionMap(): Record<string, number> {
  try {
    const raw = localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    const obj = raw ? JSON.parse(raw) : null;
    if (!obj || typeof obj !== "object") return {};
    const entries = Object.entries(obj).filter(
      (entry): entry is [string, number] =>
        typeof entry[1] === "number" && Number.isFinite(entry[1]),
    );
    return Object.fromEntries(entries);
  } catch {
    return {};
  }
}

export function rememberActiveSession(loadKey: string, sessionId: number) {
  try {
    const map = loadRememberedSessionMap();
    map[loadKey] = sessionId;
    localStorage.setItem(
      ACTIVE_SESSION_STORAGE_KEY,
      JSON.stringify(map),
    );
  } catch {
    // ignore
  }
}

interface UseAiSessionsParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  /** 会话作用域：chapter = 按章节隔离（默认）；setting = 不绑章节的全局会话 */
  scope?: ChatSessionScope;
  conversations: AgentConversationMessage[];
  setConversations: React.Dispatch<React.SetStateAction<AgentConversationMessage[]>>;
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
  const activeSessionIdRef = React.useRef<number | null>(null)
  activeSessionIdRef.current = activeSessionId
  const sessionsRef = React.useRef<AiSession[]>([])
  sessionsRef.current = sessions
  const [prependedHistory, setPrependedHistory] = React.useState<AgentConversationMessage[]>(
    [],
  );
  /** 当前 book+scope(+chapter) 维度的会话列表是否已完成首次拉取 */
  const [sessionsLoaded, setSessionsLoaded] = React.useState(false);
  const loadKeyRef = React.useRef<string>("");
  /** 建会话的去重：自动建默认会话与手动 +/发送兜底并发时只建一个 */
  const createInFlightRef = React.useRef<Promise<number | null> | null>(null);
  const setActiveSessionId = React.useCallback<
    React.Dispatch<React.SetStateAction<number | null>>
  >((next) => {
    setActiveSessionIdState((current) => {
      const resolved = typeof next === "function" ? next(current) : next;
      activeSessionIdRef.current = resolved
      if (resolved != null && loadKeyRef.current) {
        activeSessionByLoadKey.set(loadKeyRef.current, resolved);
        rememberActiveSession(loadKeyRef.current, resolved);
      }
      return resolved;
    });
  }, []);

  /**
   * 写作范围的建会话（自动默认会话与手动 + 共用）：
   * - 同一时刻只允许一个创建请求在途（并发去重）；
   * - 请求返回时校验 loadKey 未变化，避免切书/切章后把会话挂到旧范围。
   */
  const createSessionOnce = React.useCallback(
    async (
      bookIdValue: NonNullable<typeof bookId>,
      chapterIdValue: EntityId | null,
      expectedLoadKey: string,
    ): Promise<number | null> => {
      if (createInFlightRef.current) return createInFlightRef.current;
      const task = (async () => {
        try {
          if (loadKeyRef.current !== expectedLoadKey) return null;
          const res = await services.sessions.createSession({
            bookId: bookIdValue,
            chapterId: chapterIdValue,
          });
          if (!res.success || !res.data) return null;
          if (loadKeyRef.current !== expectedLoadKey) return null;
          const created = res.data;
          setSessions((prev) =>
            prev.some((session) => session.id === created.id)
              ? prev
              : [...prev, created],
          );
          setActiveSessionIdState(created.id);
          return created.id;
        } finally {
          createInFlightRef.current = null;
        }
      })();
      createInFlightRef.current = task;
      return task;
    },
    [],
  );

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
          // 内存记忆优先（本次运行内最新），重启后回退 localStorage 记忆；
          // 都没有时落到列表最新一项，并同步写回两份记忆。
          const rememberedSessionId = activeSessionByLoadKey.get(key)
            ?? loadRememberedSessionMap()[key];
          const restoredSession = res.data.find(
            (session) => session.id === rememberedSessionId,
          );
          const nextSessionId =
            restoredSession?.id ?? res.data[res.data.length - 1].id;
          activeSessionByLoadKey.set(key, nextSessionId);
          rememberActiveSession(key, nextSessionId);
          setActiveSessionIdState(nextSessionId);
        } else if (res.success) {
          activeSessionByLoadKey.delete(key);
          // 默认存在一个对话：首次拉取为空时静默创建默认会话，
          // 会话出现后列表正常呈现（仅零会话时才显示空态，见 composerPolicy）。
          if (!isSettingScope && bookId != null) {
            void createSessionOnce(bookId, chapterId ?? null, key)
              .catch(() => undefined);
          }
        }
        setSessionsLoaded(true);
      });
  }, [bookId, chapterId, scope, setConversations, setLoading, createSessionOnce]);

  React.useEffect(() => {
    setPrependedHistory([]);
  }, [activeSessionId]);

  const handleNewSession = React.useCallback(async (): Promise<number | null> => {
    if (bookId == null) return null;
    const isSettingScope = scope === "setting";
    if (!isSettingScope && chapterId == null) {
      appMessage.warning("请选择一个章节，再创建对话");
      return null;
    }
    if (sessions.length > 0 && conversations.length === 0) return null;
    const res = await services.sessions.createSession({
      bookId,
      chapterId: isSettingScope ? null : chapterId,
      ...(isSettingScope ? { scope: "setting" as const } : {}),
    });
    if (!res.success || !res.data) return null;
    setSessions((prev) => [...prev, res.data]);
    setActiveSessionId(res.data.id);
    return res.data.id;
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
      const reconciled = reconcileDeletedOpenSessions(
        sessionsRef.current,
        session.id,
        activeSessionIdRef.current,
      )
      sessionsRef.current = reconciled.sessions
      setSessions(reconciled.sessions)
      if (reconciled.activeSessionId !== activeSessionIdRef.current) {
        setActiveSessionId(reconciled.activeSessionId)
      }
    },
    [setActiveSessionId],
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

  /** 拖拽排序：先按目标顺序乐观更新本地 sort_order，再整列表落库；失败回滚 */
  const handleReorderSessions = React.useCallback((
    orderedIds: number[],
  ) => {
    if (orderedIds.length === 0) return;
    const previous = sessionsRef.current;
    const rankById = new Map(orderedIds.map((id, index) => [id, index]));
    setSessions((prev) => prev.map((session) => (
      rankById.has(session.id)
        ? { ...session, sort_order: rankById.get(session.id) ?? null }
        : session
    )));
    services.sessions.reorderSessions({ orderedIds })
      .then((res) => {
        if (!res.success) throw new Error(res.error || "reorderSessions failed");
      })
      .catch(() => {
        setSessions(previous);
        appMessage.warning("对话排序保存失败，已还原，请重试");
      });
  }, [appMessage]);

  /** 置顶/取消置顶：乐观更新本地标记并落库；失败回滚 */
  const handleToggleSessionPinned = React.useCallback((
    sessionId: number,
    pinned: boolean,
  ) => {
    setSessions((prev) => prev.map((session) => (
      session.id === sessionId ? { ...session, pinned: pinned ? 1 : 0 } : session
    )));
    services.sessions.updateSessionPinned({ sessionId, pinned })
      .then((res) => {
        if (!res.success) throw new Error(res.error || "updateSessionPinned failed");
      })
      .catch(() => {
        // 仅在本地仍处于乐观值时回滚，避免吞掉紧随其后的再次切换
        setSessions((prev) => prev.map((session) => (
          session.id === sessionId && session.pinned === (pinned ? 1 : 0)
            ? { ...session, pinned: pinned ? 0 : 1 }
            : session
        )));
        appMessage.warning("置顶状态保存失败，已还原，请重试");
      });
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
    handleReorderSessions,
    handleToggleSessionPinned,
  };
}
