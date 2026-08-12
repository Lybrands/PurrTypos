export interface HistoryRequestCoordinator {
  beginLatest(): number
  captureLatest(): number
  invalidateLatest(): void
  isCurrent(request: number): boolean
  isMounted(): boolean
  unmount(): void
  runOnce<T>(key: string, operation: () => Promise<T>): Promise<T>
}

export function createHistoryRequestCoordinator(): HistoryRequestCoordinator {
  let generation = 0
  let mounted = true
  const inFlight = new Map<string, Promise<unknown>>()

  return {
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
    unmount() {
      mounted = false
      generation += 1
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
