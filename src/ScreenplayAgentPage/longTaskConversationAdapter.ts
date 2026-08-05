import type {
  AiAgentDelegation,
  AiLongTask,
  AiLongTaskConversationEvent,
  ScreenplayDocumentProposal,
} from '../types'
import type { AiStreamChunk, AiTaskPlan, ChatMessage } from '../agent-runtime'
import { parseLongTaskTime } from './longTaskProgress.ts'

export function isHostDispatchReceipt(content: string): boolean {
  const value = content.trim()
  if (!value) return false
  return value.includes('本轮将持续显示')
    || value.includes('执行进度与最终结果将在此处持续更新')
    || value.startsWith('已恢复原有')
    || value.startsWith('已关联到正在执行的同一项')
    || value.startsWith('已创建长篇正文任务')
    || /^长篇正文任务已开始，共/.test(value)
}

function taskSessionId(task: AiLongTask): number | null {
  const value = Number(task.metadata.sessionId)
  return Number.isInteger(value) && value > 0 ? value : null
}

export function longTaskBelongsToSession(
  task: AiLongTask,
  sessionId: number | null,
): boolean {
  const origin = taskSessionId(task)
  return origin == null || sessionId == null || origin === sessionId
}

function taskPlanStatus(task: AiLongTask): AiTaskPlan['status'] {
  if (task.status === 'pending' || task.status === 'running') return 'running'
  if (task.status === 'completed') return 'done'
  if (task.status === 'paused') return 'paused'
  if (task.status === 'canceled') return 'canceled'
  return 'failed'
}

function taskStepStatus(
  status: AiLongTask['units'][number]['status'],
): AiTaskPlan['steps'][number]['status'] {
  if (status === 'completed') return 'done'
  if (status === 'claimed' || status === 'running') return 'running'
  if (status === 'failed' || status === 'canceled') return 'failed'
  return 'pending'
}

function unitTitle(unit: AiLongTask['units'][number], index: number): string {
  const label = typeof unit.metadata.label === 'string'
    ? unit.metadata.label.trim()
    : ''
  if (label) return label
  if (unit.id === 'finalize') return '核验并生成可应用提案'
  const headings = Array.isArray(unit.metadata.sceneHeadings)
    ? unit.metadata.sceneHeadings
      .map((item) => String(item || '').trim())
      .filter(Boolean)
    : []
  if (headings.length) return `创作并校验：${headings.join('、')}`
  return `执行第 ${index + 1} 批`
}

/** Project the durable executor's real units into the ordinary task-plan UI. */
export function buildLongTaskTaskPlan(task: AiLongTask): AiTaskPlan {
  const metadataTitle = typeof task.metadata.taskTitle === 'string'
    ? task.metadata.taskTitle.trim()
    : ''
  const title = task.kind === 'screenplay_draft_generation'
    ? '长篇正文分批创作'
    : metadataTitle || '剧本阶段提案'
  const goal = typeof task.metadata.goal === 'string'
    ? task.metadata.goal.trim() || undefined
    : undefined
  return {
    title,
    goal,
    status: taskPlanStatus(task),
    steps: [...task.units]
      .sort((left, right) => left.position - right.position)
      .map((unit, index) => ({
        id: unit.id,
        title: unitTitle(unit, index),
        type: unit.id === 'finalize' ? 'review' : 'write',
        status: taskStepStatus(unit.status),
        executor: unit.id === 'finalize' ? 'tool' : 'model',
        riskLevel: 'write',
        error: unit.errorCode || undefined,
      })),
  }
}

export function resetLongTaskAssistantMessage(
  message: ChatMessage,
  task: AiLongTask,
): ChatMessage {
  const active = task.status === 'pending' || task.status === 'running'
  const startedAt = parseLongTaskTime(task.createTime)
  const finishedAt = active ? Date.now() : parseLongTaskTime(task.updateTime)
  const elapsedMs = startedAt != null
    ? Math.max(0, (finishedAt ?? Date.now()) - startedAt)
    : undefined
  return {
    ...message,
    // The continuation is replayed from child Run events. Starting from an
    // empty answer makes reconnect deterministic and prevents a persisted
    // final answer from being appended to itself.
    content: '',
    thinking: '',
    thinkingBlocks: undefined,
    thinkingDurationsMs: undefined,
    toolCallSegments: undefined,
    contentAfterToolCalls: undefined,
    toolCalling: false,
    delegations: undefined,
    subAgentActivities: undefined,
    error: undefined,
    isError: false,
    termination: undefined,
    turnStartedAt: active && elapsedMs != null
      ? performance.now() - elapsedMs
      : undefined,
    durationMs: active ? undefined : elapsedMs,
    taskPlan: buildLongTaskTaskPlan(task),
  }
}

