export interface ScreenplayConversationLoadToken {
  projectId: string
  sessionId: number
  epoch: number
  identity: string
}

export function createScreenplayConversationSessionLifecycle() {
  let epoch = 0
  let current: ScreenplayConversationLoadToken | undefined
  let initializing = false
  const drafts = new Map<string, string>()

  const draftKey = (token: Pick<
    ScreenplayConversationLoadToken,
    'projectId' | 'sessionId'
  >) => `${token.projectId}\u0000${token.sessionId}`
  const isCurrent = (token: ScreenplayConversationLoadToken) => (
    current?.epoch === token.epoch
    && current.projectId === token.projectId
    && current.sessionId === token.sessionId
  )

  return {
    beginLoad(projectId: string, sessionId: number): ScreenplayConversationLoadToken {
      epoch += 1
      current = {
        projectId,
        sessionId,
        epoch,
        identity: `screenplay-session:${projectId}:${sessionId}:${epoch}`,
      }
      initializing = true
      return current
    },
    finishLoad(token: ScreenplayConversationLoadToken): boolean {
      if (!isCurrent(token)) return false
      initializing = false
      return true
    },
    invalidate(): void {
      epoch += 1
      current = undefined
      initializing = false
    },
    isCurrent,
    canAct(token: ScreenplayConversationLoadToken | undefined): boolean {
      return Boolean(token && isCurrent(token) && !initializing)
    },
    currentToken: () => current,
    currentIdentity: () => current?.identity,
    setDraft(token: ScreenplayConversationLoadToken, value: string): void {
      drafts.set(draftKey(token), value)
    },
    getDraft(token: ScreenplayConversationLoadToken): string {
      return drafts.get(draftKey(token)) ?? ''
    },
  }
}
