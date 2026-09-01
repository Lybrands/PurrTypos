import type {
  AiAgentRunSnapshot,
  AiWritingChatRequestReceipt,
  ApiResult,
} from '../types.ts'

type RecoveryChunk = Record<string, any>
type LatestRunResult = ApiResult<{
  request?: AiWritingChatRequestReceipt
  prompt: string
  snapshot: AiAgentRunSnapshot | null
} | null>

export async function recoverDurableAgentStream(dependencies: {
  runId?: string
  excludeRunId?: string
  sessionId: number
  after: number
  getLatestRun(sessionId: number): Promise<LatestRunResult>
  subscribe(input: {
    runId: string
    after: number
    onEvent(snapshot: AiAgentRunSnapshot): Promise<void>
  }): Promise<void>
  wait(ms: number): Promise<void>
  isAborted(): boolean
  emit(chunk: RecoveryChunk): void | Promise<void>
}): Promise<'terminal' | 'aborted'> {
  let runId = String(dependencies.runId || '').trim()
  let cursor = Math.max(0, Math.floor(dependencies.after || 0))
  let longTaskDispatched = false
  let terminal = false
  let initial: AiAgentRunSnapshot | undefined
  const seenProposals = new Set<string>()
  for (let attempt = 0; !runId && !dependencies.isAborted(); attempt++) {
    if (attempt >= 9) throw new Error('无法恢复本次请求的执行归属，请重新连接')
    let latest: LatestRunResult | undefined
    try { latest = await dependencies.getLatestRun(dependencies.sessionId) } catch { /* bounded retry */ }
    if (latest?.httpStatus && [400, 401, 403, 404].includes(latest.httpStatus)) {
      throw new Error(latest.error || '当前会话不可读取')
    }
    const request = latest?.success ? latest.data?.request : undefined
    if (request && ['rejected', 'canceled'].includes(request.status) && !latest?.data?.snapshot) {
      await dependencies.emit({ done: true, requestResult: request,
        ...(request.status === 'canceled' ? { aborted: true } : {}), finalResponseExpected: false })
      return 'terminal'
    }
    const candidate = latest?.success ? latest.data?.snapshot : undefined
    if (candidate && (candidate.run.runId !== dependencies.excludeRunId
      || request?.runId === candidate.run.runId)) {
      runId = candidate.run.runId
      initial = candidate
      if (request?.runId) await dependencies.emit({ requestReceipt: request })
    } else {
      await dependencies.wait(Math.min(10_000, 250 * 2 ** attempt))
    }
  }
  const consume = async (snapshot: AiAgentRunSnapshot) => {
    if (dependencies.isAborted()) return
    if (snapshot.run.runId !== runId) throw new Error('恢复事件不属于当前 Run')
    const previous = cursor
    for (const event of snapshot.events) {
      if (event.cursor <= cursor) continue
      const chunk = event.chunk as RecoveryChunk | undefined
      if (chunk?.payload?.eventType === 'long_task.dispatched') longTaskDispatched = true
      if (chunk) await dependencies.emit(chunk)
      cursor = Math.max(cursor, event.cursor)
    }
    cursor = Math.max(cursor, snapshot.nextCursor)
    for (const event of snapshot.productEvents ?? []) {
      if (!event.proposalId || seenProposals.has(event.proposalId)) continue
      seenProposals.add(event.proposalId)
      await dependencies.emit(event.chunk as RecoveryChunk)
    }
    if (snapshot.hasMore) {
      if (cursor <= previous) throw new Error('恢复分页游标没有前进')
      return
    }
    if (snapshot.run.status !== 'running') {
      const errorCode = snapshotErrorCode(snapshot)
      await dependencies.emit({
        done: true, runId,
        runResult: { runId, status: snapshot.run.status, ...(errorCode ? { errorCode } : {}) },
        ...(snapshot.run.status === 'done' && !longTaskDispatched
          ? { finalResponse: snapshot.run.finalResponse } : {}),
        ...(longTaskDispatched ? { finalResponseExpected: false } : {}),
        model: snapshot.run.provenance.modelName || undefined,
      })
      terminal = true
    }
  }
  if (initial) await consume(initial)
  if (!terminal && !dependencies.isAborted()) {
    await dependencies.subscribe({ runId, after: cursor, onEvent: consume })
  }
  if (dependencies.isAborted()) return 'aborted'
  if (!terminal) throw new Error('对话连接结束，但执行尚未收口，请重新连接')
  return 'terminal'
}

function snapshotErrorCode(snapshot: AiAgentRunSnapshot): string | undefined {
  for (let index = snapshot.events.length - 1; index >= 0; index -= 1) {
    const chunk = snapshot.events[index].chunk as RecoveryChunk | undefined
    const value = chunk?.runResult?.errorCode
      ?? chunk?.payload?.errorCode
      ?? chunk?.payload?.error
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}
