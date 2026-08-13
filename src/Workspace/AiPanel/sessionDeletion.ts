import type { AiSession } from '../../types.ts'

export function reconcileDeletedOpenSessions(
  sessions: AiSession[],
  deletedSessionId: number,
  currentActiveSessionId: number | null,
): { sessions: AiSession[]; activeSessionId: number | null } {
  const remaining = sessions.filter((session) => session.id !== deletedSessionId)
  return {
    sessions: remaining,
    activeSessionId: currentActiveSessionId === deletedSessionId
      ? remaining.at(-1)?.id ?? null
      : currentActiveSessionId,
  }
}
