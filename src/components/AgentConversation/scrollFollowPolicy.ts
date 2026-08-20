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

export function agentConversationMessageKey(
  index: number,
  message: AgentConversationMessage,
): string {
  if (message.clientTurnId) {
    return `client:${message.clientTurnId}:${message.role}`
  }
  if (message.conversationId != null) {
    return `conversation:${message.conversationId}:${message.role}`
  }
  if (message.agentRunId) return `run:${message.agentRunId}:${message.role}`
  return `message:${index}:${message.role}`
}

function stableUserTurnKey(
  message: AgentConversationMessage,
): string | undefined {
  if (message.clientTurnId) return `user-client:${message.clientTurnId}`
  if (message.conversationId != null) return `user-conversation:${message.conversationId}`
  if (message.sentAt) return `user-sent:${message.sentAt}`
  return undefined
}

function latestStableUserTurn(
  messages: AgentConversationMessage[],
): { key: string; anchorIndex: number } | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'user') continue
    const key = stableUserTurnKey(message)
    return key ? { key, anchorIndex: index } : undefined
  }
  return undefined
}

/**
 * Records the initial/restored turn, then reports a newly appended stable user
 * turn immediately. Assistant streaming retains the user key and does not re-pin.
 */
export function advanceLiveTurnCursor(
  previous: LiveTurnCursor | undefined,
  messages: AgentConversationMessage[],
): LiveTurnObservation {
  const current = latestStableUserTurn(messages)
  const cursor = current ? { key: current.key } : {}
  if (!previous || !current || previous.key === current.key) return { cursor }
  return { cursor, anchorIndex: current.anchorIndex }
}
