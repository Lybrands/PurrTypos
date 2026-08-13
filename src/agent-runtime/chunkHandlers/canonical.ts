import type {
  AiAgentDelegation,
  AiContextCompactionState,
} from '../../types.ts'
import {
  initialCanonicalOutputState,
  isCanonicalOutputEvent,
  reduceCanonicalOutput,
} from '../canonicalOutput.ts'
import { projectContextBudget } from '../contextBudgetProjection.ts'
import type {
  AgentConversationMessage,
  AiSubAgentActivity,
  AiTaskPlan,
  AiTaskStep,
} from '../contracts.ts'
import type { AgentChunkRuntimeContext, AiStreamChunk } from './types.ts'

export function handleCanonicalOutput(
  chunk: AiStreamChunk,
  ctx: AgentChunkRuntimeContext,
): boolean {
  const transportStreamId = chunk.streamId
  if (!isCanonicalOutputEvent(chunk)) return false

  const currentState = ctx.acc.canonicalOutput ?? initialCanonicalOutputState()
  const acceptedCanonicalEvent = chunk.sequence
    > (currentState.lastSequenceByRun[chunk.runId] ?? 0)
  const currentRunId = currentState.runId ?? ctx.acc.agentRunId
  const foreignRun = Boolean(currentRunId && chunk.runId !== currentRunId)
  const authoritativeRootRunId = ctx.acc.conversationRunId
  const violatesRootBinding = Boolean(
    authoritativeRootRunId && chunk.runId !== authoritativeRootRunId
  )
  const terminalSettlement = ctx.acc.terminalSettlement
  const resumesPausedRun = Boolean(
    acceptedCanonicalEvent
    && chunk.visibility === 'public'
    && foreignRun
    && chunk.kind === 'run.lifecycle'
    && chunk.payload.status === 'running'
    && chunk.runId === authoritativeRootRunId
    && terminalSettlement
    && terminalSettlement.phase !== 'projecting'
    && terminalSettlement.outcome === 'paused'
    && chunk.runId !== terminalSettlement.runId
  )
  if (
    (ctx.turnId && transportStreamId && transportStreamId !== ctx.turnId)
    || violatesRootBinding
    || (foreignRun && !resumesPausedRun)
  ) return true
  if (resumesPausedRun) {
    ctx.acc.terminalSettlement = undefined
    ctx.acc.taskPlan = undefined
  }

  const state = reduceCanonicalOutput(
    resumesPausedRun
      ? {
          ...currentState,
          runId: chunk.runId,
          runStatus: null,
          runTerminal: false,
        }
      : currentState,
    chunk,
  )

  ctx.acc.canonicalOutput = state
  ctx.acc.agentRunId = state.runId ?? chunk.runId
  ctx.acc.response = state.finalText
  ctx.acc.commentary = ''
  ctx.acc.commentaryBlocks = state.commentaryBlocks
    .filter((block) => !block.aborted)
    .map((block) => block.text.trim())
    .filter(Boolean)
  ctx.acc.commentaryDurationsMs = undefined
  if (acceptedCanonicalEvent && chunk.visibility === 'public') {
    if (chunk.kind === 'runtime.event') {
      applyCanonicalRuntimeView(ctx, state.latestRuntimeEvent, chunk.runId)
    } else if (chunk.kind === 'run.lifecycle') {
      applyRunLifecycleView(ctx, chunk.runId, chunk.payload.status)
    }
  }
  ctx.acc.delegations = state.delegationOrder.map((delegationId) =>
    delegationView(state.delegations[delegationId]),
  )
  ctx.acc.subAgentActivities = state.delegationOrder.map((delegationId) =>
    delegationActivity(state.delegations[delegationId]),
  )

  if (ctx.host.isVisible()) {
    ctx.host.scheduleCommit((previous) => {
      const next = [...previous]
      const last = next.at(-1)
      if (!last || last.role !== 'assistant') return previous
      const message = last as AgentConversationMessage
      const finalCommitted = state.finalStreamStatus === 'committed'
      next[next.length - 1] = {
        ...message,
        agentRunId: state.runId ?? chunk.runId,
        canonicalOutput: state,
        content: finalCommitted ? state.finalText : message.content,
        streamingContent: !finalCommitted && state.finalText
          ? state.finalText
          : undefined,
        commentary: '',
        commentaryStartedAt: undefined,
        commentaryBlocks: ctx.acc.commentaryBlocks?.length
          ? ctx.acc.commentaryBlocks
          : undefined,
        commentaryDurationsMs: undefined,
        taskPlan: resumesPausedRun
          ? undefined
          : ctx.acc.taskPlan ?? message.taskPlan,
        longTaskId: ctx.acc.longTaskId ?? message.longTaskId,
        delegations: ctx.acc.delegations,
        subAgentActivities: ctx.acc.subAgentActivities,
        toolApprovals: state.approvalOrder.map(
          (approvalId) => state.approvals[approvalId],
        ),
        contextBudget: ctx.acc.contextBudget ?? message.contextBudget,
        contextCompaction:
          ctx.acc.contextCompaction ?? message.contextCompaction,
        toolCalling: state.operationOrder.some(
          (operationId) => state.operations[operationId]?.status === 'running',
        ),
      }
      return next
    })
  }
  return true
}

