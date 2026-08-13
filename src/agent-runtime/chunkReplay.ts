import type { AiModelConfig } from '../types.ts'
import type { AgentConversationMessage } from './contracts.ts'
import {
  dispatchAgentChunk,
  initialAgentAccumulator,
  type AgentAccumulator,
  type AgentChunkHost,
  type AgentChunkRuntimeContext,
  type AiStreamChunk,
} from './chunkHandlers/index.ts'

export interface AgentChunkTurnSeed {
  turnId: string
  /** Root identity from a persisted Run snapshot, never from an event envelope. */
  rootRunId?: string
  sessionId: number
  userContent: string
  model?: string
  turnStartedAt: number
}

/** Replays canonical Agent chunks through the same business-agnostic reducer as live runs. */
export class AgentChunkReplay {
  private readonly accumulators = new Map<string, AgentAccumulator>()
  private readonly assistants = new Map<string, AgentConversationMessage>()

  reset(): void {
    this.accumulators.clear()
    this.assistants.clear()
  }

  assistant(turnId: string): AgentConversationMessage | undefined {
    return this.assistants.get(turnId)
  }

  dispatch(
    seed: AgentChunkTurnSeed,
    chunk: AiStreamChunk,
    dependencies: {
      cfg: AiModelConfig
      appMessage?: unknown
    },
  ): AgentConversationMessage {
    const acc = this.accumulators.get(seed.turnId)
      ?? initialAgentAccumulator({
        sessionId: seed.sessionId,
        userText: seed.userContent,
        model: seed.model,
        turnStartedAt: seed.turnStartedAt,
      })
    const rootRunId = String(seed.rootRunId || '').trim()
    if (rootRunId) acc.conversationRunId = rootRunId
    this.accumulators.set(seed.turnId, acc)

    let messages: AgentConversationMessage[] = [
      { role: 'user', content: seed.userContent },
      this.assistants.get(seed.turnId) ?? {
        role: 'assistant',
        content: '',
        model: seed.model,
        turnStartedAt: seed.turnStartedAt,
      },
    ]
    const replaceMessages = (next: AgentConversationMessage[]) => {
      messages = next
      const assistant = next.at(-1)
      if (assistant?.role === 'assistant') {
        this.assistants.set(seed.turnId, assistant)
      }
    }
    const apply = (
      updater: (current: AgentConversationMessage[]) => AgentConversationMessage[],
    ) => replaceMessages(updater(messages))
    const host: AgentChunkHost = {
      readMessages: () => messages,
      replaceMessages,
      scheduleCommit: apply,
      flushCommits: () => undefined,
      setRunning: () => undefined,
      isVisible: () => true,
      onHostChunk: () => undefined,
      onSettled: () => undefined,
    }
    const context: AgentChunkRuntimeContext = {
      acc,
      sessionId: seed.sessionId,
      turnId: seed.turnId,
      modelIdentity: {
        configId: dependencies.cfg.id,
        name: seed.model || dependencies.cfg.name,
      },
      host,
      persistConversation: false,
      now: () => performance.now(),
    }

    dispatchAgentChunk(chunk, context)
    return this.assistants.get(seed.turnId) ?? messages[1]
  }
}