export function applyLongTaskProgress(
  task: AiLongTask,
  event: Extract<AiLongTaskConversationEvent, { type: 'task.progress' }>,
): AiLongTask {
  if (task.id !== event.taskId) return task
  const progressByUnitId = new Map(
    event.units.map((unit) => [unit.id, unit]),
  )
  return {
    ...task,
    status: event.status,
    revision: event.revision,
    totalUnits: event.totalUnits,
    completedUnits: event.completedUnits,
    failedUnits: event.failedUnits,
    updateTime: event.updateTime,
    units: task.units.map((unit) => {
      const progress = progressByUnitId.get(unit.id)
      return progress ? { ...unit, ...progress } : unit
    }),
  }
}

export function resolveProposalSourceRunId(
  task: AiLongTask | null | undefined,
  visibleRunId: string,
): string | undefined {
  return task?.parentRunId?.trim() || visibleRunId.trim() || undefined
}

/**
 * Locate the proposal produced by a durable task without depending on a
 * hard-coded unit id. Planner-generated tasks may use different unit names;
 * the latest completed unit carrying a valid proposal is authoritative.
 */
export function extractLongTaskProposal(
  task: AiLongTask | null | undefined,
): ScreenplayDocumentProposal | null {
  if (!task) return null
  const units = [...task.units].sort((left, right) => right.position - left.position)
  for (const unit of units) {
    if (unit.status !== 'completed') continue
    const value = unit.metadata.proposal
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const proposal = value as Record<string, unknown>
    if (
      typeof proposal.kind !== 'string'
      || typeof proposal.title !== 'string'
      || !proposal.contentJson
      || typeof proposal.contentJson !== 'object'
      || Array.isArray(proposal.contentJson)
      || typeof proposal.contentText !== 'string'
      || !Array.isArray(proposal.derivedFromIds)
    ) {
      continue
    }
    return proposal as unknown as ScreenplayDocumentProposal
  }
  return null
}

export type LongTaskConversationAdapter = {
  toChunks: (event: AiLongTaskConversationEvent) => AiStreamChunk[]
  lastError: () => string
}

/**
 * Narrow transport adapter only. It converts durable child-Run envelopes to
 * the ordinary Agent chunk contract; all message animation and accumulation
 * remains owned by dispatchChunk.
 */
