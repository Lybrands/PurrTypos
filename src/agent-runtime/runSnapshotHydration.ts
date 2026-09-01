import type { AiAgentRunSnapshot, AiModelConfig } from '../types.ts'
import { AgentChunkReplay } from './chunkReplay.ts'
import type { AiStreamChunk } from './chunkHandlers/types.ts'
import type { AgentConversationMessage } from './contracts.ts'

type SnapshotResult = {
  success: boolean
  data?: AiAgentRunSnapshot
  error?: string
}

export class AgentRunSnapshotHydrationError extends Error {
  readonly runId: string

  constructor(message: string, runId: string) {
    super(message)
    this.name = 'AgentRunSnapshotHydrationError'
    this.runId = runId
  }
}

export async function loadCompleteAgentRunSnapshot(
  runId: string,
  dependencies: {
    getRunSnapshot(input: {
      runId: string
      after?: number
      limit?: number
    }): Promise<SnapshotResult>
    pageSize?: number
    isCurrent?(): boolean
    initialSnapshot?: AiAgentRunSnapshot
  },
): Promise<AiAgentRunSnapshot | undefined> {
  const isCurrent = dependencies.isCurrent ?? (() => true)
  const pageSize = Math.max(1, Math.floor(dependencies.pageSize ?? 200))
  let after: number | undefined
  let snapshot: AiAgentRunSnapshot | undefined
  const events: AiAgentRunSnapshot['events'] = []
  const seen = new Set<string>()
  let initial = dependencies.initialSnapshot

  while (isCurrent()) {
    let result: SnapshotResult
    try {
      result = initial ? { success: true, data: initial }
        : await dependencies.getRunSnapshot({ runId, after, limit: pageSize })
      initial = undefined
    } catch (error) {
      if (!isCurrent()) return undefined
      throw new AgentRunSnapshotHydrationError(
        error instanceof Error ? error.message : 'Run snapshot unavailable',
        runId,
      )
    }
    if (!result.success || !result.data) {
      if (!isCurrent()) return undefined
      throw new AgentRunSnapshotHydrationError(
        result.error || 'Run snapshot unavailable',
        runId,
      )
    }
    snapshot = result.data
    for (const event of snapshot.events) {
      const identity = `${event.cursor}:${event.type}`
      if (seen.has(identity)) continue
      seen.add(identity)
      events.push(event)
    }
    if (!snapshot.hasMore) break
    const cursor = snapshot.nextCursor
    if (!Number.isFinite(cursor) || cursor <= (after ?? 0)) {
      throw new AgentRunSnapshotHydrationError(
        'Run snapshot pagination did not advance',
        runId,
      )
    }
    after = cursor
  }

  if (!snapshot || !isCurrent()) return undefined
  events.sort((left, right) => left.cursor - right.cursor)
  return { ...snapshot, events }
}

export function mergeAgentRunSnapshot(current: AiAgentRunSnapshot, page: AiAgentRunSnapshot): AiAgentRunSnapshot {
  if (current.run.runId !== page.run.runId) throw new Error('不能合并不同 Run 的事件')
  const events = new Map(current.events.map(event => [`${event.cursor}:${event.type}`, event]))
  for (const event of page.events) events.set(`${event.cursor}:${event.type}`, event)
  return {
    ...page, nextCursor: Math.max(current.nextCursor, page.nextCursor),
    events: [...events.values()].sort((a, b) => a.cursor - b.cursor),
    productEvents: page.productEvents ?? current.productEvents,
  }
}

