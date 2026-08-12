import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'

export interface ScrollFollowState {
  userDetached: boolean
  leftBottomAfterDetach: boolean
}

export function createScrollFollowState(): ScrollFollowState {
  return {
    userDetached: false,
    leftBottomAfterDetach: false,
  }
}

/** Stop following synchronously on user intent, before the scroll position moves. */
export function detachScrollFollow(
  state: ScrollFollowState,
  isAtBottom: boolean,
): ScrollFollowState {
  if (state.userDetached && (isAtBottom || state.leftBottomAfterDetach)) {
    return state
  }
  return {
    userDetached: true,
    leftBottomAfterDetach: !isAtBottom,
  }
}

/**
 * An at-bottom callback fired before the user's wheel movement must not undo
 * their intent. Following resumes only after the viewport left the bottom and
 * then genuinely returned to it.
 */
export function observeScrollBottom(
  state: ScrollFollowState,
  isAtBottom: boolean,
): ScrollFollowState {
  if (!state.userDetached) return state
  if (!isAtBottom) {
    if (state.leftBottomAfterDetach) return state
    return { ...state, leftBottomAfterDetach: true }
  }
  return state.leftBottomAfterDetach ? createScrollFollowState() : state
}

export interface LiveTurnCursor {
  key?: string
}

export interface LiveTurnObservation {
  cursor: LiveTurnCursor
  anchorIndex?: number
}

function stableAssistantTurnKey(
  message: AgentConversationMessage,
): string | undefined {
  if (message.clientTurnId) return `client:${message.clientTurnId}`
  if (message.conversationId != null) return `conversation:${message.conversationId}`
  return undefined
}

function stableUserTurnKey(
  message: AgentConversationMessage,
): string | undefined {
  if (message.clientTurnId) return `user-client:${message.clientTurnId}`
  if (message.conversationId != null) return `user-conversation:${message.conversationId}`
  if (message.sentAt) return `user-sent:${message.sentAt}`
  return undefined
}

function latestLiveTurn(
  messages: AgentConversationMessage[],
): { key: string; anchorIndex: number } | undefined {
  for (let assistantIndex = messages.length - 1; assistantIndex >= 0; assistantIndex -= 1) {
    const message = messages[assistantIndex]
    if (message.role !== 'assistant') continue
    for (let userIndex = assistantIndex - 1; userIndex >= 0; userIndex -= 1) {
      const user = messages[userIndex]
      if (user.role !== 'user') continue
      const key = stableAssistantTurnKey(message) ?? stableUserTurnKey(user)
      return key ? { key, anchorIndex: userIndex } : undefined
    }
    return undefined
  }
  return undefined
}

/**
 * Records the initial/restored turn, then reports only a genuinely new stable
 * assistant turn. Streaming mutations retain the same key and do not re-pin.
 */
export function advanceLiveTurnCursor(
  previous: LiveTurnCursor | undefined,
  messages: AgentConversationMessage[],
): LiveTurnObservation {
  const current = latestLiveTurn(messages)
  const cursor = current ? { key: current.key } : {}
  if (!previous || !current || previous.key === current.key) return { cursor }
  return { cursor, anchorIndex: current.anchorIndex }
}
