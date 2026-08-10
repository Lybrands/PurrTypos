import type { AiModelConfig } from '../types'
import {
  dispatchChunk,
  type AccState,
  type AiStreamChunk,
  type ChunkCtx,
} from '../Workspace/AiPanel/hooks/chunkHandlers'
import type { AppMessage } from '../Workspace/AiPanel/hooks/chunkHandlers/types'
import type { ChatMessage } from '../Workspace/AiPanel/hooks/chat.types'

export interface AgentChunkTurnSeed {
  turnId: string
  sessionId: number
  userContent: string
  model?: string
  turnStartedAt: number
}

/**
 * Shared replay/live runtime for business Agents that receive the canonical
 * Agent chunk protocol from a durable backend stream.
 */
export class AgentChunkReplay {
  private readonly accumulators = new Map<string, AccState>()
  private readonly assistants = new Map<string, ChatMessage>()

  reset(): void {
    this.accumulators.clear()
    this.assistants.clear()
  }

  assistant(turnId: string): ChatMessage | undefined {
    return this.assistants.get(turnId)
  }

  dispatch(
    seed: AgentChunkTurnSeed,
    chunk: AiStreamChunk,
    dependencies: {
      cfg: AiModelConfig
      appMessage: AppMessage
    },
  ): ChatMessage {
    const acc = this.accumulators.get(seed.turnId) ?? this.createAccumulator(seed)
    this.accumulators.set(seed.turnId, acc)
    const apply = (updater: (messages: ChatMessage[]) => ChatMessage[]) => {
      const current = this.assistants.get(seed.turnId) ?? {
        role: 'assistant' as const,
        content: '',
        model: seed.model,
        turnStartedAt: seed.turnStartedAt,
      }
      const next = updater([
        { role: 'user', content: seed.userContent },
        current,
      ])
      const assistant = next.at(-1)
      if (assistant?.role === 'assistant') this.assistants.set(seed.turnId, assistant)
    }
    const ctx: ChunkCtx = {
      acc,
      sessionId: seed.sessionId,
      cfg: dependencies.cfg,
      apiModelName: seed.model || dependencies.cfg.name,
      writingChapters: [],
      availableOutlines: [],
      setConversations: (value) => {
        apply((messages) => (
          typeof value === 'function' ? value(messages) : value
        ))
      },
      scheduleCommit: apply,
      flushCommits: () => undefined,
      setLoading: () => undefined,
      setSessions: () => undefined,
      appMessage: dependencies.appMessage,
      isVisibleSession: () => true,
      persistConversation: false,
      cleanup: () => undefined,
    }
    dispatchChunk(chunk, ctx)
    return this.assistants.get(seed.turnId) ?? {
      role: 'assistant',
      content: '',
      model: seed.model,
      turnStartedAt: seed.turnStartedAt,
    }
  }

  private createAccumulator(seed: AgentChunkTurnSeed): AccState {
    return {
      response: '',
      commentary: '',
      bookId: null,
      sessionId: seed.sessionId,
      chapterId: null,
      needsTitle: false,
      userText: seed.userContent,
      model: seed.model || '',
      turnStartedAt: seed.turnStartedAt,
      commentaryBlocks: [],
      commentaryDurationsMs: [],
    }
  }
}
