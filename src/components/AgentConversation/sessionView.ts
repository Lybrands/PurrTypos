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
