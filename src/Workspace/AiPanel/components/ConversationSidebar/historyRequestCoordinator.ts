export interface HistoryRequestCoordinator {
  activate(): void
  deactivate(): void
  beginLatest(): number
  captureLatest(): number
  invalidateLatest(): void
  isCurrent(request: number): boolean
  isMounted(): boolean
  runOnce<T>(key: string, operation: () => Promise<T>): Promise<T>
}

export function createHistoryRequestCoordinator(): HistoryRequestCoordinator {
  let generation = 0
  let mounted = false
  const inFlight = new Map<string, Promise<unknown>>()

  return {
    activate() {
      mounted = true
      generation += 1
    },
    deactivate() {
      mounted = false
      generation += 1
      inFlight.clear()
    },
    beginLatest() {
      generation += 1
      return generation
    },
    captureLatest() {
      return generation
    },
    invalidateLatest() {
      generation += 1
    },
    isCurrent(request) {
      return mounted && request === generation
    },
    isMounted() {
      return mounted
    },
    runOnce<T>(key: string, operation: () => Promise<T>): Promise<T> {
      const existing = inFlight.get(key) as Promise<T> | undefined
      if (existing) return existing

      let pending: Promise<T>
      try {
        pending = operation()
      } catch (error) {
        pending = Promise.reject(error)
      }
      const tracked = pending.finally(() => {
        if (inFlight.get(key) === tracked) inFlight.delete(key)
      })
      inFlight.set(key, tracked)
      return tracked
    },
  }
}
