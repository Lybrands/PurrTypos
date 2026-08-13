import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import { agentConversationMessageKey } from './scrollFollowPolicy.ts'

export interface ViewportEditTarget {
  sessionIdentity: string
  messageKey: string
  index: number
}

export function cancelViewportFrame(
  frame: number | null,
  cancel: (frame: number) => void,
): null {
  if (frame != null) cancel(frame)
  return null
}

export function createViewportEditTarget(
  sessionIdentity: string,
  index: number,
  message: AgentConversationMessage,
): ViewportEditTarget {
  return {
    sessionIdentity,
    messageKey: agentConversationMessageKey(index, message),
    index,
  }
}

export function resolveViewportEditTarget(
  target: ViewportEditTarget | null,
  sessionIdentity: string,
  messages: AgentConversationMessage[],
): number | undefined {
  if (!target || target.sessionIdentity !== sessionIdentity) return undefined
  const message = messages[target.index]
  if (
    !message
    || message.role !== 'user'
    || agentConversationMessageKey(target.index, message) !== target.messageKey
  ) return undefined
  return target.index
}
