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
  },
): Promise<AiAgentRunSnapshot | undefined> {
  const isCurrent = dependencies.isCurrent ?? (() => true)
  const pageSize = Math.max(1, Math.floor(dependencies.pageSize ?? 200))
  let after: number | undefined
  let snapshot: AiAgentRunSnapshot | undefined
  const events: AiAgentRunSnapshot['events'] = []
  const seen = new Set<string>()

  while (isCurrent()) {
    let result: SnapshotResult
    try {
      result = await dependencies.getRunSnapshot({ runId, after, limit: pageSize })
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
    if (!Number.isFinite(cursor) || cursor === after) {
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

export function replayAgentRunSnapshot(input: {
  snapshot: AiAgentRunSnapshot
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

  for (const event of snapshot.events) {
    if (event.chunk) replay.dispatch(seed, event.chunk as AiStreamChunk, { cfg })
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
