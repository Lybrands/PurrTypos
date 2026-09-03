export interface ConversationLoadToken<TSessionId> {
  sessionId: TSessionId
  epoch: number
  identity: string
}

export interface RecoveredRunProjectionOwner<TSessionId> {
  sessionId: TSessionId
  runId: string
  runtimeRevision: number
  streamId: string
}

interface RecoveredRunRuntime<TSessionId> {
  sessionId: TSessionId
  revision: number
  loading: boolean
  streamId?: string
}

export function recoveredRunProjectionControl(runId: string): {
  loading: true
  streamId: string
} {
  return {
    loading: true,
    streamId: `recovered-run:${runId}`,
  }
}

export function captureRecoveredRunProjectionOwner<TSessionId>(
  runtime: RecoveredRunRuntime<TSessionId>,
  runId: string,
): RecoveredRunProjectionOwner<TSessionId> {
  return {
    sessionId: runtime.sessionId,
    runId,
    runtimeRevision: runtime.revision,
    streamId: recoveredRunProjectionControl(runId).streamId,
  }
}

export function canCommitRecoveredRunProjection<TSessionId>(
  owner: RecoveredRunProjectionOwner<TSessionId>,
  runtime: RecoveredRunRuntime<TSessionId> | undefined,
): boolean {
  return Boolean(
    runtime
    && runtime.sessionId === owner.sessionId
    && runtime.revision === owner.runtimeRevision
    && runtime.loading
    && runtime.streamId === owner.streamId,
  )
}

export async function readStableConversationProjection<T>(dependencies: {
  isCurrent(): boolean
  getRevision(): number | undefined
  read(): Promise<T>
  isSuccessful(value: T): boolean
  wait(delayMs?: number): Promise<void>
}): Promise<{ value: T; revision: number | undefined } | undefined> {
  for (let attempt = 0; dependencies.isCurrent(); attempt++) {
    if (attempt >= 9) throw new Error('读取会话状态失败，请重新连接')
    const revisionBeforeRead = dependencies.getRevision()
    const value = await dependencies.read()
    if (
      dependencies.isCurrent()
      && dependencies.isSuccessful(value)
      && dependencies.getRevision() === revisionBeforeRead
    ) return { value, revision: revisionBeforeRead }
    if (dependencies.isCurrent()) await dependencies.wait(Math.min(10_000, 250 * 2 ** attempt))
  }
  return undefined
}

export async function retryCurrentConversationRead<T>(dependencies: {
  isCurrent(): boolean
  read(): Promise<T>
  isRetryable(error: unknown): boolean
  wait(delayMs?: number): Promise<void>
}): Promise<T | undefined> {
  for (let attempt = 0; dependencies.isCurrent(); attempt++) {
    try {
      return await dependencies.read()
    } catch (error) {
      if (!dependencies.isCurrent()) return undefined
      if (!dependencies.isRetryable(error) || attempt >= 8) throw error
      await dependencies.wait(Math.min(10_000, 250 * 2 ** attempt))
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
  }
}
