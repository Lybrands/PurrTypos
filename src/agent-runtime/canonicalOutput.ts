export type CanonicalOutputSource = 'provider' | 'runtime' | 'tool' | 'domain'
export type CanonicalOutputChannel =
  | 'commentary'
  | 'final'
  | 'operation'
  | 'lifecycle'
  | 'error'
  | 'diagnostic'
  | 'delegation'
export type CanonicalOutputVisibility = 'public' | 'private' | 'diagnostic'

export type CanonicalOutputEvent = {
  eventId: string
  outputStreamId: string | null
  runId: string
  turnId: string | null
  invocationId: string | null
  sequence: number
  source: CanonicalOutputSource
  kind: string
  channel: CanonicalOutputChannel
  visibility: CanonicalOutputVisibility
  payload: Record<string, unknown>
  occurredAt: string
  emittedAt: string
}

export type CanonicalOperationStatus =
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'

export type CanonicalOperation = {
  operationId: string
  runId: string
  invocationId: string | null
  kind: string
  firstSequence: number
  status: CanonicalOperationStatus
  startedAt: string
  finishedAt?: string
  durationMs?: number
  errorCode?: string
  display: {
    labelKey?: string
    labelParams: Record<string, unknown>
    resourceRef?: string
  }
  toolCallId?: string
  toolName?: string
}

export type CanonicalCommentaryBlock = {
  outputStreamId: string
  invocationId?: string | null
  text: string
  firstSequence: number
  lastSequence: number
  startedAt: string
  committed: boolean
  aborted: boolean
}

export type CanonicalPlanningProgress = {
  eventId: string
  outputStreamId: string
  invocationId: string
  operationId: string
  revision: number
  attempt: number
  recordIndex: number
  text: string
  sequence: number
  occurredAt: string
}

export type CanonicalDelegation = {
  delegationId: string
  firstSequence: number
  runId: string
  agentName: string
  agentTitle: string | null
  objective: string
  status: string
  errorCode: string | null
  output: CanonicalOutputState
}

export type CanonicalApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'timed_out'
  | 'canceled'
  | 'unavailable'

export type CanonicalApproval = {
  approvalId: string
  toolName: string
  title: string
  riskLevel: 'write' | 'destructive'
  summary: string
  status: CanonicalApprovalStatus
}

export type CanonicalOutputState = {
  lastSequence: number
  lastSequenceByRun: Record<string, number>
  seenEventIds: Record<string, true>
  finalText: string
  commentaryText: string
  commentaryBlocks: CanonicalCommentaryBlock[]
  planningProgress: CanonicalPlanningProgress[]
  operations: Record<string, CanonicalOperation>
  operationOrder: string[]
  delegations: Record<string, CanonicalDelegation>
  delegationOrder: string[]
  approvals: Record<string, CanonicalApproval>
  approvalOrder: string[]
  runId: string | null
  runStatus: string | null
  runTerminal: boolean
  finalStreamStatus: 'idle' | 'open' | 'committed' | 'aborted'
  finalStreamId: string | null
  finalStreamErrorCode: string | null
  latestRuntimeEvent: {
    eventType: string
    data: Record<string, unknown>
    sequence: number
  } | null
}

export function initialCanonicalOutputState(
  afterSequence = 0,
): CanonicalOutputState {
  return {
    lastSequence: afterSequence,
    lastSequenceByRun: {},
    seenEventIds: {},
    finalText: '',
    commentaryText: '',
    commentaryBlocks: [],
    planningProgress: [],
    operations: {},
    operationOrder: [],
    delegations: {},
    delegationOrder: [],
    approvals: {},
    approvalOrder: [],
    runId: null,
    runStatus: null,
    runTerminal: false,
    finalStreamStatus: 'idle',
    finalStreamId: null,
    finalStreamErrorCode: null,
    latestRuntimeEvent: null,
  }
}

export function isCanonicalOutputEvent(
  value: unknown,
): value is CanonicalOutputEvent {
  if (!isRecord(value)) return false
  return typeof value.eventId === 'string'
    && typeof value.runId === 'string'
    && Number.isSafeInteger(value.sequence)
    && Number(value.sequence) > 0
    && typeof value.source === 'string'
    && typeof value.kind === 'string'
    && typeof value.channel === 'string'
    && typeof value.visibility === 'string'
    && isRecord(value.payload)
    && typeof value.occurredAt === 'string'
    && typeof value.emittedAt === 'string'
}

