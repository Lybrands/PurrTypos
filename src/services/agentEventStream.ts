/** One cursor-owning reader for live output and reconnect replay. No snapshot timer. */
export async function consumeAgentEventStream<T extends {
  nextCursor: number
  hasMore?: boolean
  done?: boolean
}>(options: {
  url(after: number): string
  after?: number
  signal: AbortSignal
  onEvent(event: T): void | Promise<void>
  fetch?: typeof fetch
  wait?: (ms: number, signal: AbortSignal) => Promise<void>
}): Promise<void> {
  let cursor = Math.max(0, options.after ?? 0)
  let failures = 0
  const request = options.fetch ?? fetch
  const wait = options.wait ?? waitForAgentRetry
  while (!options.signal.aborted) {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    try {
      const response = await request(options.url(cursor), {
        headers: { Accept: 'text/event-stream' }, signal: options.signal,
      })
      if (!response.ok) {
        const error = new AgentStreamError(`对话连接失败 (${response.status})`)
        error.permanent = response.status >= 400 && response.status < 500
          && ![408, 429].includes(response.status)
        throw error
      }
      if (!response.body) throw new Error('对话连接没有返回事件流')
      reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!options.signal.aborted) {
        const { done, value } = await reader.read()
        buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n')
        let boundary: number
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const payload = block.split('\n').filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart()).join('\n')
          if (!payload) continue // SSE heartbeat comments are transport-only.
          const event = JSON.parse(payload) as T
          if (!Number.isSafeInteger(event.nextCursor) || event.nextCursor < cursor
            || (event.hasMore && event.nextCursor === cursor)) {
            throw new AgentStreamError('对话事件游标无效')
          }
          if (options.signal.aborted) return
          // Commit the cursor only after the owner consumed the page.
          await options.onEvent(event)
          if (event.nextCursor > cursor) failures = 0
          cursor = event.nextCursor
          if (event.done) return
        }
        if (done) throw new Error('对话连接中断，正在恢复')
      }
    } catch (error) {
      if (options.signal.aborted) return
      if ((error instanceof AgentStreamError && error.permanent) || ++failures > 8) {
        throw error
      }
    } finally {
      await reader?.cancel().catch(() => undefined)
      reader?.releaseLock()
    }
    await wait(Math.min(10_000, 250 * 2 ** (failures - 1)), options.signal)
  }
}

class AgentStreamError extends Error { permanent = true }

export function waitForAgentRetry(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    if (signal.aborted) return resolve()
    const finish = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, ms)
    signal.addEventListener('abort', finish, { once: true })
  })
}
