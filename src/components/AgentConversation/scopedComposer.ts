import { changeQueuedSubmission, type QueuedSubmissionEdit } from '../../agent-runtime/queuedSubmission.ts'

export interface ComposerSubmission<T> {
  id: string
  scope: string
  content: string
  snapshot: T
  draftVersion?: number
  editing?: boolean
}

/** In-memory drafts and FIFO submissions, owned by a mounted conversation surface. */
export function createScopedComposer<T>() {
  const scopes = new Map<string, {
    draft: string; version: number; queue: ComposerSubmission<T>[]
    pending?: ComposerSubmission<T>; held: boolean
  }>()
  const state = (scope: string) => {
    if (!scopes.has(scope)) scopes.set(scope, { draft: '', version: 0, queue: [], held: false })
    return scopes.get(scope)!
  }
  return {
    draft: (scope: string) => state(scope).draft,
    setDraft(scope: string, value: string) {
      const entry = state(scope)
      entry.draft = value
      entry.version += 1
    },
    adoptDraft(from: string, to: string) {
      if (from === to || !state(from).draft) return
      const source = state(from)
      const target = state(to)
      target.draft = [target.draft, source.draft].filter(Boolean).join('\n')
      target.version += 1
      source.draft = ''
      source.version += 1
    },
    queue: (scope: string) => [...state(scope).queue],
    pending: (scope: string) => state(scope).pending,
    held: (scope: string) => state(scope).held,
    retry(scope: string) { state(scope).held = false },
    updateQueued(scope: string, id: string, patch: QueuedSubmissionEdit | null) {
      const entry = state(scope)
      const next = changeQueuedSubmission(entry.queue, id, patch, item => item.scope === scope)
      if (next === entry.queue) return false
      entry.queue = next
      return true
    },
    enqueue(scope: string, id: string, content: string, snapshot: T, consumeDraft: boolean) {
      const entry = state(scope)
      const submission: ComposerSubmission<T> = { scope, id, content, snapshot: structuredClone(snapshot) }
      if (consumeDraft) {
        entry.draft = ''
        submission.draftVersion = ++entry.version
      }
      entry.held = false
      entry.queue.push(submission)
      return submission
    },
    claim(scope: string) {
      const entry = state(scope)
      if (entry.pending || entry.held || entry.queue.some(item => item.editing)) return undefined
      entry.pending = entry.queue.shift()
      return entry.pending
    },
    settle(submission: ComposerSubmission<T>, accepted: boolean) {
      const entry = state(submission.scope)
      if (entry.pending !== submission) return
      entry.pending = undefined
      if (!accepted) {
        entry.held = true
        if (submission.draftVersion === entry.version && !entry.draft) {
          entry.draft = submission.content
          entry.version += 1
        } else {
          entry.queue.unshift(submission)
        }
      }
    },
    cancelQueue(scope: string) {
      state(scope).queue = []
      state(scope).held = false
    },
  }
}