export function reduceCanonicalOutput(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  if (state.seenEventIds[event.eventId]) return state
  const runSequence = state.lastSequenceByRun[event.runId] ?? 0
  if (event.sequence <= runSequence) return state
  const presentationSequence = Math.max(state.lastSequence + 1, event.sequence)
  const lateRootEvent = Boolean(
    state.runTerminal && state.runId === event.runId,
  )

  let next: CanonicalOutputState = {
    ...state,
    lastSequence: presentationSequence,
    lastSequenceByRun: {
      ...state.lastSequenceByRun,
      [event.runId]: event.sequence,
    },
    seenEventIds: {
      ...state.seenEventIds,
      [event.eventId]: true,
    },
    runId: state.runId ?? event.runId,
  }
  // Display ordering spans related Runs; authoritative deduplication above
  // remains based on each original Run sequence, not this presentation index.
  event = { ...event, sequence: presentationSequence }
  if (lateRootEvent) return next
  if (event.visibility !== 'public') return next

  if (
    event.source === 'provider'
    && (
      event.kind === 'provider.content_delta'
      || event.kind === 'provider.delta_batch'
    )
  ) {
    const delta = canonicalProviderTextDelta(event)
    if (!delta) return next
    if (event.channel === 'final') {
      return {
        ...next,
        finalText: next.finalText + delta,
        finalStreamStatus: 'open',
        finalStreamId: event.outputStreamId,
      }
    }
    if (event.channel === 'commentary' && event.outputStreamId) {
      return appendCommentary(next, event, delta)
    }
    return next
  }

  if (event.kind === 'planning.progress') {
    return appendPlanningProgress(next, event)
  }

  if (event.kind === 'stream.committed' || event.kind === 'stream.aborted') {
    next = settleStream(next, event)
  }
  if (event.kind === 'operation.started') {
    next = startOperation(next, event)
  } else if (event.kind === 'operation.finished') {
    next = finishOperation(next, event)
  } else if (event.kind === 'tool.event') {
    next = applyToolEvent(next, event)
  } else if (event.kind === 'run.lifecycle') {
    next = applyRunLifecycle(next, event)
  } else if (event.kind === 'runtime.event') {
    next = applyRuntimeEvent(next, event)
  } else if (event.kind === 'delegation.event') {
    next = applyDelegationEvent(next, event)
  }
  return next
}

function appendPlanningProgress(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  if (
    event.source !== 'provider'
    || event.channel !== 'commentary'
    || event.payload.schemaVersion !== 'purra.planning-stream/v1'
  ) return state
  const text = stringValue(event.payload.text)
  const outputStreamId = event.outputStreamId ?? ''
  const invocationId = event.invocationId ?? ''
  const operationId = stringValue(event.payload.operationId)
  const revision = numberValue(event.payload.revision)
  const attempt = numberValue(event.payload.attempt)
  const recordIndex = numberValue(event.payload.recordIndex)
  if (
    !text
    || !outputStreamId
    || !invocationId
    || !operationId
    || revision == null
    || attempt == null
    || recordIndex == null
    || !Number.isSafeInteger(revision)
    || !Number.isSafeInteger(attempt)
    || !Number.isSafeInteger(recordIndex)
    || revision < 0
    || attempt < 0
    || recordIndex < 1
  ) return state
  return {
    ...state,
    planningProgress: [
      ...state.planningProgress,
      {
        eventId: event.eventId,
        outputStreamId,
        invocationId,
        operationId,
        revision,
        attempt,
        recordIndex,
        text,
        sequence: event.sequence,
        occurredAt: event.occurredAt,
      },
    ],
  }
}

export function canonicalProviderTextDelta(event: CanonicalOutputEvent): string {
  if (event.kind === 'provider.content_delta') {
    return stringValue(event.payload.delta)
  }
  if (event.kind !== 'provider.delta_batch') return ''
  const entries = event.payload.entries
  if (!Array.isArray(entries)) return ''
  return entries
    .filter((entry): entry is Record<string, unknown> => isRecord(entry))
    .filter((entry) => entry.kind === 'provider.content_delta')
    .map((entry) => isRecord(entry.payload)
      ? stringValue(entry.payload.delta)
      : '')
    .join('')
}

export function replayCanonicalOutput(
  events: readonly CanonicalOutputEvent[],
  afterSequence = 0,
): CanonicalOutputState {
  return events.reduce(
    reduceCanonicalOutput,
    initialCanonicalOutputState(afterSequence),
  )
}

