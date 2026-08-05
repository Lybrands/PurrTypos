import type { AiSession } from '../types'

/**
 * Re-entering a screenplay project should restore the conversation the user
 * actually left, instead of blindly opening the newest session created for it.
 */
export function selectScreenplayAgentSession(
  sessions: AiSession[],
  preferredSessionId: number | null,
  fallbackSessionId: number | null,
): AiSession | null {
  if (preferredSessionId != null) {
    const preferred = sessions.find((session) => session.id === preferredSessionId)
    if (preferred) return preferred
  }

  if (fallbackSessionId != null) {
    const fallback = sessions.find((session) => session.id === fallbackSessionId)
    if (fallback) return fallback
  }

  return sessions.at(-1) ?? null
}
