import type {
  ScreenplayAgentTask,
  ScreenplayAgentTaskStatus,
  ScreenplayDocumentKind,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayConversationTurnStatus,
  ScreenplayV2DeliverableRole,
  ScreenplayV2Workspace,
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

export interface ScreenplayTurnArtifact {
  turnId: string
  taskId: string
  revisionId: string
  role: ScreenplayV2DeliverableRole
  revisionNo: number | null
  title: string
  kind: ScreenplayDocumentKind | null
  status: 'current' | 'historical' | 'candidate'
  sourceRunId: string | null
}

const DOCUMENT_KINDS = new Set<ScreenplayDocumentKind>([
  'source_analysis',
  'creative_brief',
  'beat_sheet',
  'episode_outline',
  'scene_list',
  'scene_draft',
  'review',
])

const ROLE_LABELS: Record<ScreenplayV2DeliverableRole, string> = {
  sourceAnalysis: '原作分析',
  creativeBrief: '创作简报',
  structure: '结构设计',
  sceneList: '场景规划',
  screenplayDraft: '剧本正文',
  review: '审阅修订',
}

function artifactStatus(
  task: ScreenplayAgentTask,
  workspace: Pick<ScreenplayV2Workspace, 'workflow' | 'candidates'> | null,
): ScreenplayTurnArtifact['status'] {
  const revisionId = task.resultRevisionId
  if (!revisionId) return 'candidate'
  if (workspace?.workflow.heads[task.targetRole]?.id === revisionId) {
    return 'current'
  }
  if (workspace?.candidates.some((revision) => revision.id === revisionId)) {
    return 'candidate'
  }
  if (workspace && task.resultRevision?.status === 'current') {
    return 'historical'
  }
  return task.resultRevision?.status ?? 'candidate'
}

export function screenplayTurnArtifacts(
  tasks: ScreenplayAgentTask[],
  workspace: Pick<ScreenplayV2Workspace, 'workflow' | 'candidates'> | null,
): Map<string, ScreenplayTurnArtifact> {
  const artifacts = new Map<string, ScreenplayTurnArtifact>()
  for (const task of tasks) {
    const revisionId = task.resultRevisionId
    if (task.status !== 'completed' || !revisionId) continue
    const revision = task.resultRevision?.id === revisionId
      && task.resultRevision.role === task.targetRole
        ? task.resultRevision
        : null
    const proposalKind = String(revision?.summary.proposalKind || '')
    const kind = DOCUMENT_KINDS.has(proposalKind as ScreenplayDocumentKind)
      ? proposalKind as ScreenplayDocumentKind
      : null
    artifacts.set(task.turnId, {
      turnId: task.turnId,
      taskId: task.id,
      revisionId,
      role: task.targetRole,
      revisionNo: revision?.revisionNo ?? null,
      title: String(revision?.summary.title || `${ROLE_LABELS[task.targetRole]}候选稿`),
      kind,
      status: artifactStatus(task, workspace),
      sourceRunId: revision?.finalizingRunId
        || revision?.rootRunId
        || modelRunIds(task).at(-1)
        || task.plannerRunId,
    })
  }
  return artifacts
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
  return ['paused', 'completed', 'failed', 'canceled'].includes(turn.status)
}

export function screenplayTurnReconciliationKey(
  turn: ScreenplayConversationTurn | undefined,
  task?: ScreenplayAgentTask,
): string | null {
  if (!turn || !isScreenplayTurnTerminal(turn)) return null
  if (turn.status === 'paused') return null
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
    return turn.assistantContent
  }
  if (task.status === 'failed') return task.error?.message || '剧本任务执行失败。'
  if (task.status === 'canceled') return '剧本任务已终止。'
  return ''
}