export function replayAgentRunSnapshot(input: {
  snapshot: AiAgentRunSnapshot
  relatedSnapshots?: AiAgentRunSnapshot[]
  prompt: string
  turnId: string
  sessionId?: number
  model?: string
}): AgentConversationMessage {
  const { snapshot } = input
  const replay = new AgentChunkReplay()
  const model = input.model || snapshot.run.provenance.modelName || ''
  const seed = {
    turnId: input.turnId,
    rootRunId: snapshot.run.runId,
    sessionId: input.sessionId ?? snapshot.run.sessionId ?? 0,
    userContent: input.prompt,
    model,
    turnStartedAt: performance.now() - (snapshotDurationMs(snapshot) ?? 0),
  }
  const cfg: AiModelConfig = {
    id: `run-snapshot:${snapshot.run.runId}`,
    name: model,
    supportsThinking: false,
    thinkingOnly: false,
    apiKey: '',
    baseUrl: '',
  }

  const events = [snapshot, ...(input.relatedSnapshots ?? []).filter(
    (related) => related.run.runId !== snapshot.run.runId,
  )].flatMap((source) => source.events.map((event) => ({ source, event })))
  // Sequence numbers are Run-local. Across bound units, journal timestamps
  // order presentation while the reducer still deduplicates each Run.
  if (input.relatedSnapshots?.length) {
    events.sort((left, right) => (
      String(left.event.createdAt || (left.event.chunk as { emittedAt?: string })?.emittedAt || '')
        .localeCompare(String(right.event.createdAt || (right.event.chunk as { emittedAt?: string })?.emittedAt || ''))
      || (left.source.run.runId === right.source.run.runId
        ? left.event.cursor - right.event.cursor : 0)
    ))
  }
  for (const { source, event } of events) {
    if (event.chunk) replay.dispatch({
      ...seed,
      eventRunId: source.run.runId,
      runRole: source.run.runId === snapshot.run.runId ? 'root' : 'unit',
    }, event.chunk as AiStreamChunk, { cfg })
  }
  if (snapshot.run.status !== 'running') {
    replay.dispatch(seed, terminalChunk(snapshot), { cfg })
  }

  const replayed = replay.assistant(seed.turnId) ?? {
    role: 'assistant' as const,
    content: '',
  }
  const content = snapshot.run.status === 'done' && !replayed.longTaskId
    ? snapshot.run.finalResponse || replayed.content || ''
    : replayed.content || ''
  const canonicalOutput = replayed.canonicalOutput && snapshot.run.status === 'done'
    ? {
        ...replayed.canonicalOutput,
        finalText: content,
        finalStreamStatus: 'committed' as const,
        runStatus: 'done' as const,
        runTerminal: true,
      }
    : replayed.canonicalOutput
  return {
    ...replayed,
    content,
    canonicalOutput,
    agentRunId: snapshot.run.runId,
    model,
    durationMs: snapshotDurationMs(snapshot) ?? replayed.durationMs,
    ...(snapshot.run.status === 'running' ? { isError: false } : {}),
  }
}

function snapshotDurationMs(snapshot: AiAgentRunSnapshot): number | undefined {
  const started = Date.parse(snapshot.run.createdAt || '')
  const ended = Date.parse(snapshot.run.updatedAt || '')
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
    return undefined
  }
  return ended - started
}

function terminalChunk(snapshot: AiAgentRunSnapshot): AiStreamChunk {
  const { runId, status } = snapshot.run
  if (status === 'failed' || status === 'blocked' || status === 'canceled') {
    return {
      done: true,
      runId,
      runResult: { runId, status, errorCode: snapshotErrorCode(snapshot) },
      model: snapshot.run.provenance.modelName || undefined,
    }
  }
  return {
    done: true,
    runId,
    runResult: { runId, status },
    model: snapshot.run.provenance.modelName || undefined,
  }
}

function snapshotErrorCode(snapshot: AiAgentRunSnapshot): string | undefined {
  for (let index = snapshot.events.length - 1; index >= 0; index -= 1) {
    const chunk = snapshot.events[index].chunk as {
      runResult?: { errorCode?: unknown }
      payload?: { errorCode?: unknown; error?: unknown }
    } | undefined
    const value = chunk?.runResult?.errorCode
      ?? chunk?.payload?.errorCode
      ?? chunk?.payload?.error
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}
