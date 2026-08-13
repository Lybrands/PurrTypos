import type { AiStreamChunk } from './chunkHandlers/types.ts'

export interface RootRunBindingResolution {
  accepted: boolean
  rootRunId?: string
}

export interface TerminalRootOwnership extends RootRunBindingResolution {
  terminal: boolean
  source?: 'runResult' | 'requestResult'
}

/** Resolve a backend/persisted Root binding without allowing later rebinding. */
export function resolveRootRunBinding(
  currentRootRunId: string | undefined,
  candidateRunId: string | null | undefined,
): RootRunBindingResolution {
  const current = normalizedRunId(currentRootRunId)
  const candidate = normalizedRunId(candidateRunId)
  if (!candidate) return { accepted: false, rootRunId: current }
  if (current && candidate !== current) {
    return { accepted: false, rootRunId: current }
  }
  return { accepted: true, rootRunId: current || candidate }
}

/**
 * Root terminal ownership shared by live transport, reducer, and host control.
 * A request result without a Run id is valid only before any Run is observed.
 */
export function resolveTerminalRootOwnership(
  chunk: AiStreamChunk,
  currentRootRunId?: string,
  observedRunId?: string,
  currentRequestId?: string,
): TerminalRootOwnership {
  if (!chunk.done) return { terminal: false, accepted: true }
  if (chunk.requestResult) {
    const candidate = normalizedRunId(chunk.requestResult.runId)
    if (!candidate) {
      const expectedRequestId = normalizedRunId(currentRequestId)
      const resultRequestId = normalizedRunId(chunk.requestResult.requestId)
      return {
        terminal: true,
        source: 'requestResult',
        accepted: !normalizedRunId(currentRootRunId)
          && !normalizedRunId(observedRunId)
          && Boolean(expectedRequestId && resultRequestId)
          && resultRequestId === expectedRequestId,
      }
    }
    const binding = resolveRootRunBinding(currentRootRunId, candidate)
    return {
      terminal: true,
      source: 'requestResult',
      ...binding,
    }
  }
  if (chunk.runResult) {
    const candidate = normalizedRunId(chunk.runResult.runId)
    if (!candidate) {
      return {
        terminal: true,
        source: 'runResult',
        accepted: !normalizedRunId(currentRootRunId)
          && !normalizedRunId(observedRunId),
      }
    }
    const binding = resolveRootRunBinding(
      currentRootRunId,
      candidate,
    )
    return {
      terminal: true,
      source: 'runResult',
      ...binding,
    }
  }
  return { terminal: false, accepted: true }
}

function normalizedRunId(value: string | null | undefined): string | undefined {
  return String(value || '').trim() || undefined
}
