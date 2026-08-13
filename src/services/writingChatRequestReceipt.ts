import type { AiWritingChatRequestReceipt } from '../types.ts'

export type WritingChatRequestReservation =
  | { kind: 'accepted'; receipt: AiWritingChatRequestReceipt }
  | { kind: 'rejected'; error: string; status: number }

export async function reserveWritingChatRequest(dependencies: {
  requestId: string
  send(requestId: string): Promise<WritingChatRequestReservation>
  wait(): Promise<void>
  isAborted?(): boolean
}): Promise<WritingChatRequestReservation> {
  while (true) {
    if (dependencies.isAborted?.()) {
      throw new DOMException('Writing chat request reservation aborted', 'AbortError')
    }
    try {
      return await dependencies.send(dependencies.requestId)
    } catch {
      // A lost reserve response is ambiguous: replay the same idempotency key
      // until the server returns either the durable receipt or an explicit
      // validation/conflict rejection.
      await dependencies.wait()
    }
  }
}

export async function cancelAfterWritingRequestReservation(dependencies: {
  requestId: string
  reservation: Promise<WritingChatRequestReservation>
  cancel(requestId: string): Promise<AiWritingChatRequestReceipt>
}): Promise<AiWritingChatRequestReceipt | undefined> {
  const reserved = await dependencies.reservation
  if (reserved.kind !== 'accepted') return undefined
  return dependencies.cancel(dependencies.requestId)
}

export async function replayWritingChatPostUntilObserved<T>(dependencies: {
  send(): Promise<T>
  accept?(value: T): boolean
  wait(): Promise<void>
  isAborted(): boolean
}): Promise<T> {
  while (true) {
    if (dependencies.isAborted()) {
      throw new DOMException('Writing chat request POST aborted', 'AbortError')
    }
    try {
      const value = await dependencies.send()
      if (!dependencies.accept || dependencies.accept(value)) return value
    } catch {
      // The durable receipt makes POST a replayable claim. If the first POST
      // reached ASGI, a duplicate observes starting/run_bound instead of
      // submitting a second Run; if it did not, the duplicate starts it.
    }
    await dependencies.wait()
  }
}
