import type { AgentConversationMessage } from '../agent-runtime/contracts.ts'
import type { AiAgentDelegation, AiAgentRunSnapshot, AiModelConfig, NovelAnalysisRun, NovelAnalysisStreamPage } from '../types.ts'
import { AgentChunkReplay } from '../agent-runtime/chunkReplay.ts'
import type { AiStreamChunk } from '../agent-runtime/chunkHandlers/types.ts'
import {
  loadCompleteAgentRunSnapshot,
  replayAgentRunSnapshotAsync,
} from '../agent-runtime/runSnapshotHydration.ts'
import {
  backendTimestampMs,
  buildNovelAnalysisTaskPlan,
  buildNovelAnalysisTiming,
} from './analysisTaskPlan.ts'

const historyModel: AiModelConfig = {
  id: '', name: '', apiKey: '', baseUrl: '', supportsThinking: false, thinkingOnly: false,
}

const ANALYSIS_ARTIFACT_PREFIXES = [
  'novel-analysis://',
] as const

export function novelAnalysisArtifactId(reference: string): string {
  const value = String(reference || '').trim()
  const prefix = ANALYSIS_ARTIFACT_PREFIXES.find(item => value.startsWith(item))
  if (prefix) return value.slice(prefix.length)
  return value.includes('://') ? '' : value
}

/**
 * Load every persisted page before a saved analysis is projected back into
 * the conversation. The analysis event stream then takes over for live
 * updates, but it must not be mistaken for a complete history after its first
 * page arrives.
 */
export async function hydrateNovelAnalysisHistory(input: {
  run: NovelAnalysisRun
  model: AiModelConfig
  getRunSnapshot(input: { runId: string; after?: number; limit?: number }): Promise<{
    success: boolean
    data?: AiAgentRunSnapshot
    error?: string
  }>
  isCurrent(): boolean
}): Promise<{ runId: string; message: AgentConversationMessage } | undefined> {
  const runIds = [...new Set([
    input.run.runId,
    ...(input.run.relatedRuns ?? []).map((related) => related.runId),
  ].filter(Boolean))]
  const snapshots: (AiAgentRunSnapshot | undefined)[] = new Array(runIds.length)
  let next = 0
  await Promise.all(Array.from({ length: Math.min(4, runIds.length) }, async () => {
    while (input.isCurrent()) {
      const index = next++
      if (index >= runIds.length) break
      snapshots[index] = await loadCompleteAgentRunSnapshot(runIds[index], {
        getRunSnapshot: input.getRunSnapshot, isCurrent: input.isCurrent,
      })
    }
  }))
  const [snapshot, ...relatedSnapshots] = snapshots
  if (!snapshot || !input.isCurrent()) return undefined
  return {
    runId: input.run.runId,
    message: await replayAgentRunSnapshotAsync({
      snapshot,
      relatedSnapshots: relatedSnapshots.filter((item): item is AiAgentRunSnapshot => Boolean(item)),
      relatedRunRoles: Object.fromEntries((input.run.relatedRuns ?? []).map((item) => [
        item.runId,
        item.role === 'previous_root' ? 'final_response' : 'related',
      ])),
      prompt: input.run.prompt || '',
      turnId: `novel-analysis:${input.run.commandId || input.run.runId}`,
      model: input.model.name,
    }, input.isCurrent),
  }
}

export class NovelAnalysisConversationStream {
  private runs: NovelAnalysisRun[] = []
  private streams = new Map<string, NovelAnalysisRunStream>()
  private history: Record<string, AgentConversationMessage> = {}
  private pendingEvents = new Map<number, NovelAnalysisStreamPage['chunks'][number]>()

  private targets(page: NovelAnalysisStreamPage) {
    if (page.runs) this.runs = page.runs
    for (const event of page.chunks) this.pendingEvents.set(event.cursor, event)
    return this.runs.map(run => {
      let stream = this.streams.get(run.runId)
      if (!stream) { stream = new NovelAnalysisRunStream(); this.streams.set(run.runId, stream) }
      const ids = new Set([run.runId, ...(run.relatedRuns ?? []).map(item => item.runId)])
      const chunks = [...this.pendingEvents.values()].filter(event => ids.has(event.runId))
      for (const event of chunks) this.pendingEvents.delete(event.cursor)
      return { stream, page: { ...page, runs: [run], chunks } }
    })
  }

  private snapshot() {
    const latest = this.runs[0]
    return latest && this.history[latest.runId]
      ? { runId: latest.runId, message: this.history[latest.runId], history: { ...this.history } } : undefined
  }

  apply(page: NovelAnalysisStreamPage, cfg: AiModelConfig = historyModel,
    onChunk?: (value: { runId: string; message: AgentConversationMessage; history: Record<string, AgentConversationMessage> }) => void) {
    for (const target of this.targets(page)) {
      const result = target.stream.apply(target.page, cfg, value => {
        this.history[value.runId] = value.message
        const snapshot = this.snapshot()
        if (snapshot) onChunk?.(snapshot)
      })
      if (result) this.history[result.runId] = result.message
    }
    return this.snapshot()
  }