function applyCanonicalRuntimeView(
  ctx: AgentChunkRuntimeContext,
  runtime: {
    eventType: string
    data: Record<string, unknown>
    sequence: number
  } | null,
  envelopeRunId?: string,
): void {
  if (!runtime) return
  const { eventType, data } = runtime
  const runId = envelopeRunId
    || stringValue(data.runId ?? data.run_id)
    || ctx.acc.agentRunId

  if (eventType === 'run.todos_updated') {
    const plan = normalizePlan(data, runId)
    if (plan && (!ctx.acc.taskPlan || ctx.acc.taskPlan.runId === plan.runId)) {
      ctx.acc.taskPlan = plan
    }
    return
  }
  if (eventType === 'run.todo_updated') {
    const stepId = stringValue(data.stepId ?? data.step_id)
    const step = normalizeStep(data.step)
    if (!stepId || !step || !ctx.acc.taskPlan || ctx.acc.taskPlan.runId !== runId) {
      return
    }
    ctx.acc.taskPlan = {
      ...ctx.acc.taskPlan,
      status: normalizePlanStatus(data.status ?? ctx.acc.taskPlan.status),
      steps: ctx.acc.taskPlan.steps.map((current) =>
        current.id === stepId ? { ...current, ...step } : current,
      ),
    }
    return
  }
  if (
    eventType === 'run.completed'
    || eventType === 'run.failed'
    || eventType === 'run.blocked'
    || eventType === 'run.canceled'
  ) {
    updateTaskPlanStatus(
      ctx,
      runId,
      eventType === 'run.completed'
        ? 'done'
        : eventType.slice('run.'.length) as AiTaskPlan['status'],
    )
    return
  }
  if (eventType === 'long_task.dispatched') {
    ctx.acc.longTaskId = stringValue(data.taskId ?? data.task_id)
      || ctx.acc.longTaskId
    return
  }
  if (eventType === 'long_task.progress') {
    ctx.acc.longTaskId = stringValue(data.taskId ?? data.task_id)
      || ctx.acc.longTaskId
    return
  }
  if (eventType === 'context.budgeted' || eventType === 'context.usage_recorded') {
    ctx.acc.contextBudget = projectContextBudget(
      ctx.acc.contextBudget,
      data,
      ctx.modelIdentity,
    )
    return
  }
  if (
    eventType === 'conversation.compaction.started'
    || eventType === 'conversation.compaction.completed'
  ) {
    ctx.acc.contextCompaction = contextCompactionView(data)
  }
}

function applyRunLifecycleView(
  ctx: AgentChunkRuntimeContext,
  runId: string,
  status: unknown,
): void {
  const planStatus = normalizePlanStatus(status)
  if (planStatus === 'running' || planStatus === 'planned') return
  updateTaskPlanStatus(ctx, runId, planStatus)
}

function updateTaskPlanStatus(
  ctx: AgentChunkRuntimeContext,
  runId: string | undefined,
  status: AiTaskPlan['status'],
): void {
  if (!ctx.acc.taskPlan || ctx.acc.taskPlan.runId !== runId) return
  ctx.acc.taskPlan = { ...ctx.acc.taskPlan, status }
}

function normalizePlan(
  value: Record<string, unknown>,
  fallbackRunId?: string,
): AiTaskPlan | null {
  if (!Array.isArray(value.steps)) return null
  return {
    runId: fallbackRunId || stringValue(value.runId ?? value.run_id),
    title: stringValue(value.title) || 'To-dos',
    goal: stringValue(value.goal) || undefined,
    status: normalizePlanStatus(value.status),
    steps: value.steps
      .map(normalizeStep)
      .filter((step): step is AiTaskStep => step != null),
  }
}

