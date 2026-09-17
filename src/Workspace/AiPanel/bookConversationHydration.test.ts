import assert from 'node:assert/strict'
import test from 'node:test'
import type { AiAgentRunSnapshot, Conversation } from '../../types.ts'
import { AgentChunkReplay } from '../../agent-runtime/chunkReplay.ts'
import type { AiStreamChunk } from '../../agent-runtime/chunkHandlers/types.ts'
import { getAssistantRenderableMarkdown } from '../../components/AgentConversation/assistantCopy.ts'
import {
  BookConversationHydrationError,
  mergeHydratedBookRun,
  hydrateBookConversationReadModel,
  hydrateLatestBookRun,
} from './bookConversationHydration.ts'

import { createConversationSessionLifecycle } from './conversationSessionLifecycle.ts'

const hydrateBookConversations = (...args: Parameters<typeof hydrateBookConversationReadModel>) =>
  hydrateBookConversationReadModel(...args).then(result => result?.messages)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

const canonical = (
  runId: string,
  sequence: number,
  overrides: Record<string, unknown>,
) => ({
  eventId: `${runId}-event-${sequence}`,
  outputStreamId: null,
  runId,
  turnId: null,
  invocationId: null,
  sequence,
  source: 'runtime',
  kind: 'runtime.event',
  channel: 'lifecycle',
  visibility: 'public',
  payload: {},
  occurredAt: `2026-08-12T08:00:0${sequence}+00:00`,
  emittedAt: `2026-08-12T08:00:0${sequence}+00:00`,
  ...overrides,
})

function row(id: number, runId: string, response = '客户端残缺快照'): Conversation {
  return {
    id,
    session_id: 7,
    chapter_id: 'chapter-1',
    prompt: `问题 ${id}`,
    response,
    model: 'test-model',
    agent_run_id: runId,
  } as Conversation
}

function snapshot(
  runId: string,
  status: AiAgentRunSnapshot['run']['status'],
  chunks: Record<string, unknown>[],
  finalResponse = '',
): AiAgentRunSnapshot {
  return {
    version: 2,
    run: {
      runId,
      sessionId: 7,
      conversationId: Number(runId.split('-').at(-1)) || null,
      status,
      finalResponse,
      createdAt: '2026-08-12T08:00:00+00:00',
      updatedAt: '2026-08-12T08:00:06+00:00',
      execution: { attempt: 1, cancellationRequested: status === 'canceled' },
      provenance: { modelName: 'test-model' },
    },
    todos: [],
    events: chunks.map((chunk, index) => ({
      version: 1,
      cursor: index + 1,
      type: String(chunk.kind || 'runtime.event'),
      runId,
      payload: {},
      chunk,
    })),
    delegations: {
      items: [],
      aggregate: {
        state: 'ready',
        counts: { queued: 0, claimed: 0, running: 0, done: 0, failed: 0, canceled: 0 },
        requiredFailures: [],
        results: [],
      },
    },
    nextCursor: chunks.length,
    hasMore: false,
  }
}

test('durable snapshot failure is not accepted as stored Conversation authority', async () => {
  await assert.rejects(
    hydrateBookConversationReadModel([row(900, 'run-900')], {
      getRunSnapshot: async () => ({
        success: false,
        error: 'snapshot temporarily unavailable',
      }),
    }),
    BookConversationHydrationError,
  )
})