function appendCommentary(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
  delta: string,
): CanonicalOutputState {
  const streamId = event.outputStreamId as string
  const index = state.commentaryBlocks.findIndex(
    (block) => block.outputStreamId === streamId,
  )
  const commentaryBlocks = [...state.commentaryBlocks]
  if (index < 0) {
    commentaryBlocks.push({
      outputStreamId: streamId,
      invocationId: event.invocationId,
      text: delta,
      firstSequence: event.sequence,
      lastSequence: event.sequence,
      startedAt: event.occurredAt,
      committed: false,
      aborted: false,
    })
  } else {
    const current = commentaryBlocks[index]
    commentaryBlocks[index] = {
      ...current,
      invocationId: current.invocationId ?? event.invocationId,
      text: current.text + delta,
      lastSequence: event.sequence,
    }
  }
  return {
    ...state,
    commentaryText: state.commentaryText + delta,
    commentaryBlocks,
  }
}

function settleStream(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const committed = event.kind === 'stream.committed'
  if (event.channel === 'final') {
    return {
      ...state,
      finalStreamStatus: committed ? 'committed' : 'aborted',
      finalStreamId: event.outputStreamId,
      finalStreamErrorCode: committed
        ? null
        : stringValue(event.payload.errorCode) || null,
    }
  }
  if (event.channel !== 'commentary' || !event.outputStreamId) return state
  return {
    ...state,
    commentaryBlocks: state.commentaryBlocks.map((block) =>
      block.outputStreamId === event.outputStreamId
        ? {
            ...block,
            committed,
            aborted: !committed,
            lastSequence: event.sequence,
          }
        : block,
    ),
  }
}

function startOperation(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const operationId = stringValue(event.payload.operationId)
  if (!operationId) return state
  const display = normalizeDisplay(event.payload.display)
  const current = state.operations[operationId]
  const operation: CanonicalOperation = {
    operationId,
    runId: event.runId,
    invocationId: event.invocationId,
    kind: stringValue(event.payload.kind) || current?.kind || 'unknown',
    firstSequence: current?.firstSequence ?? event.sequence,
    status: 'running',
    startedAt: stringValue(event.payload.startedAt) || event.occurredAt,
    display,
    toolCallId: current?.toolCallId,
    toolName: current?.toolName,
  }
  return {
    ...state,
    operations: { ...state.operations, [operationId]: operation },
    operationOrder: current
      ? state.operationOrder
      : [...state.operationOrder, operationId],
  }
}

function finishOperation(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const operationId = stringValue(event.payload.operationId)
  if (!operationId) return state
  const current = state.operations[operationId]
  const duration = numberValue(event.payload.durationMs)
  const status = operationStatus(event.payload.status)
  const operation: CanonicalOperation = {
    operationId,
    runId: current?.runId ?? event.runId,
    invocationId: current?.invocationId ?? event.invocationId,
    kind: current?.kind ?? 'unknown',
    firstSequence: current?.firstSequence ?? event.sequence,
    status,
    startedAt: current?.startedAt ?? event.occurredAt,
    finishedAt: stringValue(event.payload.finishedAt) || event.occurredAt,
    ...(duration == null ? {} : { durationMs: duration }),
    ...(stringValue(event.payload.errorCode)
      ? { errorCode: stringValue(event.payload.errorCode) }
      : {}),
    display: mergeDisplay(
      current?.display,
      normalizeDisplay(event.payload.display),
    ),
    toolCallId: current?.toolCallId,
    toolName: current?.toolName,
  }
  return {
    ...state,
    operations: { ...state.operations, [operationId]: operation },
    operationOrder: current
      ? state.operationOrder
      : [...state.operationOrder, operationId],
  }
}

function applyToolEvent(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const operationId = stringValue(event.payload.operationId)
  const current = state.operations[operationId]
  if (!operationId || !current) return state
  return {
    ...state,
    operations: {
      ...state.operations,
      [operationId]: {
        ...current,
        toolCallId: stringValue(event.payload.toolCallId) || current.toolCallId,
        toolName: stringValue(event.payload.toolName) || current.toolName,
      },
    },
  }
}

function applyRunLifecycle(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const status = stringValue(event.payload.status) || null
  const terminal = status != null && status !== 'running' && status !== 'pending'
  const finalResponse = terminal
    ? stringValue(event.payload.final_response ?? event.payload.finalResponse)
    : ''
  return {
    ...state,
    runId: event.runId,
    runStatus: status,
    runTerminal: terminal,
    finalText: finalResponse || state.finalText,
    finalStreamStatus: finalResponse ? 'committed' : state.finalStreamStatus,
  }
}

