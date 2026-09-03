import type { AgentConversationMessage } from './contracts.ts'
import type { CanonicalOperation } from './canonicalOutput.ts'

export function planningOperationLabel(operation: CanonicalOperation): string | null {
  if (operation.kind !== 'planning') return null
  if (operation.display.labelKey !== 'agent.operation.planning') return null
  const revision = operation.display.labelParams.revision
  if (typeof revision === 'number' && revision > 0) return '调整计划'
  return '制定计划'
}

const INTERNAL_PROGRESS_TOKEN = /(?:\b(?:run|task|longtask|spaturn|turn|operation|invocation|output|artifact|revision|project|session|scene)[_-][a-z0-9-]+\b|\b(?:run|task|turn|operation|artifact|revision|project|session|scene)?ids?\b|\b[a-z]+(?:[A-Z][A-Za-z0-9]*)+\b|\b(?=[a-z0-9_-]{8,}\b)(?=[a-z0-9_-]*\d)[a-z0-9_-]+\b|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b)/i
const PROTOCOL_PROGRESS_CONTENT = /(?:```|[{}\[\]]|https?:\/\/|file:\/\/|\/Users\/|Traceback|stack trace)/i

export function publicAgentProgressNarration(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim() : ''
  const text = /^\*\*[^*]+\*\*$/.test(raw)
    ? raw.slice(2, -2).trim()
    : raw
  if (
    !text
    || text.length > 280
    || text.includes('\n')
    || /^(?:[#>-]|\d+[.)])\s/.test(text)
    || INTERNAL_PROGRESS_TOKEN.test(text)
    || PROTOCOL_PROGRESS_CONTENT.test(text)
  ) {
    return ''
  }
  return text
}

export function getAgentProcessingLabel(
  message: AgentConversationMessage,
): string {
  const canonical = message.canonicalOutput
  if (canonical) {
    const activeOperation = [...canonical.operationOrder]
      .reverse()
      .map((operationId) => canonical.operations[operationId])
      .find((operation) => operation?.status === 'running')
    const planningLabel = activeOperation && planningOperationLabel(activeOperation)
    if (planningLabel && activeOperation) {
      const progress = [...canonical.planningProgress]
        .reverse()
        .find((item) => item.operationId === activeOperation.operationId)
      const title = publicAgentProgressNarration(progress?.text)
      if (title) return title
      return `正在${planningLabel}`
    }
    const invocationId = activeOperation?.invocationId
    if (invocationId) {
      const providerProgress = [...canonical.agentProgress]
        .reverse()
        .find((item) => item.invocationId === invocationId)
      const providerTitle = publicAgentProgressNarration(providerProgress?.text)
      if (providerTitle) return providerTitle
      const modelTitle = [...canonical.commentaryBlocks]
        .reverse()
        .find((block) => !block.aborted && block.invocationId === invocationId)
      const title = publicAgentProgressNarration(modelTitle?.text)
      if (title) return title
    }
    const latestProgress = publicAgentProgressNarration(
      canonical.agentProgress.at(-1)?.text,
    )
    if (latestProgress) return latestProgress
  }
  return '正在思考'
}
