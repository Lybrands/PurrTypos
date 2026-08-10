import type {
  ScreenplayAgentTask,
  ScreenplayAgentTaskStatus,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayConversationTurnStatus,
} from '../types'

type DisplayStatus = ScreenplayConversationTurnStatus | ScreenplayAgentTaskStatus

export interface ScreenplayConversationMessage {
  id: string
  turnId: string
  role: 'user' | 'assistant'
  content: string
  status: DisplayStatus
  taskId: string | null
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
  tasks: ScreenplayAgentTask[]
  messages: ScreenplayConversationMessage[]
}

export function stateFromScreenplayConversationSnapshot(
  snapshot: ScreenplayConversationSnapshot,
): ScreenplayConversationState {
  const tasksByTurn = new Map(snapshot.tasks.map((task) => [task.turnId, task]))
  return {
    projectId: String(snapshot.projectId),
    sessionId: snapshot.sessionId,
    cursor: snapshot.cursor,
    turns: snapshot.turns,
    tasks: snapshot.tasks,
    messages: snapshot.turns.flatMap((turn) => messagesFromTurn(
      turn,
      tasksByTurn.get(turn.id),
    )),
  }
}

export function isScreenplayTurnTerminal(
  turn: ScreenplayConversationTurn,
): boolean {
  return ['completed', 'failed', 'canceled'].includes(turn.status)
}

export function screenplayTurnReconciliationKey(
  turn: ScreenplayConversationTurn | undefined,
  task?: ScreenplayAgentTask,
): string | null {
  if (!turn || !isScreenplayTurnTerminal(turn)) return null
  if (task && !['completed', 'failed', 'canceled'].includes(task.status)) return null
  return [
    turn.id,
    task?.status || turn.status,
    task?.resultRevisionId || '',
  ].join(':')
}

export function modelRunIds(task: ScreenplayAgentTask | undefined): string[] {
  if (!task) return []
  return [...new Set([
    task.plannerRunId,
    ...task.units.map((unit) => String(unit.output.runId || '') || null),
  ].filter((value): value is string => Boolean(value)))]
}

function messagesFromTurn(
  turn: ScreenplayConversationTurn,
  task?: ScreenplayAgentTask,
): ScreenplayConversationMessage[] {
  const status = task?.status || turn.status
  const error = task?.error || turn.error
  const runId = modelRunIds(task).at(-1) || turn.plannerRunId
  const shared = {
    turnId: turn.id,
    status,
    taskId: task?.id || turn.taskId,
    runId,
    revisionId: task?.resultRevisionId || null,
    model: turn.runtimeProfile.model || null,
    error,
    createdAt: turn.createdAt || null,
  }
  return [
    {
      ...shared,
      id: `${turn.id}:user`,
      role: 'user' as const,
      content: turn.userContent,
      error: null,
    },
    {
      ...shared,
      id: `${turn.id}:assistant`,
      role: 'assistant' as const,
      content: assistantContent(turn, task),
    },
  ]
}

function assistantContent(
  turn: ScreenplayConversationTurn,
  task?: ScreenplayAgentTask,
): string {
  if (!task) return turn.assistantContent
  if (task.status === 'completed') {
    return '剧本任务已完成，候选稿已生成。请在下方预览并应用。'
  }
  if (task.status === 'failed') return task.error?.message || '剧本任务执行失败。'
  if (task.status === 'canceled') return '剧本任务已终止。'
  return ''
}
