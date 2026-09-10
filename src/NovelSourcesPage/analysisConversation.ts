import type { AgentConversationMessage } from '../agent-runtime/contracts.ts'
import type { AiAgentRunSnapshot, AiModelConfig, NovelAnalysisRun, NovelAnalysisStreamPage } from '../types.ts'
import { AgentChunkReplay } from '../agent-runtime/chunkReplay.ts'
import type { AiStreamChunk } from '../agent-runtime/chunkHandlers/types.ts'
import {
  loadCompleteAgentRunSnapshot,
  replayAgentRunSnapshotAsync,
} from '../agent-runtime/runSnapshotHydration.ts'
import { buildNovelAnalysisTaskPlan, buildNovelAnalysisTiming } from './analysisTaskPlan.ts'

const historyModel: AiModelConfig = {
  id: '', name: '', apiKey: '', baseUrl: '', supportsThinking: false, thinkingOnly: false,
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
    const createdAt = Date.parse(run.createTime || '')
    const freshCursors = new Set(fresh.map(event => event.cursor))
    const events = rebuild ? [...this.events.values()].sort((a, b) => a.cursor - b.cursor)
      : added.size === 0 ? fresh : [...this.events.values()].filter(event => added.has(event.runId) || freshCursors.has(event.cursor))
        .sort((a, b) => a.cursor - b.cursor)
    let sliceStarted = performance.now()
    for (const event of events) {
      if (!ids.has(event.runId)) continue
      this.replay.dispatch({
        turnId, rootRunId: run.runId, eventRunId: event.runId,
        runRole: event.runId === run.runId ? 'root' : 'unit', sessionId: 0,
        userContent: run.prompt || '', model: cfg.name,
        turnStartedAt: performance.now() - (Number.isFinite(createdAt) ? Math.max(0, Date.now() - createdAt) : 0),
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
  if (['pending', 'running', 'claimed'].includes(run.taskStatus || '')) return false
  return [run.taskStatus, run.runStatus].some(status => ['failed', 'blocked', 'paused'].includes(status || ''))
}

function analysisErrorMessage(run: NovelAnalysisRun) {
  if (!analysisMayHaveError(run)) return ''
  const code = run.error || (run.units || []).find((unit) => unit.status === 'failed' && unit.errorCode)?.errorCode
  if (!code) return ''
  if (code === 'planning_failed') return '模型未能生成符合来源范围和安全约束的分析计划，请调整分析重点后重试。'
  if (code === 'durable_task_scope_conflict') return '已有分析任务尚未结束，请恢复或取消当前任务。'
  if (code === 'upstream_stream_interrupted' || code === 'model_gateway_error') return '模型连接中断；系统会自动重试，仍未恢复时可手动重试。'
  if (code === 'provider_authentication_failed') return '模型凭据无效，请检查模型设置后重试。'
  if (code === 'provider_bad_request') return '模型拒绝了分析请求，请更换兼容模型或检查模型设置。'
  if (code === 'provider_rate_limited') return '模型服务当前繁忙，系统会自动重试。'
  if (code === 'provider_insufficient_balance') return '模型账户余额或额度不足，请处理后重试。'
  if (code === 'model_invocation_deadline_exceeded') return '模型单次分析超过当前时限，未完成的步骤已经安全停止；可以重试或更换响应更快的模型。'
  if (code === 'model_invocation_failed') return '模型调用中断，可以保留当前任务并重试。'
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
  const messages: AgentConversationMessage[] = []
  if (run.prompt) {
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
      || (run.runStatus === 'done' ? run.finalResponse : '')
      || '',
    sentAt: run.updateTime || undefined,
    agentRunId: run.runId,
    longTaskId: run.taskId || undefined,
    model: runtimeMessage?.model || modelName,
    taskPlan: followUp ? runtimeMessage?.taskPlan : buildNovelAnalysisTaskPlan(run),
    isError: Boolean(error || (mayHaveError && runtimeMessage?.isError)),
    error: error || undefined,
    ...buildNovelAnalysisTiming(run),
  })
  return messages
}