function normalizeStep(value: unknown): AiTaskStep | null {
  if (!isRecord(value)) return null
  if (value.protocol_private === true || value.protocolPrivate === true) {
    return null
  }
  const id = stringValue(value.id)
  const title = stringValue(value.title)
  if (!id || !title) return null
  return {
    id,
    title,
    description: stringValue(value.description) || undefined,
    type: normalizeStepType(value.type),
    status: normalizeStepStatus(value.status),
    executor: normalizeExecutor(value.executor),
    riskLevel: normalizeRisk(value.riskLevel ?? value.risk_level),
    suggestedTools: stringArray(value.suggestedTools ?? value.suggested_tools),
    planningCapability: stringValue(
      value.planningCapability ?? value.planning_capability,
    ) || undefined,
    protocolPrivate: false,
    agentRole: stringValue(value.agentRole ?? value.agent_role) || undefined,
    assignment: isRecord(value.assignment) ? value.assignment : undefined,
    dependsOn: Array.isArray(value.dependsOn ?? value.depends_on)
      ? stringArray(value.dependsOn ?? value.depends_on) ?? []
      : undefined,
    resultSummary: stringValue(value.resultSummary ?? value.result_summary) || undefined,
    error: stringValue(value.error) || undefined,
  }
}

function normalizePlanStatus(value: unknown): AiTaskPlan['status'] {
  const status = stringValue(value)
  return status === 'planned'
    || status === 'running'
    || status === 'paused'
    || status === 'done'
    || status === 'blocked'
    || status === 'failed'
    || status === 'canceled'
    ? status
    : 'running'
}

function normalizeStepType(value: unknown): AiTaskStep['type'] {
  const type = stringValue(value)
  return type === 'read'
    || type === 'analyze'
    || type === 'write'
    || type === 'review'
    || type === 'confirm'
    ? type
    : 'analyze'
}

function normalizeStepStatus(value: unknown): AiTaskStep['status'] {
  const status = stringValue(value)
  return status === 'pending'
    || status === 'running'
    || status === 'done'
    || status === 'blocked'
    || status === 'failed'
    ? status
    : 'pending'
}

function normalizeExecutor(value: unknown): AiTaskStep['executor'] {
  const executor = stringValue(value)
  return executor === 'model' || executor === 'tool' || executor === 'agent'
    ? executor
    : undefined
}

function normalizeRisk(value: unknown): AiTaskStep['riskLevel'] {
  const risk = stringValue(value)
  return risk === 'read' || risk === 'write' || risk === 'destructive'
    ? risk
    : undefined
}

function contextCompactionView(
  data: Record<string, unknown>,
): AiContextCompactionState {
  return data as unknown as AiContextCompactionState
}

function delegationView(
  value: {
    delegationId: string
    firstSequence: number
    parentRunId: string
    childRunId: string | null
    agentRole: string
    agentTitle: string | null
    objective: string
    status: string
    errorCode: string | null
  },
): AiAgentDelegation {
  return {
    delegationId: value.delegationId,
    parentRunId: value.parentRunId,
    rootRunId: value.parentRunId,
    childRunId: value.childRunId,
    agentRole: value.agentRole,
    agentTitle: value.agentTitle,
    objective: value.objective,
    unitId: null,
    attempt: null,
    status: value.status as AiAgentDelegation['status'],
    required: true,
    priority: 0,
    resultSummary: null,
    error: value.errorCode,
  }
}

function delegationActivity(
  value: Parameters<typeof delegationView>[0] & {
    output: import('../canonicalOutput.ts').CanonicalOutputState
  },
): AiSubAgentActivity {
  const committed = value.output.finalStreamStatus === 'committed'
  return {
    delegationId: value.delegationId,
    parentRunId: value.parentRunId,
    rootRunId: value.parentRunId,
    childRunId: value.childRunId,
    agentRole: value.agentRole,
    agentTitle: value.agentTitle,
    objective: value.objective,
    status: value.status as AiAgentDelegation['status'],
    message: {
      role: 'assistant',
      content: committed ? value.output.finalText : '',
      streamingContent: !committed && value.output.finalText
        ? value.output.finalText
        : undefined,
      agentRunId: value.childRunId ?? undefined,
      canonicalOutput: value.output,
      toolApprovals: value.output.approvalOrder.map(
        (approvalId) => value.output.approvals[approvalId],
      ),
      toolCalling: value.output.operationOrder.some(
        (operationId) =>
          value.output.operations[operationId]?.status === 'running',
      ),
    },
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  const result = value.filter((item): item is string => typeof item === 'string')
  return result.length ? result : undefined
}