test('fresh Book hydrate replays authoritative completed operations and approval state', async () => {
  const runId = 'run-101'
  const chunks = [
    canonical(runId, 1, {
      kind: 'operation.started',
      channel: 'operation',
      payload: {
        operationId: 'tool-1',
        kind: 'tool',
        startedAt: '2026-08-12T08:00:01+00:00',
        display: { labelParams: { toolName: 'readChapter' } },
      },
    }),
    canonical(runId, 2, {
      kind: 'operation.finished',
      channel: 'operation',
      payload: {
        operationId: 'tool-1',
        status: 'succeeded',
        finishedAt: '2026-08-12T08:00:01.048+00:00',
        durationMs: 48,
        display: {},
      },
    }),
    canonical(runId, 3, {
      payload: {
        eventType: 'approval.requested',
        data: {
          approvalId: 'approval-1',
          toolName: 'writeChapter',
          title: '写入章节',
          riskLevel: 'write',
          summary: '写入候选内容',
        },
      },
    }),
    canonical(runId, 4, {
      payload: {
        eventType: 'approval.resolved',
        data: { approvalId: 'approval-1', toolName: 'writeChapter', status: 'approved' },
      },
    }),
    canonical(runId, 5, {
      source: 'provider',
      kind: 'provider.content_delta',
      channel: 'final',
      outputStreamId: 'final-1',
      invocationId: 'invocation-1',
      payload: { delta: 'Run journal 终稿' },
    }),
    canonical(runId, 6, {
      kind: 'stream.committed',
      channel: 'final',
      outputStreamId: 'final-1',
      invocationId: 'invocation-1',
      payload: { finishReason: 'stop' },
    }),
    canonical(runId, 7, {
      kind: 'run.lifecycle',
      channel: 'lifecycle',
      payload: { status: 'done' },
    }),
  ]
  const hydrated = await hydrateBookConversations([row(101, runId)], {
    getRunSnapshot: async () => ({ success: true, data: snapshot(runId, 'done', chunks, 'Run journal 终稿') }),
  })
  const assistant = hydrated!.at(-1)!
  const live = new AgentChunkReplay()
  const seed = {
    turnId: 'live-101',
    sessionId: 7,
    userContent: '问题 101',
    model: 'test-model',
    turnStartedAt: performance.now(),
  }
  const cfg = {
    id: 'test-model',
    name: 'test-model',
    supportsThinking: false,
    thinkingOnly: false,
    apiKey: '',
    baseUrl: '',
  }
  for (const chunk of chunks) live.dispatch(seed, chunk as AiStreamChunk, { cfg })
  live.dispatch(seed, {
    done: true,
    runResult: { runId, status: 'done' },
  }, { cfg })
  const liveAssistant = live.assistant(seed.turnId)!

  assert.equal(assistant.content, 'Run journal 终稿')
  assert.equal(assistant.canonicalOutput?.operations['tool-1']?.status, 'succeeded')
  assert.equal(assistant.canonicalOutput?.operations['tool-1']?.durationMs, 48)
  assert.equal(assistant.toolApprovals?.[0]?.status, 'approved')
  assert.equal(assistant.agentRunId, runId)
  assert.equal(assistant.conversationId, 101)
  assert.equal(assistant.durationMs, 6_000)
  assert.deepEqual(assistant.canonicalOutput, liveAssistant.canonicalOutput)
  assert.deepEqual(assistant.toolApprovals, liveAssistant.toolApprovals)
})

test('fresh hydrate preserves failed, canceled, and empty terminal structure', async () => {
  const cases = [
    { id: 201, status: 'failed' as const },
    { id: 202, status: 'canceled' as const },
    { id: 203, status: 'done' as const },
  ]
  const hydrated = await hydrateBookConversations(
    cases.map(({ id }) => row(id, `run-${id}`, '不可信客户端正文')),
    {
      getRunSnapshot: async ({ runId }) => {
        const item = cases.find(({ id }) => runId === `run-${id}`)!
        return { success: true, data: snapshot(runId, item.status, [], '') }
      },
      concurrency: 2,
    },
  )
  const assistants = hydrated!.filter((message) => message.role === 'assistant')

  assert.equal(assistants[0].content, '')
  assert.equal(assistants[0].isError, true)
  assert.ok(assistants[0].error)
  assert.equal(assistants[1].content, '')
  assert.ok(assistants[1].termination)
  assert.equal(assistants[2].content, '')
  assert.equal(assistants[2].isError, false)
  assert.ok(assistants[2].error)
})

