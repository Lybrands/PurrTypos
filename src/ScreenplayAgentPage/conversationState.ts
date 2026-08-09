import type {
  ScreenplayConversationEventPage,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayConversationTurnStatus,
} from '../types'

export interface ScreenplayConversationMessage {
  id: string
  turnId: string
  role: 'user' | 'assistant'
  content: string
  status: ScreenplayConversationTurnStatus
  route: 'read_only' | 'operation'
  operationId: string | null
  runId: string | null
  revisionId: string | null
  model: string | null
  error: { code?: string; message?: string } | null
  createdAt: string | null
}

export interface ScreenplayConversationState {
  projectId: string
  sessionId: number
  cursor: number
  turns: ScreenplayConversationTurn[]
  messages: ScreenplayConversationMessage[]
}

export function stateFromScreenplayConversationSnapshot(
  snapshot: ScreenplayConversationSnapshot,
): ScreenplayConversationState {
  return {
    projectId: String(snapshot.projectId),
    sessionId: snapshot.sessionId,
    cursor: snapshot.cursor,
    turns: snapshot.turns,
    messages: snapshot.turns.flatMap(messagesFromTurn),
  }
}

export function conversationPageRequiresSnapshot(
  state: ScreenplayConversationState,
  page: ScreenplayConversationEventPage,
): boolean {
  return page.events.some((event) => event.cursor > state.cursor)
}

export function isScreenplayTurnTerminal(
  turn: ScreenplayConversationTurn,
): boolean {
  return ['completed', 'failed', 'canceled'].includes(turn.status)
}

function messagesFromTurn(
  turn: ScreenplayConversationTurn,
): ScreenplayConversationMessage[] {
  const shared = {
    turnId: turn.id,
    status: turn.status,
    route: turn.route,
    operationId: turn.operationId,
    runId: turn.runId,
    revisionId: turn.revisionId,
    model: turn.runtimeProfile.model || null,
    error: turn.error,
    createdAt: turn.createdAt || null,
  }
  return [
    {
      ...shared,
      id: `${turn.id}:user`,
      role: 'user',
      content: turn.userContent,
      error: null,
    },
    {
      ...shared,
      id: `${turn.id}:assistant`,
      role: 'assistant',
      content: turn.assistantContent,
    },
  ]
}
