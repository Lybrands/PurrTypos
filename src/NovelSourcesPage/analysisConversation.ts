import type { AgentConversationMessage } from '../agent-runtime/contracts.ts'
import type { AiModelConfig, NovelAnalysisRun, NovelAnalysisStreamPage } from '../types.ts'
import { AgentChunkReplay } from '../agent-runtime/chunkReplay.ts'
import type { AiStreamChunk } from '../agent-runtime/chunkHandlers/types.ts'
import { buildNovelAnalysisTaskPlan, buildNovelAnalysisTiming } from './analysisTaskPlan.ts'

const historyModel: AiModelConfig = {
  id: '', name: '', apiKey: '', baseUrl: '', supportsThinking: false, thinkingOnly: false,
}

/** One revision subscription; dynamic unit membership can catch up from memory. */
export class NovelAnalysisConversationStream {
  private events = new Map<number, NovelAnalysisStreamPage['chunks'][number]>()
  private replay = new AgentChunkReplay()
  private membership = ''
  private run?: NovelAnalysisRun

  apply(
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
    const membership = [...ids].join('|')
    const rebuild = this.membership !== membership
    if (rebuild) this.replay.reset()
    this.membership = membership
    const turnId = `novel-analysis:${run.commandId || run.runId}`
    const createdAt = Date.parse(run.createTime || '')
    const events = rebuild ? [...this.events.values()].sort((a, b) => a.cursor - b.cursor) : fresh
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