  async applyAsync(page: NovelAnalysisStreamPage, cfg: AiModelConfig | undefined,
    onChunk: (value: { runId: string; message: AgentConversationMessage; history: Record<string, AgentConversationMessage> }) => void,
    isCurrent: () => boolean) {
    for (const target of this.targets(page)) {
      if (!isCurrent()) return undefined
      const result = await target.stream.applyAsync(target.page, cfg, value => {
        this.history[value.runId] = value.message
        const snapshot = this.snapshot()
        if (snapshot) onChunk(snapshot)
      }, isCurrent)
      if (result) this.history[result.runId] = result.message
    }
    const snapshot = this.snapshot()
    if (snapshot && isCurrent()) onChunk(snapshot)
    return snapshot
  }
}

/** One revision subscription; dynamic unit membership can catch up from memory. */
class NovelAnalysisRunStream {
  private events = new Map<number, NovelAnalysisStreamPage['chunks'][number]>()
  private replay = new AgentChunkReplay()
  private members = new Set<string>()
  private rootRunId = ''
  private run?: NovelAnalysisRun

  apply(...args: Parameters<NovelAnalysisRunStream['steps']>) {
    const iterator = this.steps(...args)
    let result = iterator.next()
    while (!result.done) result = iterator.next()
    return result.value
  }

  async applyAsync(page: NovelAnalysisStreamPage, cfg: AiModelConfig | undefined,
    onChunk: (value: {runId: string; message: AgentConversationMessage}) => void,
    isCurrent: () => boolean) {
    let pending: {runId: string; message: AgentConversationMessage} | undefined
    const iterator = this.steps(page, cfg, value => { pending = value })
    let result = iterator.next()
    while (!result.done) {
      if (isCurrent() && pending) { onChunk(pending); pending = undefined }
      await new Promise<void>(resolve => setTimeout(resolve, 0))
      if (!isCurrent()) { iterator.return(undefined); return undefined }
      result = iterator.next()
    }
    if (isCurrent() && pending) onChunk(pending)
    return result.value
  }

  private *steps(
    page: NovelAnalysisStreamPage,
    cfg: AiModelConfig = historyModel,
    onChunk?: (value: { runId: string; message: AgentConversationMessage }) => void,
  ) {
    if (page.runs) this.run = page.runs[0]
    const fresh = page.chunks.filter(event => !this.events.has(event.cursor))
    for (const event of fresh) this.events.set(event.cursor, event)
    const run = this.run
    if (!run) return undefined
    const ids = new Set([run.runId, ...(run.relatedRuns ?? []).map(item => item.runId)])
    const rebuild = this.rootRunId !== run.runId || [...this.members].some(id => !ids.has(id))
    const added = new Set([...ids].filter(id => !this.members.has(id)))
    if (rebuild) this.replay.reset()
    this.members = ids
    this.rootRunId = run.runId
    const turnId = `novel-analysis:${run.commandId || run.runId}`
    const createdAt = backendTimestampMs(run.createTime)
    const freshCursors = new Set(fresh.map(event => event.cursor))
    const events = rebuild ? [...this.events.values()].sort((a, b) => a.cursor - b.cursor)
      : added.size === 0 ? fresh : [...this.events.values()].filter(event => added.has(event.runId) || freshCursors.has(event.cursor))
        .sort((a, b) => a.cursor - b.cursor)
    let sliceStarted = performance.now()
    for (const event of events) {
      if (!ids.has(event.runId)) continue
      this.replay.dispatch({
        turnId, rootRunId: run.runId, eventRunId: event.runId,
        runRole: event.runId === run.runId
          ? 'root'
          : run.relatedRuns?.find(item => item.runId === event.runId)?.role === 'previous_root'
            ? 'final_response'
            : 'related',
        sessionId: 0,
        userContent: run.prompt || '', model: cfg.name,
        turnStartedAt: performance.now() - (createdAt == null ? 0 : Math.max(0, Date.now() - createdAt)),
      }, event.chunk as AiStreamChunk, { cfg })
      const message = this.replay.assistant(turnId)
      if (message) onChunk?.({ runId: run.runId, message })
      if (performance.now() - sliceStarted >= 8) {
        yield
        sliceStarted = performance.now()
      }
    }
    return { runId: run.runId, message: this.replay.assistant(turnId) ?? {
      role: 'assistant' as const, content: '', agentRunId: run.runId,
    } }
  }
}

function analysisMayHaveError(run: NovelAnalysisRun) {
  if (run.partialCompletion) return false
  const workflowStatus = run.workflowStatus
  if (['queued', 'running'].includes(workflowStatus || '')) return false
  // A pause is an actionable workflow state, not an assistant-response error.
  if (workflowStatus === 'paused') return false
  if (workflowStatus === 'failed') return true
  // Workflow completion means its Units settled; it does not retroactively
  // erase a failed root public presentation from the same analysis turn.
  return run.runStatus === 'failed'
}

