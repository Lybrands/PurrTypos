import { flushSync } from 'react-dom'
import type { AgentConversationMessage } from '../contracts.ts'

export type ConversationUpdater = (
  prev: AgentConversationMessage[],
) => AgentConversationMessage[]

export type ConversationMessageSetter = (
  updater: ConversationUpdater,
) => void

export interface CommitScheduler {
  scheduleCommit: (updater: ConversationUpdater) => void;
  flushCommits: () => void;
}

/** Commit each received chunk without a timer or a pending batch. */
export function createCommitScheduler(
  setConversations: ConversationMessageSetter,
): CommitScheduler {
  return {
    scheduleCommit: updater => commitAgentChunk(() => setConversations(updater)),
    flushCommits: () => undefined,
  }
}

export function commitAgentChunk(update: () => void): void {
  flushSync(update)
}

/** Historical pages commit once; live chunks retain immediate delivery. */
export function createAgentReplayPageCommit(replayingHistory: boolean, update: () => void) {
  let pending = false
  return {
    change() {
      if (replayingHistory) pending = true
      else commitAgentChunk(update)
    },
    finish() {
      if (!pending) return
      pending = false
      commitAgentChunk(update)
    },
  }
}
