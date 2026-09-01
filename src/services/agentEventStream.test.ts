import assert from 'node:assert/strict'
import test from 'node:test'
import { consumeAgentEventStream } from './agentEventStream.ts'

const stream = (...pages: object[]) => new Response(pages.map(page =>
  `data: ${JSON.stringify(page)}\n\n`).join(''), { status: 200 })

test('reconnect resumes the consumed cursor without a snapshot or new command', async () => {
  const urls: string[] = []
  const cursors: number[] = []
  const delays: number[] = []
  await consumeAgentEventStream({
    url: after => `/events?after=${after}`, signal: new AbortController().signal,
    fetch: (async url => {
      urls.push(String(url))
      return urls.length === 1 ? stream({ nextCursor: 3 })
        : stream({ nextCursor: 4, done: true })
    }) as typeof fetch,
    wait: async ms => { delays.push(ms) },
    onEvent: page => { cursors.push(page.nextCursor) },
  })
  assert.deepEqual(urls, ['/events?after=0', '/events?after=3'])
  assert.deepEqual(cursors, [3, 4])
  assert.deepEqual(delays, [250])
})

test('permanent HTTP failures do not retry and transient failures have a budget', async () => {
  for (const status of [403, 404, 503]) {
    let calls = 0
    await assert.rejects(consumeAgentEventStream({
      url: () => '/events', signal: new AbortController().signal,
      fetch: (async () => { calls++; return new Response('', { status }) }) as typeof fetch,
      wait: async () => {}, onEvent: () => {},
    }))
    assert.equal(calls, status === 503 ? 9 : 1)
  }
})

test('abort prevents late pages and stops a reconnect', async () => {
  const owner = new AbortController()
  let calls = 0
  await consumeAgentEventStream({
    url: () => '/events', signal: owner.signal,
    fetch: (async () => { calls++; return stream({ nextCursor: 1 }) }) as typeof fetch,
    onEvent: () => { owner.abort() }, wait: async () => {},
  })
  assert.equal(calls, 1)
})

test('empty pages may advance but non-advancing backlog fails closed', async () => {
  await assert.rejects(consumeAgentEventStream({
    url: () => '/events', signal: new AbortController().signal,
    fetch: (async () => stream({ nextCursor: 0, hasMore: true })) as typeof fetch,
    onEvent: () => { assert.fail('invalid page must not commit') },
  }), /游标/)
})
