import type { AgentConversationMessage } from '../../../../agent-runtime/contracts.ts'

export function getErrorNoticeMessage(
  message: Pick<AgentConversationMessage, 'content' | 'error'>,
): string {
  return message.error?.trim()
    || message.content.trim()
    || '本轮执行失败'
}

export function hasRenderableErrorMessage(
  message: Pick<AgentConversationMessage, 'content' | 'error' | 'isError'>,
): boolean {
  return message.isError === true || Boolean(message.error?.trim())
}
