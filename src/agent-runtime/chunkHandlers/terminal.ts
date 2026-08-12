import { presentAgentRunError } from '../agentErrorPresentation.ts'
import { EMPTY_RESPONSE_MESSAGE } from '../chatHistory.ts'
import type {
  AgentConversationMessage,
  AiTaskPlan,
} from '../contracts.ts'
import type {
  AgentChunkHandler,
  AgentChunkRuntimeContext,
  AgentRunOutcome,
  AgentTerminalSnapshot,
} from './types.ts'

export const MANUAL_ABORT_MESSAGE = '本轮对话已由你手动终止。'
export { EMPTY_RESPONSE_MESSAGE } from '../chatHistory.ts'

export const handleRunResultTerminal: AgentChunkHandler = (chunk, context) => {
  if (!chunk.done || !chunk.runResult) return
  const { status, errorCode } = chunk.runResult
  if (status === 'failed' || status === 'blocked') {
    return handleError({
      ...chunk,
      error: presentAgentRunError(status, errorCode),
    }, context)
  }
  if (status === 'canceled') {
    return handleDone({ ...chunk, aborted: true }, context)
  }
}

export const handleError: AgentChunkHandler = (chunk, context) => {
  if (!chunk.error) return
  return withTerminalSettlement(context, () => {
    const { acc, host } = context
    const durationMs = elapsedDuration(context)
    const commentaryBlocks = acc.commentaryBlocks ?? []
    const commentaryDurationsMs = acc.commentaryDurationsMs ?? []
    const response = acc.response || ''

    host.flushCommits()
    if (host.isVisible()) {
      replaceLastAssistant(context, (message) => {
        const hasInspectableProcess = Boolean(
          response.trim()
            || commentaryBlocks.length
            || (acc.toolCallSegments?.length ?? message.toolCallSegments?.length ?? 0)
            || acc.taskPlan
            || message.taskPlan
            || acc.delegations?.length
            || message.delegations?.length
            || message.subAgentActivities?.length
            || acc.contextCompaction
            || message.contextCompaction,
        )
        return {
          ...message,
          content: response,
          streamingContent: undefined,
          commentary: '',
          commentaryStartedAt: undefined,
          commentaryBlocks: commentaryBlocks.length
            ? commentaryBlocks
            : message.commentaryBlocks,
          commentaryDurationsMs: commentaryDurationsMs.length
            ? commentaryDurationsMs
            : message.commentaryDurationsMs,
          toolCallSegments: acc.toolCallSegments ?? message.toolCallSegments,
          taskPlan: acc.taskPlan ?? message.taskPlan,
          durationMs,
          turnStartedAt: undefined,
          toolCalling: false,
          errorReport: chunk.errorReport ?? message.errorReport,
          error: chunk.error,
          isError: !hasInspectableProcess,
        }
      })
    }

    acc.response = response
    return { outcome: 'failed', durationMs }
  })
}

export const handleDone: AgentChunkHandler = (chunk, context) => {
  if (!chunk.done) return
  return withTerminalSettlement(context, () => {
    const { acc, host } = context
    const durationMs = elapsedDuration(context)

    host.flushCommits()
    if (chunk.model) acc.model = chunk.model
    if (chunk.aborted) acc.taskPlan = markTaskPlanAborted(acc.taskPlan)

    const response = acc.response || ''
    const finalResponseExpected = chunk.finalResponseExpected !== false
    const emptyResponse = finalResponseExpected
      && !chunk.aborted
      && !response.trim()
    const commentaryBlocks = acc.commentaryBlocks ?? []
    const commentaryDurationsMs = acc.commentaryDurationsMs ?? []

    if (host.isVisible()) {
      replaceLastAssistant(context, (message) => ({
        ...message,
        content: response,
        streamingContent: undefined,
        model: acc.model || undefined,
        durationMs,
        turnStartedAt: undefined,
        commentary: '',
        commentaryStartedAt: undefined,
        commentaryBlocks: commentaryBlocks.length
          ? commentaryBlocks
          : message.commentaryBlocks,
        commentaryDurationsMs: commentaryDurationsMs.length
          ? commentaryDurationsMs
          : message.commentaryDurationsMs,
        toolCallSegments: acc.toolCallSegments ?? message.toolCallSegments,
        taskPlan: acc.taskPlan
          ?? (chunk.aborted ? markTaskPlanAborted(message.taskPlan) : message.taskPlan),
        longTaskId: acc.longTaskId ?? message.longTaskId,
        termination: chunk.aborted ? MANUAL_ABORT_MESSAGE : undefined,
        toolCalling: false,
        errorReport: emptyResponse
          ? chunk.errorReport ?? message.errorReport
          : message.errorReport,
        ...(emptyResponse
          ? { error: EMPTY_RESPONSE_MESSAGE, isError: false }
          : {}),
      }))
    }

    acc.response = response
    const outcome: AgentRunOutcome = !finalResponseExpected
      ? 'paused'
      : chunk.aborted
        ? 'canceled'
        : emptyResponse
          ? 'failed'
          : 'completed'
    return { outcome, durationMs }
  })
}

function withTerminalSettlement(
  context: AgentChunkRuntimeContext,
  project: () => { outcome: AgentRunOutcome; durationMs: number },
): true {
  const { acc } = context
  if (acc.terminalSettlement) return true
  acc.terminalSettlement = {
    phase: 'settling',
    runId: acc.agentRunId,
  }
  try {
    const { outcome, durationMs } = project()
    settle(context, outcome, durationMs)
    acc.terminalSettlement = {
      phase: 'settled',
      outcome,
      runId: acc.agentRunId,
    }
    return true
  } catch (error) {
    acc.terminalSettlement = undefined
    throw error
  }
}

function replaceLastAssistant(
  context: AgentChunkRuntimeContext,
  project: (message: AgentConversationMessage) => AgentConversationMessage,
): void {
  const messages = context.host.readMessages()
  const last = messages.at(-1)
  if (!last || last.role !== 'assistant') return
  const next = [...messages]
  next[next.length - 1] = project(last)
  context.host.replaceMessages(next)
}

function elapsedDuration(context: AgentChunkRuntimeContext): number {
  return Math.max(0, Math.round(context.now() - context.acc.turnStartedAt))
}

function settle(
  context: AgentChunkRuntimeContext,
  outcome: AgentRunOutcome,
  durationMs: number,
): void {
  context.host.setRunning(false)
  context.host.onSettled(outcome, terminalSnapshot(context, durationMs))
}

function terminalSnapshot(
  context: AgentChunkRuntimeContext,
  durationMs: number,
): AgentTerminalSnapshot {
  const { acc } = context
  return {
    sessionId: context.sessionId,
    userText: acc.userText,
    response: acc.response,
    model: acc.model || undefined,
    agentRunId: acc.conversationRunId ?? acc.agentRunId,
    longTaskId: acc.longTaskId,
    taskPlan: acc.taskPlan,
    commentaryBlocks: acc.commentaryBlocks ?? [],
    commentaryDurationsMs: acc.commentaryDurationsMs ?? [],
    toolCallSegments: acc.toolCallSegments ?? [],
    contextCompaction: acc.contextCompaction,
    contextBudget: acc.contextBudget,
    durationMs,
  }
}

function markTaskPlanAborted(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan
  return {
    ...plan,
    status: 'canceled',
    steps: plan.steps.map((step) => {
      if (step.status !== 'running') return step
      return {
        ...step,
        status: 'blocked' as const,
        resultSummary: step.resultSummary || MANUAL_ABORT_MESSAGE,
      }
    }),
  }
}
