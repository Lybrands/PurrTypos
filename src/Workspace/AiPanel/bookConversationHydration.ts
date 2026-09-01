import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import {
  loadCompleteAgentRunSnapshot,
  replayAgentRunSnapshot,
} from '../../agent-runtime/runSnapshotHydration.ts'
import type {
  AiAgentRunSnapshot,
  Conversation,
  ProposedSettingDiff,
  SettingDiffCardState,
} from '../../types.ts'
import { parseConversationsFromApi } from './utils.ts'
import { settingDiffCard } from './settingDiffProjection.ts'

type SnapshotResult = {
  success: boolean
  data?: AiAgentRunSnapshot
  error?: string
}

export class BookConversationHydrationError extends Error {
  readonly runId: string

  constructor(message: string, runId: string) {
    super(message)
    this.name = 'BookConversationHydrationError'
    this.runId = runId
  }
}

export interface BookConversationHydrationDependencies {
  getRunSnapshot(input: {
    runId: string
    after?: number
    limit?: number
  }): Promise<SnapshotResult>
  concurrency?: number
  pageSize?: number
  isCurrent?(): boolean
}

export interface HydratedSettingDiffOccurrence {
  proposal: ProposedSettingDiff & { proposalId: string }
  card: SettingDiffCardState
  message: AgentConversationMessage
  owner: {
    sessionId: number
    bookId?: string
    chapterId?: string | null
    prompt: string
  }
}

export interface HydratedBookConversationReadModel {
  messages: AgentConversationMessage[]
  settingDiffOccurrences: HydratedSettingDiffOccurrence[]
}

export async function hydrateLatestBookRun(input: {
  sessionId: number
  prompt: string
  snapshot: AiAgentRunSnapshot
}, dependencies?: Pick<BookConversationHydrationDependencies, 'getRunSnapshot'>): Promise<HydratedBookConversationReadModel> {
  const synthetic = {
    id: -1,
    session_id: input.sessionId,
    chapter_id: '',
    prompt: input.prompt,
    response: '',
    model: input.snapshot.run.provenance.modelName || '',
    agent_run_id: input.snapshot.run.runId,
  } as Conversation
  const result = await hydrateBookConversationReadModel([synthetic], {
    getRunSnapshot: async request => {
      if (request.after == null) return { success: true, data: input.snapshot }
      if (!dependencies) throw new Error('完整恢复需要读取后续事件页')
      return dependencies.getRunSnapshot(request)
    },
  })
  const readModel = result ?? { messages: [], settingDiffOccurrences: [] }
  const messages = readModel.messages.map((message) => (
    message.role === 'assistant'
      ? { ...message, conversationId: undefined }
      : message
  ))
  const assistant = messages.find(
    (message) => message.role === 'assistant',
  )
  return {
    messages,
    settingDiffOccurrences: readModel.settingDiffOccurrences.map(
      (occurrence) => ({
        ...occurrence,
        message: assistant ?? occurrence.message,
      }),
    ),
  }
}

export function mergeHydratedBookRun(
  history: HydratedBookConversationReadModel,
  latest: HydratedBookConversationReadModel,
  runId: string,
): HydratedBookConversationReadModel {
  const removed = new Set<number>()
  history.messages.forEach((message, index) => {
    if (message.role !== 'assistant' || message.agentRunId !== runId) return
    removed.add(index)
    const preceding = history.messages[index - 1]
    if (preceding?.role === 'user') removed.add(index - 1)
  })
  const latestProposalIds = new Set(
    latest.settingDiffOccurrences.map((occurrence) => occurrence.card.proposalId),
  )
  return {
    messages: [
      ...history.messages.filter((_message, index) => !removed.has(index)),
      ...latest.messages,
    ],
    settingDiffOccurrences: [
      ...history.settingDiffOccurrences.filter(
        (occurrence) => !latestProposalIds.has(occurrence.card.proposalId),
      ),
      ...latest.settingDiffOccurrences,
    ],
  }
}

/**
 * Rebuilds Book turns from the authoritative Run journal. The stored
 * Conversation remains the fallback for legacy/non-Agent turns only.
 */
export async function hydrateBookConversations(
  rows: Conversation[],
  dependencies: BookConversationHydrationDependencies,
): Promise<AgentConversationMessage[] | undefined> {
  const result = await hydrateBookConversationReadModel(rows, dependencies)
  return result?.messages
}

