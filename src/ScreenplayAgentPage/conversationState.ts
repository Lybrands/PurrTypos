import type {
  ScreenplayAgentTask,
  ScreenplayAgentTaskStatus,
  ScreenplayDocumentKind,
  ScreenplayConversationSnapshot,
  ScreenplayConversationTurn,
  ScreenplayConversationTurnStatus,
  ScreenplayOperationProjection,
  ScreenplayOperationStatus,
  ScreenplayV2DeliverableRole,
  ScreenplayV2Workspace,
} from '../types'

type DisplayStatus = ScreenplayConversationTurnStatus
  | ScreenplayAgentTaskStatus
  | ScreenplayOperationStatus

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
  operations: ScreenplayOperationProjection[]
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
  task: ScreenplayAgentTask | undefined,
  operation: ScreenplayOperationProjection,
  workspace: Pick<ScreenplayV2Workspace, 'workflow' | 'candidates'> | null,
): ScreenplayTurnArtifact['status'] {
  const revisionId = operation.resultRevisionId
  if (!revisionId) return 'candidate'
  if (workspace?.workflow.heads[operation.targetRole]?.id === revisionId) {
    return 'current'
  }
  if (workspace?.candidates.some((revision) => revision.id === revisionId)) {
    return 'candidate'
  }
  if (workspace && task?.resultRevision?.status === 'current') {
    return 'historical'
  }
  return task?.resultRevision?.status ?? 'candidate'
}

export function screenplayTurnArtifacts(
  operations: ScreenplayOperationProjection[],
  tasks: ScreenplayAgentTask[],
  workspace: Pick<ScreenplayV2Workspace, 'workflow' | 'candidates'> | null,
): Map<string, ScreenplayTurnArtifact> {
  const artifacts = new Map<string, ScreenplayTurnArtifact>()
  const tasksById = new Map(tasks.map((task) => [task.id, task]))
  for (const operation of operations) {
    const revisionId = operation.resultRevisionId
    if (
      operation.status !== 'succeeded'
      || !revisionId
      || !operation.finalizationReceiptId
    ) continue
    const task = operation.taskId ? tasksById.get(operation.taskId) : undefined
    const hydratedRevision = operation.resultRevision || task?.resultRevision
    const revision = hydratedRevision?.id === revisionId
      && hydratedRevision.role === operation.targetRole
        ? hydratedRevision
        : null
    const proposalKind = String(revision?.summary.proposalKind || '')
    const kind = DOCUMENT_KINDS.has(proposalKind as ScreenplayDocumentKind)
      ? proposalKind as ScreenplayDocumentKind
      : null
    if (!operation.taskId) continue
    artifacts.set(operation.turnId, {
      turnId: operation.turnId,
      taskId: operation.taskId,
      revisionId,
      role: operation.targetRole,
      revisionNo: revision?.revisionNo ?? null,
      title: String(revision?.summary.title || `${ROLE_LABELS[operation.targetRole]}候选稿`),
      kind,
      status: artifactStatus(task, operation, workspace),
      sourceRunId: revision?.finalizingRunId
        || revision?.rootRunId
        || modelRunIds(task).at(-1)
        || task?.plannerRunId
        || null,
    })
  }
  return artifacts
}

export function stateFromScreenplayConversationSnapshot(
  snapshot: ScreenplayConversationSnapshot,
): ScreenplayConversationState {
  const tasksByTurn = new Map(snapshot.tasks.map((task) => [task.turnId, task]))
  const operationsByTurn = new Map(snapshot.operations.map((item) => [item.turnId, item]))
  return {
    projectId: String(snapshot.projectId),
    sessionId: snapshot.sessionId,
    cursor: snapshot.cursor,
    turns: snapshot.turns,
    tasks: snapshot.tasks,
    operations: snapshot.operations,
    messages: snapshot.turns.flatMap((turn) => messagesFromTurn(
      turn,
      operationsByTurn.get(turn.id),
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
  operation?: ScreenplayOperationProjection,
  task?: ScreenplayAgentTask,
): string | null {
  if (!turn || !operation || !isScreenplayTurnTerminal(turn)) return null
  if (
    operation.status !== 'succeeded'
    || !operation.resultRevisionId
    || !operation.finalizationReceiptId
  ) return null
  if (task?.status !== 'completed') return null
  return [
    turn.id,
    operation.status,
    operation.resultRevisionId,
    operation.finalizationReceiptId,
  ].join(':')
}

export function isScreenplayOperationCancellable(
  operation: ScreenplayOperationProjection | null | undefined,
): boolean {
  return Boolean(
    operation
    && ['queued', 'running', 'paused'].includes(operation.status)
    && !operation.cancelRequestedAt
    && !operation.cancelReceiptId,
  )
}

export function modelRunIds(task: ScreenplayAgentTask | undefined): string[] {
  if (!task) return []
  return [...new Set([
    task.plannerRunId,
    ...task.units.map((unit) => (
      String(unit.validationReceipt.runId || '') || null
    )),
  ].filter((value): value is string => Boolean(value)))]
}

function messagesFromTurn(
  turn: ScreenplayConversationTurn,
  operation?: ScreenplayOperationProjection,
  task?: ScreenplayAgentTask,
): ScreenplayConversationMessage[] {
  const status = operation?.status || task?.status || turn.status
  const error = operation?.error || task?.error || turn.error
  const runId = modelRunIds(task).at(-1) || turn.plannerRunId
  const shared = {
    turnId: turn.id,
    status,
    taskId: task?.id || turn.taskId,
    runId,
    revisionId: operation?.resultRevisionId || task?.resultRevisionId || null,
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
      content: assistantContent(turn, operation),
    },
  ]
}

function assistantContent(
  turn: ScreenplayConversationTurn,
  operation?: ScreenplayOperationProjection,
): string {
  if (!operation) return turn.status === 'completed' ? turn.assistantContent : ''
  return operation.status === 'succeeded'
    && Boolean(operation.resultRevisionId)
    && Boolean(operation.finalizationReceiptId)
    ? turn.assistantContent
    : ''
}
