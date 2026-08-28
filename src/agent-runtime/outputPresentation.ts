import type { AgentConversationMessage } from './contracts.ts'

const INTERNAL_PROGRESS_TOKEN = /(?:\b(?:run|task|longtask|spaturn|turn|operation|invocation|output|artifact|revision|project|session|scene)[_-][a-z0-9-]+\b|\b(?:run|task|turn|operation|artifact|revision|project|session|scene)?ids?\b|\b[a-z]+(?:[A-Z][A-Za-z0-9]*)+\b|\b(?=[a-z0-9_-]{8,}\b)(?=[a-z0-9_-]*\d)[a-z0-9_-]+\b|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b)/i
const PROTOCOL_PROGRESS_CONTENT = /(?:```|[{}\[\]]|https?:\/\/|file:\/\/|\/Users\/|Traceback|stack trace)/i

export function publicAgentProgressNarration(value: unknown): string {
  const raw = typeof value === 'string' ? value.trim() : ''
  const text = /^\*\*[^*]+\*\*$/.test(raw)
    ? raw.slice(2, -2).trim()
    : raw
  if (
    !text
    || text.length > 60
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
    const invocationId = activeOperation?.invocationId
    if (invocationId) {
      const modelTitle = [...canonical.commentaryBlocks]
        .reverse()
        .find((block) => !block.aborted && block.invocationId === invocationId)
      const title = publicAgentProgressNarration(modelTitle?.text)
      if (title) return title
    }
  }
  return '正在思考'
}