test('failed and canceled hydration preserves public provider text without trusting finalResponse', async () => {
  const providerText = (runId: string, text: string) => canonical(runId, 1, {
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    outputStreamId: `final-${runId}`,
    invocationId: `invocation-${runId}`,
    payload: { delta: text },
  })
  const hydrated = await hydrateBookConversations([
    row(204, 'run-204'),
    row(205, 'run-205'),
  ], {
    getRunSnapshot: async ({ runId }) => ({
      success: true,
      data: runId === 'run-204'
        ? snapshot(runId, 'failed', [providerText(runId, '失败前已生成')], '错误码不可当正文')
        : snapshot(runId, 'canceled', [providerText(runId, '取消前已生成')], '取消回执不可当正文'),
    }),
  })
  const assistants = hydrated!.filter((message) => message.role === 'assistant')

  assert.equal(assistants[0].content, '失败前已生成')
  assert.equal(assistants[0].canonicalOutput?.finalText, '失败前已生成')
  assert.ok(assistants[0].error)
  assert.equal(assistants[1].content, '取消前已生成')
  assert.equal(assistants[1].canonicalOutput?.finalText, '取消前已生成')
  assert.ok(assistants[1].termination)
})

test('failed Run hydration cannot replace newer state through a stale load token', async () => {
  let currentIdentity = 'session:7:1'
  const a = hydrateBookConversations([row(301, 'run-301')], {
    getRunSnapshot: async () => ({ success: false, error: 'offline' }),
    isCurrent: () => currentIdentity === 'session:7:1',
  })
  currentIdentity = 'session:8:2'

  assert.equal(await a, undefined)
})

test('Run finalResponse and structured error code are authoritative over client and partial events', async () => {
  const completed = snapshot('run-401', 'done', [canonical('run-401', 1, {
    source: 'provider',
    kind: 'provider.content_delta',
    channel: 'final',
    outputStreamId: 'final-401',
    invocationId: 'invocation-401',
    payload: { delta: '事件仅有前半段' },
  })], '服务端完整终稿')
  const failed = snapshot('run-402', 'failed', [canonical('run-402', 1, {
    kind: 'run.lifecycle',
    payload: { status: 'failed', error: 'provider_rate_limited' },
  })])
  const hydrated = await hydrateBookConversations([
    row(401, 'run-401'),
    row(402, 'run-402'),
  ], {
    getRunSnapshot: async ({ runId }) => ({
      success: true,
      data: runId === 'run-401' ? completed : failed,
    }),
  })
  const assistants = hydrated!.filter((message) => message.role === 'assistant')

  assert.equal(assistants[0].content, '服务端完整终稿')
  assert.equal(assistants[0].canonicalOutput?.finalText, '服务端完整终稿')
  assert.equal(assistants[0].canonicalOutput?.finalStreamStatus, 'committed')
  assert.equal(getAssistantRenderableMarkdown(assistants[0]), '服务端完整终稿')
  assert.match(assistants[1].error || '', /请求过于频繁|限额/)
})

test('Run hydration bounds concurrent snapshot reads', async () => {
  let active = 0
  let peak = 0
  const pending = hydrateBookConversations(
    Array.from({ length: 5 }, (_value, index) => row(500 + index, `run-${500 + index}`)),
    {
      concurrency: 2,
      getRunSnapshot: async ({ runId }) => {
        active += 1
        peak = Math.max(peak, active)
        await new Promise<void>((resolve) => setTimeout(resolve, 0))
        active -= 1
        return { success: true, data: snapshot(runId, 'done', [], '') }
      },
    },
  )

  await pending
  assert.equal(peak, 2)
})