function analysisErrorMessage(run: NovelAnalysisRun) {
  if (!analysisMayHaveError(run)) return ''
  const code = run.error || (run.units || []).find((unit) => unit.status === 'failed' && unit.errorCode)?.errorCode
  if (!code) return ''
  if (code === 'planning_failed') return '模型未能生成符合来源范围和安全约束的分析计划，请调整分析重点后重试。'
  if (code === 'durable_task_scope_conflict') return '已有分析任务尚未结束，请恢复或取消当前任务。'
  if (code === 'upstream_stream_interrupted' || code === 'model_gateway_error') return '模型连接中断，本次分析已结束。请检查模型服务后，由你决定是否重新分析。'
  if (code === 'provider_authentication_failed') return '模型凭据无效，请检查模型设置后重试。'
  if (code === 'provider_bad_request') return '模型拒绝了分析请求，请更换兼容模型或检查模型设置。'
  if (code === 'provider_rate_limited') return '模型服务当前繁忙，本次分析已结束。请稍后由你决定是否重新分析。'
  if (code === 'provider_insufficient_balance') return '模型账户余额或额度不足，请处理后重试。'
  if (code === 'model_invocation_deadline_exceeded') return '模型单次分析超过当前时限，本次分析已结束。你可以重新分析或更换响应更快的模型。'
  if (code === 'model_invocation_failed') return '模型调用中断，本次分析已结束。请由你决定是否重新分析。'
  if (code === 'max_model_rounds') return '子 Agent 达到模型轮次上限，仍未提交最终结果。本次分析已结束，请由你决定如何处理。'
  if (code === 'novel_analysis_child_failed') return '子 Agent 未能完成任务，本次分析已结束，请由你决定如何处理。'
  if (code === 'model_reasoning_mode_conflict') return '模型请求的推理配置发生冲突，请检查模型调用链路。'
  if (code === 'user_paused_novel_analysis') return '任务由你暂停，恢复后会从未完成的步骤继续。'
  return `分析未完成：${code}`
}

export function buildNovelAnalysisMessages(
  run: NovelAnalysisRun,
  modelName: string,
  replayed?: AgentConversationMessage,
): AgentConversationMessage[] {
  const runtimeMessage = replayed?.agentRunId === run.runId ? replayed : undefined
  const followUp = run.interactionKind === 'follow_up'
  const mayHaveError = analysisMayHaveError(run)
  const error = analysisErrorMessage(run) || (mayHaveError ? runtimeMessage?.error : undefined)
  const projectedDelegations = novelAnalysisDelegations(run)
  const messages: AgentConversationMessage[] = []
  if (run.prompt && !run.automaticRecovery) {
    messages.push({
      role: 'user',
      content: run.prompt,
      sentAt: run.createTime || undefined,
      clientTurnId: run.commandId,
    })
  }
  messages.push({
    ...runtimeMessage,
    role: 'assistant',
    content: runtimeMessage?.content
      || (run.conversationStatus === 'finalized' ? run.finalResponse : '')
      || '',
    sentAt: run.updateTime || undefined,
    agentRunId: run.runId,
    longTaskId: run.taskId || undefined,
    model: runtimeMessage?.model || modelName,
    taskPlan: followUp ? runtimeMessage?.taskPlan : buildNovelAnalysisTaskPlan(run),
    delegations: mergeDelegations(runtimeMessage?.delegations, projectedDelegations),
    isError: Boolean(error || (mayHaveError && runtimeMessage?.isError)),
    error: error || undefined,
    ...buildNovelAnalysisTiming(run),
  })
  return messages
}

function novelAnalysisDelegations(run: NovelAnalysisRun): AiAgentDelegation[] {
  return (run.relatedRuns ?? [])
    .filter((item) => item.role === 'child')
    .map((item) => ({
      delegationId: `run:${item.runId}`,
      runId: item.runId,
      ...(item.agentId ? { agentId: item.agentId } : {}),
      ...(item.previousRunId ? { previousRunId: item.previousRunId } : {}),
      agentName: item.agentName || item.agentTitle || '子 Agent',
      agentTitle: item.agentTitle || null,
      objective: item.objective || '',
      ...(item.createTime ? { startedAt: item.createTime } : {}),
      ...(item.unitId ? { unitId: item.unitId } : {}),
      ...(item.attempt != null ? { attempt: item.attempt } : {}),
      status: delegationStatus(item.status),
      required: true,
      priority: 0,
    }))
}

function mergeDelegations(
  runtime: AiAgentDelegation[] | undefined,
  projected: AiAgentDelegation[],
): AiAgentDelegation[] | undefined {
  if (!runtime?.length) return projected.length ? projected : undefined
  const byRunId = new Map(runtime.map((item) => [item.runId, item]))
  for (const item of projected) {
    const current = byRunId.get(item.runId)
    byRunId.set(item.runId, current ? { ...item, ...current, status: item.status } : item)
  }
  return [...byRunId.values()]
}

function delegationStatus(status: string): AiAgentDelegation['status'] {
  if (status === 'pending' || status === 'queued') return 'queued'
  if (status === 'claimed') return 'claimed'
  if (status === 'running' || status === 'waiting') return 'running'
  if (status === 'done' || status === 'completed') return 'done'
  if (status === 'canceled') return 'canceled'
  return 'failed'
}
