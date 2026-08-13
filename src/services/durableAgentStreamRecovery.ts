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
  getRunSnapshot(input: {
    runId: string
    after: number
    limit: number
  }): Promise<ApiResult<AiAgentRunSnapshot>>
  wait(): Promise<void>
  isAborted(): boolean
  emit(chunk: RecoveryChunk): void | Promise<void>
}): Promise<'terminal' | 'aborted'> {
  let runId = String(dependencies.runId || '').trim()
  let cursor = Math.max(0, Math.floor(dependencies.after || 0))
  const seenEvents = new Set<number>()
  const seenProposals = new Set<string>()
  let longTaskDispatched = false

  while (!dependencies.isAborted()) {
    let snapshot: AiAgentRunSnapshot | undefined
    try {
      if (!runId) {
        const latest = await dependencies.getLatestRun(dependencies.sessionId)
        const request = latest.success ? latest.data?.request : undefined
        if (
          request
          && (request.status === 'rejected' || request.status === 'canceled')
          && !latest.data?.snapshot
        ) {
          await dependencies.emit({
            done: true,
            requestResult: request,
            ...(request.status === 'canceled' ? { aborted: true } : {}),
            finalResponseExpected: false,
          })
          return 'terminal'
        }
        if (latest.success && latest.data?.snapshot) {
          const candidate = latest.data.snapshot
          const exactReceiptOwnsCandidate = Boolean(
            request?.runId
            && request.runId === candidate.run.runId,
          )
          if (
            candidate.run.runId !== dependencies.excludeRunId
            || exactReceiptOwnsCandidate
          ) {
            runId = candidate.run.runId
            snapshot = candidate
          }
        }
      }
      if (runId && !snapshot) {
        const result = await dependencies.getRunSnapshot({
          runId,
          after: cursor,
          limit: 500,
        })
        if (result.success) snapshot = result.data
      }
    } catch {
      snapshot = undefined
    }

    if (snapshot) {
      for (const event of snapshot.events) {
        if (event.cursor <= cursor || seenEvents.has(event.cursor)) continue
        seenEvents.add(event.cursor)
        const chunk = event.chunk as RecoveryChunk | undefined
        if (chunk?.payload?.eventType === 'long_task.dispatched') {
          longTaskDispatched = true
        }
        if (chunk) await dependencies.emit(chunk)
        cursor = Math.max(cursor, event.cursor)
      }
      for (const event of snapshot.productEvents ?? []) {
        if (!event.proposalId || seenProposals.has(event.proposalId)) continue
        seenProposals.add(event.proposalId)
        await dependencies.emit(event.chunk as RecoveryChunk)
      }
      if (snapshot.hasMore) {
        const nextCursor = Number(snapshot.nextCursor)
        if (Number.isSafeInteger(nextCursor) && nextCursor > cursor) {
          cursor = nextCursor
          continue
        }
        // Malformed/no-progress pagination is retryable, but must not spin.
        await dependencies.wait()
        continue
      }
      if (snapshot.run.status !== 'running') {
        const errorCode = snapshotErrorCode(snapshot)
        const dispatched = longTaskDispatched || snapshot.events.some((event) => {
          const chunk = event.chunk as RecoveryChunk | undefined
          return chunk?.payload?.eventType === 'long_task.dispatched'
        })
        await dependencies.emit({
          done: true,
          runId: snapshot.run.runId,
          runResult: {
            runId: snapshot.run.runId,
            status: snapshot.run.status,
            ...(errorCode ? { errorCode } : {}),
          },
          ...(snapshot.run.status === 'done' && !dispatched
            ? { finalResponse: snapshot.run.finalResponse }
            : {}),
          ...(dispatched ? { finalResponseExpected: false } : {}),
          model: snapshot.run.provenance.modelName || undefined,
        })
        return 'terminal'
      }
    }
    await dependencies.wait()
  }
  return 'aborted'
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
