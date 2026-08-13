import type { AiAgentRunSnapshot } from '../../types.ts'
import type { AiStreamChunk } from '../../agent-runtime/chunkHandlers/types.ts'

type SnapshotResult = {
  success: boolean
  data?: AiAgentRunSnapshot
  error?: string
}

export interface BookProposalProjectionReconciler {
  refresh(runId: string): Promise<void>
  refreshFinal(runId: string): Promise<void>
}

/**
 * Reconciles live Book cards from the exact same durable product projection
 * used by fresh hydration. The public Core stream intentionally carries no
 * private writing effect payloads.
 */
export function createBookProposalProjectionReconciler(dependencies: {
  getRunSnapshot(input: {
    runId: string
    limit?: number
  }): Promise<SnapshotResult>
  dispatch(chunk: AiStreamChunk): void
  waitBeforeFinalRetry?(delayMs: number): Promise<void>
}): BookProposalProjectionReconciler {
  const seen = new Set<string>()
  const inFlight = new Map<string, Promise<boolean>>()

  const waitBeforeFinalRetry = dependencies.waitBeforeFinalRetry
    ?? ((delayMs: number) => new Promise<void>((resolve) => {
      setTimeout(resolve, delayMs)
    }))

  const refreshOnce = (runId: string): Promise<boolean> => {
    const normalizedRunId = String(runId || '').trim()
    if (!normalizedRunId) return Promise.resolve(false)
    const current = inFlight.get(normalizedRunId)
    if (current) return current
    const pending = dependencies.getRunSnapshot({
      runId: normalizedRunId,
      limit: 1,
    }).then((result) => {
      if (!result.success || !result.data) return false
      for (const event of result.data.productEvents ?? []) {
        if (
          event.type !== 'writing.proposed_setting_diff'
          || event.runId !== normalizedRunId
          || !event.proposalId
          || seen.has(event.proposalId)
        ) continue
        seen.add(event.proposalId)
        dependencies.dispatch(event.chunk as AiStreamChunk)
      }
      return true
    }).catch(() => {
      // A later tool/terminal observation retries the durable read.
      return false
    }).finally(() => {
      if (inFlight.get(normalizedRunId) === pending) {
        inFlight.delete(normalizedRunId)
      }
    })
    inFlight.set(normalizedRunId, pending)
    return pending
  }

  const refresh = async (runId: string): Promise<void> => {
    await refreshOnce(runId)
  }

  const refreshFinal = async (runId: string): Promise<void> => {
    const normalizedRunId = String(runId || '').trim()
    if (!normalizedRunId) return
    const current = inFlight.get(normalizedRunId)
    if (current) await current
    const retryDelaysMs = [100, 250, 500, 1_000]
    for (let attempt = 0; attempt <= retryDelaysMs.length; attempt += 1) {
      if (await refreshOnce(normalizedRunId)) return
      const delayMs = retryDelaysMs[attempt]
      if (delayMs === undefined) return
      await waitBeforeFinalRetry(delayMs)
    }
  }

  return { refresh, refreshFinal }
}

export function shouldRefreshBookProposalProjection(chunk: AiStreamChunk): boolean {
  if (chunk.runResult?.runId) return true
  if (chunk.kind === 'tool.event') {
    const status = String(chunk.payload?.status || '')
    return ['completed', 'succeeded', 'done'].includes(status)
  }
  if (chunk.kind === 'operation.finished') {
    return chunk.payload?.status === 'succeeded'
  }
  return false
}
