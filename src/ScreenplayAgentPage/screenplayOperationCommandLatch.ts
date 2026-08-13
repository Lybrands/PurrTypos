export type ScreenplayOperationCommand = 'cancel' | 'resume'

export interface ScreenplayOperationCommandToken {
  operationId: string
  revision: number
  command: ScreenplayOperationCommand
  nonce: number
}

export function createScreenplayOperationCommandLatch() {
  const current = new Map<string, ScreenplayOperationCommandToken>()
  const heldNonces = new Set<number>()
  let nonce = 0
  return {
    tryAcquire(
      operationId: string,
      revision: number,
      command: ScreenplayOperationCommand,
    ): ScreenplayOperationCommandToken | undefined {
      if (current.has(operationId)) return undefined
      const token = { operationId, revision, command, nonce: ++nonce }
      current.set(operationId, token)
      return token
    },
    release(token: ScreenplayOperationCommandToken): void {
      if (
        current.get(token.operationId)?.nonce === token.nonce
        && !heldNonces.has(token.nonce)
      ) {
        current.delete(token.operationId)
      }
    },
    holdUntilAuthoritative(token: ScreenplayOperationCommandToken): void {
      if (
        current.get(token.operationId)?.nonce === token.nonce
        && token.command === 'cancel'
      ) {
        heldNonces.add(token.nonce)
      }
    },
    observeOperation(operation: {
      id: string
      revision: number
      status: string
      cancelRequestedAt?: string | null
      cancelReceiptId?: string | null
    }): void {
      if (
        !current.has(operation.id)
      ) return
      const token = current.get(operation.id)!
      if (!heldNonces.has(token.nonce)) return
      if (
        operation.revision > token.revision
        || operation.cancelRequestedAt
        || operation.cancelReceiptId
        || !['queued', 'running', 'paused'].includes(operation.status)
      ) {
        current.delete(operation.id)
        heldNonces.delete(token.nonce)
      }
    },
    clear(): void {
      current.clear()
      heldNonces.clear()
    },
    hasPendingCancel(): boolean {
      for (const token of current.values()) {
        if (token.command === 'cancel' && heldNonces.has(token.nonce)) {
          return true
        }
      }
      return false
    },
    current: () => current.values().next().value as
      | ScreenplayOperationCommandToken
      | undefined,
  }
}