test('failed hydration waits for sibling workers before a caller can retry the batch', async () => {
  const slow = deferred<void>()
  let active = 0
  let slowStarted = false
  let rejected = false
  const pending = hydrateBookConversationReadModel([
    row(550, 'run-fast-failure'),
    row(551, 'run-slow-sibling'),
  ], {
    concurrency: 2,
    getRunSnapshot: async ({ runId }) => {
      active += 1
      if (runId === 'run-fast-failure') {
        active -= 1
        return { success: false, error: 'temporary failure' }
      }
      slowStarted = true
      await slow.promise
      active -= 1
      return { success: true, data: snapshot(runId, 'done', [], '') }
    },
  }).catch((error) => {
    rejected = true
    throw error
  })

  await new Promise<void>((resolve) => setTimeout(resolve, 0))
  assert.equal(rejected, false)
  assert.equal(slowStarted, true)
  slow.resolve()
  await assert.rejects(pending, BookConversationHydrationError)
  assert.equal(active, 0)
})

test('deferred A hydrate cannot commit, submit, or edit after B becomes current', async () => {
  const lifecycle = createConversationSessionLifecycle<number>()
  const pendingA = deferred<ReturnType<typeof snapshot>>()
  const tokenA = lifecycle.beginLoad(7)
  lifecycle.setDraft(7, 'A draft')
  const hydrateA = hydrateBookConversations([row(701, 'run-701')], {
    getRunSnapshot: async () => ({ success: true, data: await pendingA.promise }),
    isCurrent: () => lifecycle.isCurrent(tokenA),
  })

  const tokenB = lifecycle.beginLoad(8)
  lifecycle.setDraft(8, 'B draft')
  const hydrateB = await hydrateBookConversations([row(801, 'run-801')], {
    getRunSnapshot: async () => ({
      success: true,
      data: snapshot('run-801', 'done', [], 'B final'),
    }),
    isCurrent: () => lifecycle.isCurrent(tokenB),
  })
  assert.equal(lifecycle.canAct(tokenB), false)
  lifecycle.finishLoad(tokenB)
  pendingA.resolve(snapshot('run-701', 'done', [], 'A late final'))

  assert.equal(hydrateB!.at(-1)?.content, 'B final')
  assert.equal(await hydrateA, undefined)
  assert.equal(lifecycle.canAct(tokenA), false)
  assert.equal(lifecycle.getDraft(8), 'B draft')
})

test('fresh read model restores proposal payload and isolates same-entity resolutions', async () => {
  const first = snapshot('run-901', 'done', [], '')
  first.productEvents = [{
    version: 1,
    type: 'writing.proposed_setting_diff',
    runId: 'run-901',
    proposalId: 'run-901:call-1:0',
    toolCallId: 'call-1',
    effectIndex: 0,
    payload: {
      proposalId: 'run-901:call-1:0',
      kind: 'character',
      bookId: 'book-1',
      characterId: 1,
      before: { name: '甲', tags: '', profileMd: '旧一' },
      proposed: { name: '甲', tags: '', profileMd: '新一' },
    },
    chunk: {
      runId: 'run-901',
      proposedSettingDiff: {
        proposalId: 'run-901:call-1:0',
        kind: 'character',
        bookId: 'book-1',
        characterId: 1,
        before: { name: '甲', tags: '', profileMd: '旧一' },
        proposed: { name: '甲', tags: '', profileMd: '新一' },
      },
    },
  }]
  const second = snapshot('run-902', 'done', [], '')
  second.productEvents = [{
    ...first.productEvents[0],
    runId: 'run-902',
    proposalId: 'run-902:call-2:0',
    toolCallId: 'call-2',
    payload: {
      ...first.productEvents[0].payload,
      proposalId: 'run-902:call-2:0',
      before: { name: '甲', tags: '', profileMd: '旧二' },
      proposed: { name: '甲', tags: '', profileMd: '新二' },
    },
    chunk: {
      runId: 'run-902',
      proposedSettingDiff: {
        ...first.productEvents[0].payload,
        proposalId: 'run-902:call-2:0',
        before: { name: '甲', tags: '', profileMd: '旧二' },
        proposed: { name: '甲', tags: '', profileMd: '新二' },
      },
    },
  }]
  const firstRow = row(901, 'run-901', '')
  firstRow.agent_process = JSON.stringify({
    settingDiff: {
      resolutions: {
        'run-901:call-1:0': {
          proposalId: 'run-901:call-1:0',
          sessionKey: 'character:1',
          kind: 'character',
          title: '人物「甲」',
          status: 'rejected',
        },
      },
    },
  })

  const hydrated = await hydrateBookConversationReadModel([
    firstRow,
    row(902, 'run-902', ''),
  ], {
    getRunSnapshot: async ({ runId }) => ({
      success: true,
      data: runId === 'run-901' ? first : second,
    }),
  })

  assert.deepEqual(
    hydrated!.settingDiffOccurrences.map(({ card }) => ({
      proposalId: card.proposalId,
      sessionKey: card.sessionKey,
      status: card.status,
    })),
    [
      {
        proposalId: 'run-901:call-1:0',
        sessionKey: 'character:1',
        status: 'rejected',
      },
      {
        proposalId: 'run-902:call-2:0',
        sessionKey: 'character:1',
        status: 'pending',
      },
    ],
  )
})