export function createLongTaskConversationAdapter(
  initialResponse = '',
  task?: AiLongTask | null,
): LongTaskConversationAdapter {
  const seen = new Set<string>()
  void initialResponse
  let terminalError = ''

  const parentRunId = task?.parentRunId?.trim() || ''
  const titleByUnitId = new Map(
    (task?.units ?? []).map((unit, index) => [unit.id, unitTitle(unit, index)]),
  )
  const delegationId = (
    event: { taskId: string; unitId: string; attempt: number },
  ) => `longtask:${event.taskId}:${event.unitId}:${Math.max(1, event.attempt)}`
  const delegation = (
    event: {
      taskId: string;
      unitId: string;
      attempt: number;
      runId: string;
      title?: string;
    },
    status: AiAgentDelegation['status'],
  ): AiAgentDelegation & { runId: string } => {
    const rootRunId = parentRunId || event.runId
    const title = event.title || titleByUnitId.get(event.unitId) || event.unitId
    return {
      runId: rootRunId,
      delegationId: delegationId(event),
      parentRunId: rootRunId,
      rootRunId,
      childRunId: event.runId || null,
      agentRole: 'screenplay_writer',
      agentTitle: title,
      objective: title,
      status,
      required: true,
      priority: 0,
      resultSummary: null,
      error: null,
    }
  }
  const childChunk = (
    event: {
      taskId: string;
      unitId: string;
      attempt: number;
      runId: string;
      title?: string;
    },
    chunk: AiStreamChunk,
  ): AiStreamChunk => {
    const rootRunId = parentRunId || event.runId
    const title = event.title || titleByUnitId.get(event.unitId) || event.unitId
    return {
      agentSubRunEvent: {
        runId: rootRunId,
        parentRunId: rootRunId,
        rootRunId,
        delegationId: delegationId(event),
        childRunId: event.runId,
        agentRole: 'screenplay_writer',
        agentTitle: title,
        objective: title,
        chunk: chunk as Record<string, unknown>,
      },
    }
  }

  const eventKey = (event: Exclude<
    AiLongTaskConversationEvent,
    { type: 'task.progress' | 'task.terminal' | 'stream.error' }
  >): string | null => {
    const cursor = Number(event.cursor || 0)
    if (cursor <= 0) return null
    return [
      event.runId,
      cursor,
      event.type,
      event.toolCallId || '',
    ].join(':')
  }

  return {
    lastError: () => terminalError,
    toChunks(event) {
      if (event.type === 'stream.error') return []
      if (event.type === 'task.progress') {
        return event.units.flatMap((unit) => {
          if (!unit.runId) return []
          const status: AiAgentDelegation['status'] = unit.status === 'completed'
            ? 'done'
            : unit.status === 'failed'
              ? 'failed'
              : unit.status === 'canceled'
                ? 'canceled'
                : unit.status === 'claimed'
                  ? 'claimed'
                  : unit.status === 'running'
                    ? 'running'
                    : 'queued'
          return [{
            agentDelegationUpdated: delegation({
              taskId: event.taskId,
              unitId: unit.id,
              attempt: unit.attempt,
              runId: unit.runId,
            }, status),
          }]
        })
      }
      if (event.type === 'task.terminal') {
        if (event.status === 'paused') return []
        if (event.status === 'failed') {
          return [{ error: terminalError || '长任务执行失败，已保留此前的执行过程。' }]
        }
        return [
          ...(event.status === 'completed' && event.finalResponse?.trim()
            ? [{ delta: event.finalResponse.trim() }]
            : []),
          ...(event.status === 'completed' && event.proposal
            ? [{ proposedScreenplayDocument: event.proposal }]
            : []),
          {
            done: true,
            aborted: event.status === 'canceled',
          },
        ]
      }

      const key = eventKey(event)
      if (key && seen.has(key)) return []
      if (key) seen.add(key)

      switch (event.type) {
        case 'turn.started':
          return [
            {
              agentDelegationCreated: delegation(event, 'running'),
            },
            childChunk(event, {
              agentRunStarted: {
                runId: event.runId,
                status: 'running',
              },
            }),
          ]
        case 'turn.thinking.delta':
          return event.delta
            ? [childChunk(event, { thinkingDelta: event.delta })]
            : []
        case 'turn.thinking.snapshot':
          return [childChunk(event, { thinkingSnapshot: event.content || '' })]
        case 'turn.chunk': {
          const chunk = event.chunk as unknown as AiStreamChunk | undefined
          if (!chunk) return []
          const terminal = chunk.agentRunFailed || chunk.agentRunBlocked
          if (terminal) {
            terminalError = 'error' in terminal && terminal.error
              ? terminal.error
              : terminalError
          }
          return [childChunk(event, chunk)]
        }
        case 'turn.model.call':
          return event.modelInvocation
            ? [childChunk(event, { modelInvocation: event.modelInvocation })]
            : []
        case 'turn.context.budgeted':
        case 'turn.context.usage':
          return event.contextBudget
            ? [childChunk(event, { contextBudget: event.contextBudget })]
            : []
        case 'turn.tool.started':
          if (!event.toolCalls?.length) return []
          return [childChunk(event, {
            toolCalls: event.toolCalls.map((call) => ({
              id: call.id,
              type: 'function',
              displayNames: call.displayNames,
              function: {
                name: call.name || 'agentTool',
                arguments: call.argumentsJson,
              },
            })),
            toolCallsInProgress: event.inProgress ?? true,
            partialContent: event.partialContent || '',
            partialThinking: event.partialThinking || '',
            model: event.model,
          })]
        case 'turn.tool.results':
          return event.toolResults?.length
            ? [childChunk(event, {
                toolResults: event.toolResults.map((result) => ({
                  tool_call_id: result.toolCallId,
                  name: result.toolName,
                  content: result.content,
                })),
              })]
            : []
        case 'turn.tool.completed':
          return [childChunk(event, {
            toolIndexCompleted: event.toolIndex ?? 0,
            toolCallId: event.toolCallId,
            toolName: event.toolName,
            toolOutcome: event.status || 'completed',
            toolErrorCode: event.errorCode,
            toolExceptionType: event.exceptionType,
            toolFromCache: event.fromCache,
          })]
        case 'turn.response': {
          const content = event.content || ''
          if (!content) return []
          return [childChunk(event, { delta: content })]
        }
        case 'turn.completed':
          if (event.status === 'failed') {
            terminalError = event.errorCode || terminalError
            return [
              childChunk(event, {
                agentRunFailed: {
                  runId: event.runId,
                  status: 'failed',
                  error: event.errorCode,
                },
              }),
              {
                agentDelegationUpdated: {
                  ...delegation(event, 'failed'),
                  error: event.errorCode || null,
                },
              },
            ]
          }
          return [
            childChunk(event, {
              agentRunCompleted: {
                runId: event.runId,
                status: 'done',
              },
            }),
            { agentDelegationUpdated: delegation(event, 'done') },
          ]
      }
    },
  }
}
