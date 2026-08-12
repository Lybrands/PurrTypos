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

/** 每帧最多一次 setConversations，合并同帧内多个 chunk updater */
export function createCommitScheduler(
  setConversations: ConversationMessageSetter,
): CommitScheduler {
  const pending: ConversationUpdater[] = [];
  let rafId: number | null = null;

  const flushCommits = () => {
    if (rafId != null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (pending.length === 0) return;
    const batch = pending.splice(0);
    setConversations((prev) => batch.reduce((state, fn) => fn(state), prev));
  };

  const scheduleCommit = (updater: ConversationUpdater) => {
    pending.push(updater);
    if (rafId != null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      if (pending.length === 0) return;
      const batch = pending.splice(0);
      setConversations((prev) => batch.reduce((state, fn) => fn(state), prev));
    });
  };

  return { scheduleCommit, flushCommits };
}