test('running latest Run rehydrates as attached work without a fake terminal conversation', async () => {
  const running = snapshot('run-running', 'running', [canonical('run-running', 1, {
    kind: 'operation.started',
    channel: 'operation',
    payload: {
      operationId: 'tool-running',
      kind: 'tool',
      startedAt: '2026-08-12T08:00:01+00:00',
      display: {},
    },
  })])
  const { hydrateLatestBookRun } = await import('./bookConversationHydration.ts')
  const hydrated = await hydrateLatestBookRun({
    sessionId: 7,
    prompt: '仍在执行',
    snapshot: running,
  })

  assert.equal(hydrated.messages[0].content, '仍在执行')
  assert.equal(hydrated.messages[1].agentRunId, 'run-running')
  assert.equal(hydrated.messages[1].conversationId, undefined)
  assert.equal(
    hydrated.messages[1].canonicalOutput?.operations['tool-running']?.status,
    'running',
  )
  assert.equal(hydrated.messages[1].termination, undefined)
  assert.equal(hydrated.messages[1].isError, false)
})

test('running shell and latest snapshot replace the same Run turn instead of duplicating it', () => {
  const history = {
    messages: [
      { role: 'user' as const, content: '旧轮' },
      { role: 'assistant' as const, content: '旧答', agentRunId: 'run-old' },
      { role: 'user' as const, content: '当前问题', conversationId: 31 },
      {
        role: 'assistant' as const,
        content: '',
        conversationId: 31,
        agentRunId: 'run-running',
      },
    ],
    settingDiffOccurrences: [],
  }
  const latest = {
    messages: [
      { role: 'user' as const, content: '当前问题' },
      {
        role: 'assistant' as const,
        content: '实时部分',
        agentRunId: 'run-running',
      },
    ],
    settingDiffOccurrences: [],
  }

  const merged = mergeHydratedBookRun(history, latest, 'run-running')

  assert.deepEqual(merged.messages.map((message) => message.content), [
    '旧轮',
    '旧答',
    '当前问题',
    '实时部分',
  ])
  assert.equal(
    merged.messages.filter((message) => message.agentRunId === 'run-running').length,
    1,
  )
})

test('running merge keeps older pending proposal occurrences for fresh reload', () => {
  const oldOccurrence = {
    proposal: { proposalId: 'proposal-old' },
    card: { proposalId: 'proposal-old' },
  }
  const runningOccurrence = {
    proposal: { proposalId: 'proposal-running' },
    card: { proposalId: 'proposal-running' },
  }
  const merged = mergeHydratedBookRun(
    {
      messages: [],
      settingDiffOccurrences: [oldOccurrence],
    } as never,
    {
      messages: [],
      settingDiffOccurrences: [runningOccurrence],
    } as never,
    'run-running',
  )

  assert.deepEqual(
    merged.settingDiffOccurrences.map((occurrence) => occurrence.card.proposalId),
    ['proposal-old', 'proposal-running'],
  )
})