function applyRuntimeEvent(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const eventType = stringValue(event.payload.eventType)
  const data = isRecord(event.payload.data) ? event.payload.data : {}
  if (!eventType) return state
  let next: CanonicalOutputState = {
    ...state,
    latestRuntimeEvent: { eventType, data, sequence: event.sequence },
  }
  if (eventType === 'approval.requested') {
    next = applyApprovalRequested(next, data)
  } else if (eventType === 'approval.resolved') {
    next = applyApprovalResolved(next, data)
  }
  return next
}

function applyApprovalRequested(
  state: CanonicalOutputState,
  data: Record<string, unknown>,
): CanonicalOutputState {
  const approvalId = stringValue(data.approvalId)
  const toolName = stringValue(data.toolName)
  const title = stringValue(data.title)
  const summary = stringValue(data.summary)
  const riskLevel = data.riskLevel === 'destructive' ? 'destructive' : 'write'
  if (!approvalId || !toolName || !title || !summary) return state
  const current = state.approvals[approvalId]
  return {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        approvalId,
        toolName,
        title,
        riskLevel,
        summary,
        status: current?.status ?? 'pending',
      },
    },
    approvalOrder: current
      ? state.approvalOrder
      : [...state.approvalOrder, approvalId],
  }
}

function applyApprovalResolved(
  state: CanonicalOutputState,
  data: Record<string, unknown>,
): CanonicalOutputState {
  const approvalId = stringValue(data.approvalId)
  const current = state.approvals[approvalId]
  if (!approvalId || !current) return state
  return {
    ...state,
    approvals: {
      ...state.approvals,
      [approvalId]: {
        ...current,
        status: approvalStatus(data.status),
      },
    },
  }
}

function applyDelegationEvent(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState {
  const eventType = stringValue(event.payload.eventType)
  const delegationId = stringValue(event.payload.delegationId)
  const agentName = stringValue(event.payload.agentName)
  if (!delegationId || !agentName) return state
  const current = state.delegations[delegationId]
  if (eventType !== 'status') return state
  const delegation: CanonicalDelegation = {
    delegationId,
    firstSequence: current?.firstSequence ?? event.sequence,
    runId: stringValue(event.payload.runId) || event.runId,
    agentName,
    agentTitle: stringValue(event.payload.agentTitle) || null,
    objective: stringValue(event.payload.objective),
    status: stringValue(event.payload.status) || current?.status || 'queued',
    errorCode: stringValue(event.payload.errorCode) || null,
    output: current?.output ?? initialCanonicalOutputState(),
  }
  return {
    ...state,
    delegations: { ...state.delegations, [delegationId]: delegation },
    delegationOrder: current
      ? state.delegationOrder
      : [...state.delegationOrder, delegationId],
  }
}

function normalizeDisplay(value: unknown): CanonicalOperation['display'] {
  if (!isRecord(value)) return { labelParams: {} }
  return {
    ...(stringValue(value.labelKey) ? { labelKey: stringValue(value.labelKey) } : {}),
    labelParams: isRecord(value.labelParams) ? value.labelParams : {},
    ...(stringValue(value.resourceRef)
      ? { resourceRef: stringValue(value.resourceRef) }
      : {}),
  }
}

function mergeDisplay(
  current: CanonicalOperation['display'] | undefined,
  next: CanonicalOperation['display'],
): CanonicalOperation['display'] {
  return {
    ...(current?.labelKey ? { labelKey: current.labelKey } : {}),
    ...(next.labelKey ? { labelKey: next.labelKey } : {}),
    labelParams: { ...(current?.labelParams ?? {}), ...next.labelParams },
    ...(current?.resourceRef ? { resourceRef: current.resourceRef } : {}),
    ...(next.resourceRef ? { resourceRef: next.resourceRef } : {}),
  }
}

function operationStatus(value: unknown): CanonicalOperationStatus {
  const status = stringValue(value)
  return status === 'succeeded'
    || status === 'failed'
    || status === 'canceled'
    ? status
    : 'failed'
}

function approvalStatus(value: unknown): CanonicalApprovalStatus {
  const status = stringValue(value)
  return status === 'approved'
    || status === 'rejected'
    || status === 'timed_out'
    || status === 'canceled'
    || status === 'unavailable'
    ? status
    : 'pending'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown): number | null {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}
