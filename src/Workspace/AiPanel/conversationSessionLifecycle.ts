export interface ConversationLoadToken<TSessionId> {
  sessionId: TSessionId
  epoch: number
  identity: string
}

export interface ConversationEditToken<TSessionId> {
  load: ConversationLoadToken<TSessionId>
  messageKey: string
}

export async function readStableConversationProjection<T>(dependencies: {
  isCurrent(): boolean
  getRevision(): number | undefined
  read(): Promise<T>
  isSuccessful(value: T): boolean
  wait(): Promise<void>
}): Promise<{ value: T; revision: number | undefined } | undefined> {
  while (dependencies.isCurrent()) {
    const revisionBeforeRead = dependencies.getRevision()
    const value = await dependencies.read()
    if (
      dependencies.isCurrent()
      && dependencies.isSuccessful(value)
      && dependencies.getRevision() === revisionBeforeRead
    ) return { value, revision: revisionBeforeRead }
    if (dependencies.isCurrent()) await dependencies.wait()
  }
  return undefined
}

export async function retryCurrentConversationRead<T>(dependencies: {
  isCurrent(): boolean
  read(): Promise<T>
  isRetryable(error: unknown): boolean
  wait(): Promise<void>
}): Promise<T | undefined> {
  while (dependencies.isCurrent()) {
    try {
      return await dependencies.read()
    } catch (error) {
      if (!dependencies.isCurrent()) return undefined
      if (!dependencies.isRetryable(error)) throw error
      await dependencies.wait()
    }
  }
  return undefined
}

export function createConversationSessionLifecycle<TSessionId extends string | number>() {
  let epoch = 0
  let current: ConversationLoadToken<TSessionId> | undefined
  let initializing = false
  const drafts = new Map<TSessionId, string>()

  const isCurrent = (token: ConversationLoadToken<TSessionId>) => (
    current?.epoch === token.epoch
    && current.sessionId === token.sessionId
  )

  return {
    beginLoad(sessionId: TSessionId): ConversationLoadToken<TSessionId> {
      epoch += 1
      current = {
        sessionId,
        epoch,
        identity: `${String(sessionId)}:${epoch}`,
      }
      initializing = true
      return current
    },
    finishLoad(token: ConversationLoadToken<TSessionId>): boolean {
      if (!isCurrent(token)) return false
      initializing = false
      return true
    },
    isCurrent,
    canAct(token: ConversationLoadToken<TSessionId>): boolean {
      return isCurrent(token) && !initializing
    },
    currentToken: () => current,
    currentIdentity: () => current?.identity,
    invalidate(): void {
      epoch += 1
      current = undefined
      initializing = false
    },
    setDraft(sessionId: TSessionId, draft: string): void {
      drafts.set(sessionId, draft)
    },
    getDraft(sessionId: TSessionId): string {
      return drafts.get(sessionId) ?? ''
    },
    deleteDraft(sessionId: TSessionId): void {
      drafts.delete(sessionId)
    },
    beginEdit(
      load: ConversationLoadToken<TSessionId>,
      messageKey: string,
    ): ConversationEditToken<TSessionId> {
      return { load, messageKey }
    },
    canSubmitEdit(edit: ConversationEditToken<TSessionId>): boolean {
      return isCurrent(edit.load) && !initializing
    },
  }
}