test('latest terminal fallback exhausts journal pages before committing read model', async () => {
  const first = snapshot('run-paged-terminal', 'done', [canonical(
    'run-paged-terminal',
    1,
    { kind: 'runtime.event', payload: { eventType: 'first', data: {} } },
  )], '服务端终稿')
  first.hasMore = true
  first.nextCursor = 500
  const second = snapshot('run-paged-terminal', 'done', [canonical(
    'run-paged-terminal',
    2,
    {
      kind: 'operation.started',
      channel: 'operation',
      payload: {
        operationId: 'operation-after-500',
        kind: 'tool',
        startedAt: '2026-08-12T08:00:02+00:00',
        display: {},
      },
    },
  )], '服务端终稿')
  second.events[0].cursor = 501
  second.nextCursor = 501
  const reads: Array<number | undefined> = []

  const hydrated = await hydrateLatestBookRun({
    sessionId: 7,
    prompt: '分页终态',
    snapshot: first,
  }, {
    getRunSnapshot: async ({ after }) => {
      reads.push(after)
      return { success: true, data: after == null ? first : second }
    },
  })

  assert.deepEqual(reads, [500])
  assert.equal(hydrated.messages[1].content, '服务端终稿')
  assert.equal(
    hydrated.messages[1].canonicalOutput?.operations['operation-after-500']?.status,
    'running',
  )
})

test('snapshot relatedRuns project onto the assistant message as delegations', async () => {
  const runId = 'run-300'
  const withChildren = {
    ...snapshot(runId, 'done', [], '委派后终稿'),
    run: {
      ...snapshot(runId, 'done', [], '委派后终稿').run,
      relatedRuns: [
        {
          runId: 'child-1',
          status: 'done',
          role: 'child' as const,
          agentId: 'agent-draft',
          agentName: 'draft-agent',
          agentTitle: '草稿',
          objective: '写出草稿。',
          createTime: '2026-08-12 08:00:02',
        },
        {
          runId: 'child-2',
          status: 'failed',
          role: 'child' as const,
          agentName: 'review-agent',
          objective: '审校草稿。',
        },
        {
          runId: 'previous-root',
          status: 'done',
          role: 'previous_root' as const,
        },
      ],
    },
  }

  const messages = await hydrateBookConversations([row(300, runId)], {
    getRunSnapshot: async () => ({ success: true, data: withChildren }),
  })

  const assistant = messages?.[1]
  assert.equal(assistant?.role, 'assistant')
  const delegations = assistant?.delegations ?? []
  assert.deepEqual(delegations.map((item) => [item.runId, item.status]), [
    ['child-1', 'done'],
    ['child-2', 'failed'],
  ])
  assert.equal(delegations[0].delegationId, 'run:child-1')
  assert.equal(delegations[0].agentTitle, '草稿')
})

test('replayed empty delegations do not clobber persisted agent_process delegations', async () => {
  const runId = 'run-301'
  const stored = row(301, runId)
  stored.agent_process = JSON.stringify({
    delegations: [{
      delegationId: 'run:child-9',
      runId: 'child-9',
      agentName: 'persisted-agent',
      agentTitle: null,
      objective: '持久化的委派',
      status: 'running',
      required: true,
      priority: 0,
    }],
  })
  const withoutChildren = snapshot(runId, 'running', [
    canonical(runId, 1, { payload: { eventType: 'fixture.event' } }),
  ])

  const messages = await hydrateBookConversations([stored], {
    getRunSnapshot: async () => ({ success: true, data: withoutChildren }),
  })

  const delegations = messages?.[1]?.delegations ?? []
  assert.equal(delegations.length, 1)
  assert.equal(delegations[0].runId, 'child-9')
  assert.equal(delegations[0].objective, '持久化的委派')
})
