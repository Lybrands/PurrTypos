export interface DurableBookRunControlDependencies {
  /** Durable renderer request identity. Recovered legacy Runs may omit it. */
  requestId?: string
  cancelRequest?(requestId: string): Promise<unknown>
  cancelRun(runId: string): Promise<unknown>
  setStopping(stopping: boolean): void
  onCancelError?(error: unknown): void
  /** Present only to make transport ownership explicit; durable stop never calls it. */
  detachTransport?(): void
}

export interface DurableBookRunControl {
  requestStop(): boolean
  observeRunId(runId: string | null | undefined): void
  observeAuthoritativeTerminal(status: string | null | undefined): boolean
  cancelRequest(): Promise<void> | undefined
  stopping(): boolean
}

const TERMINAL_STATUSES = new Set(['done', 'failed', 'blocked', 'canceled'])

export function createDurableBookRunControl(
  dependencies: DurableBookRunControlDependencies,
): DurableBookRunControl {
  let runId: string | undefined
  let stopIntent = false
  let cancelSent = false
  let terminalObserved = false
  let stopping = false
  let pending: Promise<void> | undefined

  const sendCancel = () => {
    if (!stopIntent || cancelSent || terminalObserved) return
    const requestId = String(dependencies.requestId || '').trim()
    const requestCancel = requestId && dependencies.cancelRequest
      ? () => dependencies.cancelRequest!(requestId)
      : undefined
    if (!requestCancel && !runId) return
    cancelSent = true
    pending = Promise.resolve(
      requestCancel ? requestCancel() : dependencies.cancelRun(runId!),
    ).then(
      () => undefined,
      (error) => {
        cancelSent = false
        stopIntent = false
        if (stopping) {
          stopping = false
          dependencies.setStopping(false)
        }
        dependencies.onCancelError?.(error)
      },
    )
  }

  return {
    requestStop() {
      if (stopIntent || terminalObserved) return false
      stopIntent = true
      stopping = true
      dependencies.setStopping(true)
      sendCancel()
      return true
    },
    observeRunId(value) {
      const normalized = String(value || '').trim()
      if (!normalized) return
      if (!runId) runId = normalized
      sendCancel()
    },
    observeAuthoritativeTerminal(status) {
      if (!TERMINAL_STATUSES.has(String(status || '')) || terminalObserved) {
        return false
      }
      terminalObserved = true
      stopIntent = false
      if (stopping) {
        stopping = false
        dependencies.setStopping(false)
      }
      return true
    },
    cancelRequest: () => pending,
    stopping: () => stopping,
  }
}