export async function hydrateBookConversationReadModel(
  rows: Conversation[],
  dependencies: BookConversationHydrationDependencies,
): Promise<HydratedBookConversationReadModel | undefined> {
  const isCurrent = dependencies.isCurrent ?? (() => true)
  if (!isCurrent()) return undefined

  const hydrated = new Array<{
    messages: AgentConversationMessage[]
    settingDiffOccurrences: HydratedSettingDiffOccurrence[]
  }>(rows.length)
  const concurrency = Math.max(
    1,
    Math.min(rows.length || 1, Math.floor(dependencies.concurrency ?? 4)),
  )
  let nextIndex = 0

  const workers = await Promise.allSettled(Array.from({ length: concurrency }, async () => {
    while (isCurrent()) {
      const index = nextIndex
      nextIndex += 1
      if (index >= rows.length) return
      hydrated[index] = await hydrateTurn(rows[index], dependencies, isCurrent)
    }
  }))
  const failed = workers.find(
    (worker): worker is PromiseRejectedResult => worker.status === 'rejected',
  )
  if (failed) throw failed.reason

  if (!isCurrent()) return undefined
  return {
    messages: hydrated.flatMap((item) => item.messages),
    settingDiffOccurrences: hydrated.flatMap(
      (item) => item.settingDiffOccurrences,
    ),
  }
}

async function hydrateTurn(
  row: Conversation,
  dependencies: BookConversationHydrationDependencies,
  isCurrent: () => boolean,
): Promise<{
  messages: AgentConversationMessage[]
  settingDiffOccurrences: HydratedSettingDiffOccurrence[]
}> {
  const stored = parseConversationsFromApi([row])
  const user = stored[0]
  const storedAssistant = stored[1]
  const runId = String(row.agent_run_id ?? '').trim()
  if (!runId || !isCurrent()) {
    return { messages: stored, settingDiffOccurrences: [] }
  }

  let loaded: AiAgentRunSnapshot | undefined
  try {
    loaded = await loadCompleteAgentRunSnapshot(runId, {
      getRunSnapshot: dependencies.getRunSnapshot,
      pageSize: dependencies.pageSize,
      isCurrent,
    })
  } catch (error) {
    throw new BookConversationHydrationError(
      error instanceof Error ? error.message : 'Run snapshot unavailable',
      runId,
    )
  }
  if (!loaded || !isCurrent()) {
    return { messages: stored, settingDiffOccurrences: [] }
  }

  const model = loaded.run.provenance.modelName || row.model || ''
  const replayed = replayAgentRunSnapshot({
    snapshot: loaded,
    prompt: row.prompt,
    turnId: `book-conversation:${row.id}:run:${runId}`,
    sessionId: row.session_id,
    model,
  })
  const content = replayed.content || ''
  const assistant: AgentConversationMessage = {
    ...storedAssistant,
    ...replayed,
    content,
    conversationId: row.id,
    agentRunId: runId,
    model: model || storedAssistant.model,
    durationMs: replayed.durationMs ?? storedAssistant.durationMs,
    ...(loaded.run.status === 'running' ? { isError: false } : {}),
  }
  if (content && loaded.run.status === 'done') {
    assistant.error = undefined
    assistant.isError = false
  }
  const resolutions = settingDiffResolutions(row)
  const settingDiffOccurrences = (loaded.productEvents ?? []).flatMap(
    (event): HydratedSettingDiffOccurrence[] => {
      if (event.type !== 'writing.proposed_setting_diff') return []
      const proposal = {
        ...event.payload,
        proposalId: event.proposalId,
      }
      const pending = settingDiffCard(proposal)
      if (!pending) return []
      const resolution = resolutions[event.proposalId]
      const card = resolution?.proposalId === event.proposalId
        ? { ...pending, ...resolution }
        : pending
      return [{
        proposal,
        card,
        message: assistant,
        owner: {
          sessionId: row.session_id,
          bookId: String(proposal.bookId),
          chapterId: row.chapter_id ? String(row.chapter_id) : null,
          prompt: row.prompt,
        },
      }]
    },
  )
  return { messages: [user, assistant], settingDiffOccurrences }
}

function settingDiffResolutions(
  row: Conversation,
): Record<string, SettingDiffCardState> {
  try {
    const parsed = JSON.parse(row.agent_process || '{}') as {
      settingDiff?: { resolutions?: unknown }
    }
    const resolutions = parsed.settingDiff?.resolutions
    if (!resolutions || typeof resolutions !== 'object') return {}
    return Object.fromEntries(Object.entries(resolutions).filter(
      (entry): entry is [string, SettingDiffCardState] => {
        const value = entry[1] as Partial<SettingDiffCardState> | undefined
        return Boolean(
          value
          && value.proposalId === entry[0]
          && (value.status === 'committed' || value.status === 'rejected'),
        )
      },
    ))
  } catch {
    return {}
  }
}
