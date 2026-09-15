import type { AgentConversationSession } from './controller.ts'

export function toAgentConversationSession<
  TSession extends Pick<AgentConversationSession, 'id' | 'title'>,
>(
  session: TSession,
  createdAt?: string,
): AgentConversationSession & { id: TSession['id'] } {
  return {
    id: session.id,
    title: session.title,
    createdAt,
  }
}

export function sortConversationSessionsNewestFirst<
  TSession extends Pick<AgentConversationSession, 'createdAt'>,
>(sessions: readonly TSession[]): TSession[] {
  return sessions
    .map((session, index) => ({ session, index, createdAt: session.createdAt
      ? Date.parse(session.createdAt.replace(' ', 'T') + (/[zZ]|[+-]\d\d(?::?\d\d)?$/.test(session.createdAt) ? '' : 'Z'))
      : Number.NaN }))
    .sort((left, right) => {
      const leftValid = Number.isFinite(left.createdAt)
      const rightValid = Number.isFinite(right.createdAt)
      if (leftValid && rightValid && left.createdAt !== right.createdAt) {
        return right.createdAt - left.createdAt
      }
      if (leftValid !== rightValid) return leftValid ? -1 : 1
      return left.index - right.index
    })
    .map(({ session }) => session)
}
